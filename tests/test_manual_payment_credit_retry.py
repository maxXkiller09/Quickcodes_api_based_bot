"""Fake-database tests for retry-safe manual payment approval."""
import asyncio
import os
from bson import ObjectId

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import payments


def run(coro):
    return asyncio.run(coro)


class FakeTxns:
    def __init__(self, txn):
        self.txn = dict(txn)

    async def find_one_and_update(self, query, update):
        if self.txn["_id"] == query["_id"] and self.txn["status"] == query["status"]:
            self.txn.update(update["$set"])
            return dict(self.txn)
        return None

    async def find_one(self, query):
        if self.txn["_id"] == query["_id"] and self.txn["status"] == query["status"]:
            return dict(self.txn)
        return None

    async def update_one(self, query, update):
        if self.txn["_id"] == query["_id"] and self.txn["status"] == query["status"]:
            self.txn.update(update["$set"])
        return None


class FakeClaims:
    def __init__(self):
        self.claims = {}

    async def find_one(self, query):
        claim = self.claims.get(query["_id"])
        if claim and "txn" in query and claim.get("txn") != query["txn"]:
            return None
        return claim


def setup(monkeypatch, status="pending", utr=None, credit_results=("applied",)):
    oid = ObjectId()
    txn = {"_id": oid, "user_id": 7, "amount": 12.5, "status": status, "utr": utr}
    txns, claims = FakeTxns(txn), FakeClaims()
    results = iter(credit_results)
    async def claim_ids(ids, meta):
        if any(i in claims.claims for i in ids):
            return False
        for item in ids:
            claims.claims[item] = {"_id": item, **meta}
        return True
    async def credit_once(uid, amount, key):
        return next(results)
    monkeypatch.setattr(payments, "txns", txns)
    monkeypatch.setattr(payments, "used_utrs", claims)
    monkeypatch.setattr(payments, "claim_ids", claim_ids)
    monkeypatch.setattr(payments, "credit_once", credit_once)
    return oid, txns, claims


def test_manual_approval_credits_with_stable_idempotency_key(monkeypatch):
    keys = []
    oid, txns, _ = setup(monkeypatch)
    async def credit(uid, amount, key):
        keys.append((uid, amount, key))
        return "applied"
    monkeypatch.setattr(payments, "credit_once", credit)
    status, _ = run(payments.approve_txn(str(oid), 1))
    assert status == "ok"
    assert txns.txn["status"] == "approved"
    assert keys == [(7, 12.5, f"manual-payment:{oid}")]


def test_retry_recovers_when_credit_was_already_applied(monkeypatch):
    oid, txns, _ = setup(monkeypatch, status="credit_pending", credit_results=("already_applied",))
    status, _ = run(payments.approve_txn(str(oid), 1))
    assert status == "ok"
    assert txns.txn["status"] == "approved"


def test_unconfirmed_credit_keeps_transaction_retryable(monkeypatch):
    oid, txns, _ = setup(monkeypatch, credit_results=("failed",))
    status, _ = run(payments.approve_txn(str(oid), 1))
    assert status == "credit_failed"
    assert txns.txn["status"] == "credit_pending"


def test_duplicate_utr_does_not_credit_wallet(monkeypatch):
    oid, txns, claims = setup(monkeypatch, utr="123456789012")
    claims.claims["123456789012"] = {"_id": "123456789012", "txn": "another"}
    async def must_not_credit(*args):
        raise AssertionError("duplicate UTR must not credit wallet")
    monkeypatch.setattr(payments, "credit_once", must_not_credit)
    status, _ = run(payments.approve_txn(str(oid), 1))
    assert status == "dup_utr"
    assert txns.txn["status"] == "declined"
