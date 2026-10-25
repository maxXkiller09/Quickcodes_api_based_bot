# ============================================================================
# Copyright © 2026 Valrik Thakur (@valriks)
#
# Created & Developed by: Valrik Thakur
# Brand / Network: VTH NETWORK (@vthchannel)
#
# This source code is proprietary content created by Valrik Thakur.
# Unauthorized removal, modification, replacement, or concealment of the
# original author/brand credits is strictly prohibited.
#
# Redistribution, resale, rebranding, or claiming this work as your own
# without explicit permission from the author is prohibited.
#
# Any authorized use or modification must retain this copyright notice
# and the original author/brand credits.
#
# Created by Valrik Thakur | @valriks
# Powered by VTH NETWORK | @vthchannel
# ============================================================================

"""Recharge logic: Auto UPI (UTR verification API), manual approval, single-use UTR protection."""
import re
import ssl
import certifi
import aiohttp
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from db import users, txns, used_utrs, get_settings, utcnow, credit_once

MIN_AUTO_UPI = 5
MAX_AUTO_UPI = 100000
VERIFY_ATTEMPTS = 3
VERIFY_GAP = 4


def clean_id(v):
    v = re.sub(r"\s+", "", str(v or "")).upper()
    return "" if v in ("NONE", "NULL") else v


async def id_in_use(ids):
    ids = [i for i in ids if i]
    if not ids:
        return False
    if await used_utrs.find_one({"_id": {"$in": ids}}):
        return True
    return await txns.find_one({"status": "approved", "$or": [{"utr": {"$in": ids}}, {"transaction_id": {"$in": ids}}]}) is not None


async def claim_ids(ids, meta):
    """Atomically block UTR / Txn IDs. False (and nothing blocked) if any is already blocked."""
    done = []
    for i in dict.fromkeys(x for x in ids if x):
        try:
            await used_utrs.insert_one({"_id": i, **meta, "at": utcnow()})
            done.append(i)
        except DuplicateKeyError:
            if done:
                await used_utrs.delete_many({"_id": {"$in": done}})
            return False
    return True


async def utr_lookup(utr, amount):
    """-> ('found', json) | ('notfound', json) | ('error', None)"""
    s = await get_settings()
    params = {"mail": s.get("UTR_API_MAIL", ""), "apppass": s.get("UTR_API_PASS", ""), "amount": amount}
    params["txnid" if utr.startswith("FMP") else "utr"] = utr
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))) as sess:
            async with sess.get(s.get("UTR_API_URL") or "https://subdict.qzz.io/check", params=params) as resp:
                res = await resp.json(content_type=None)
    except Exception as e:
        print(f"[UTR API] error: {e!r}")
        return "error", None
    if not isinstance(res, dict):
        return "error", None
    if str(res.get("status", "")).lower() == "found" or str(res.get("result", "")).lower() == "found":
        return "found", res
    return "notfound", res


async def has_retryable_auto_upi(uid, amount, utr):
    """True only when this user has an interrupted credit for this exact UTR."""
    key = clean_id(utr)
    if not key:
        return False
    row = await txns.find_one({
        "method": "auto_upi", "status": "credit_pending", "user_id": uid,
        "amount": round(float(amount), 2),
        "$or": [{"utr": key}, {"transaction_id": key}],
    })
    return row is not None


async def auto_upi_credit(uid, username, full_name, amount, utr, res):
    """Verified Auto-UPI payment with durable, idempotent retry semantics."""
    found_utr = clean_id(res.get("utr")) or clean_id(utr)
    found_txn = clean_id(res.get("transaction_id"))
    ids = list(dict.fromkeys(i for i in [clean_id(utr), found_utr, found_txn] if i))
    amt = round(float(amount), 2)

    # Resume the exact pending transaction before duplicate checks; its UTR claims
    # are intentionally retained after an uncertain write.
    pending = await txns.find_one({
        "method": "auto_upi", "status": "credit_pending", "user_id": uid, "amount": amt,
        "$or": [{"utr": {"$in": ids}}, {"transaction_id": {"$in": ids}}],
    }) if ids else None

    if pending:
        txn = pending
    else:
        if await id_in_use(ids) or not await claim_ids(
            ids, {"user_id": uid, "source": "auto_upi"}
        ):
            return "claimed", None
        try:
            created = await txns.insert_one({
                "user_id": uid, "username": username, "full_name": full_name, "amount": amt,
                "method": "auto_upi", "utr": found_utr, "transaction_id": found_txn or None,
                "status": "credit_pending", "created_at": utcnow(),
            })
            txn = {"_id": created.inserted_id, "user_id": uid, "amount": amt}
        except Exception as e:
            # Do not release UTR claims on an ambiguous insert result.
            print(f"[auto upi] transaction insert uncertain: {e!r}")
            return "error", None

    try:
        credit_status = await credit_once(uid, amt, f"auto-upi:{txn['_id']}")
    except Exception as e:
        print(f"[auto upi] credit status uncertain: {e!r}")
        return "error", None
    if credit_status not in ("applied", "already_applied"):
        return "error", None

    try:
        await txns.update_one(
            {"_id": txn["_id"], "status": "credit_pending"},
            {"$set": {"status": "approved", "credited_at": utcnow()}},
        )
        u = await users.find_one({"_id": uid}) or {}
    except Exception as e:
        # Credit key is durable, so the next retry safely completes the state change.
        print(f"[auto upi] finalization uncertain: {e!r}")
        return "error", None
    return "ok", float(u.get("balance", amt))


async def approve_txn(oid_str, admin_id):
    """Credit an admin-approved payment idempotently.

    A failed/ambiguous wallet write leaves the transaction in credit_pending so
    an admin can retry the same approval without a duplicate wallet credit.
    -> (status, txn): ok | invalid | done | dup_utr | credit_failed
    """
    try:
        oid = ObjectId(oid_str)
    except Exception:
        return "invalid", None

    txn = await txns.find_one_and_update(
        {"_id": oid, "status": "pending"},
        {"$set": {"status": "credit_pending", "approved_by": admin_id}},
    )
    if not txn:
        # Resume an interrupted credit; credit_once uses the same stable key.
        txn = await txns.find_one({"_id": oid, "status": "credit_pending"})
        if not txn:
            return "done", None

    utr = clean_id(txn.get("utr"))
    if utr:
        own_claim = await used_utrs.find_one({"_id": utr, "txn": str(oid)})
        if not own_claim and not await claim_ids(
            [utr], {"user_id": txn["user_id"], "source": "admin_approved", "txn": str(oid)}
        ):
            await txns.update_one(
                {"_id": oid, "status": "credit_pending"},
                {"$set": {"status": "declined", "decline_reason": "duplicate_utr"}},
            )
            return "dup_utr", txn

    credit_status = await credit_once(txn["user_id"], txn["amount"], f"manual-payment:{oid}")
    if credit_status not in ("applied", "already_applied"):
        return "credit_failed", txn

    await txns.update_one(
        {"_id": oid, "status": "credit_pending"},
        {"$set": {"status": "approved", "credited_at": utcnow()}},
    )
    return "ok", txn


async def decline_txn(oid_str, admin_id):
    try:
        oid = ObjectId(oid_str)
    except Exception:
        return None
    return await txns.find_one_and_update({"_id": oid, "status": "pending"},
                                          {"$set": {"status": "declined", "declined_by": admin_id}})
