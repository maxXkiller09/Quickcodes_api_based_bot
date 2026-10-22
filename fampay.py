"""FamPay gateway: configured from the bot (/fampay), wallet is credited automatically once FamPay confirms payment."""
import asyncio
import base64
import json
import ssl
import uuid
import aiohttp
import certifi
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from config import ADMIN_IDS
from db import users, txns, utcnow
import payments
from utils import B, KB, esc, fmt_curr, SEP

router = Router()
cfg_col = users.database["fampay_cfg"]
orders = users.database["fampay_orders"]

MIN_FP = 5
MAX_FP = 100000

ORDER_KEYS = ("order_id", "orderId", "txn_id", "txnId", "transaction_id", "id")
LINK_KEYS = ("payment_url", "paymentUrl", "checkout_url", "checkoutUrl", "payment_link", "paymentLink",
             "link", "url", "upi_link", "upi_url", "intent_url")
QR_KEYS = ("qr", "qr_url", "qrUrl", "qr_image", "qrImage", "qr_code", "qrCode")
STATUS_KEYS = ("status", "payment_status", "paymentStatus", "state")
PAID = {"success", "paid", "completed", "complete", "captured", "credited", "successful", "settled"}
FAILED = {"failed", "expired", "cancelled", "canceled", "rejected"}


class FP(StatesGroup):
    amount = State()


class FA(StatesGroup):
    create_url = State()
    status_url = State()
    api_key = State()
    header = State()


# ------------------------------- settings -------------------------------
async def get_cfg():
    d = await cfg_col.find_one({"_id": "cfg"}) or {}
    return {"active": bool(d.get("active")), "create_url": d.get("create_url", ""),
            "status_url": d.get("status_url", ""), "api_key": d.get("api_key", ""),
            "header": d.get("header") or "x-api-key"}


async def set_cfg(**kw):
    await cfg_col.update_one({"_id": "cfg"}, {"$set": kw}, upsert=True)


async def fp_ready():
    c = await get_cfg()
    return bool(c["active"] and c["create_url"] and c["status_url"] and c["api_key"])


# ------------------------------- http helpers -------------------------------
def _headers(c):
    k = c["api_key"]
    if c["header"].lower() == "authorization" and not k.lower().startswith("bearer "):
        k = "Bearer " + k
    return {c["header"]: k, "Content-Type": "application/json"}


async def _req(method, url, c, **kw):
    """-> (http_status, parsed_json_or_dict). (0, None) on network error."""
    try:
        conn = aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=certifi.where()))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), connector=conn) as s:
            async with s.request(method, url, headers=_headers(c), **kw) as r:
                txt = await r.text()
                try:
                    data = json.loads(txt)
                except Exception:
                    data = {"raw": txt[:500]}
                return r.status, data
    except Exception as e:
        print(f"[fampay] request error: {e!r}")
        return 0, None


def find(d, keys):
    """first scalar value for any of `keys`, searching nested dicts/lists"""
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if v not in (None, "", {}, []) and not isinstance(v, (dict, list)):
                return v
        for v in d.values():
            r = find(v, keys)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = find(v, keys)
            if r is not None:
                return r
    return None


async def check_status(c, o):
    """-> 'paid' | 'failed' | 'pending' | 'error'"""
    url, pid = c["status_url"], str(o["provider_id"])
    if "{order_id}" in url:
        st, d = await _req("GET", url.replace("{order_id}", pid), c)
    else:
        st, d = await _req("GET", url, c, params={"order_id": pid})
        if st in (404, 405):
            st, d = await _req("POST", url, c, json={"order_id": pid})
    if not st or d is None:
        return "error"
    s = str(find(d, STATUS_KEYS) or "").lower()
    if s in PAID:
        return "paid"
    if s in FAILED:
        return "failed"
    return "pending"


async def credit(o):
    """-> ('ok', new_balance) | ('done', None) | ('error', None)"""
    got = await orders.find_one_and_update({"_id": o["_id"], "status": "pending"}, {"$set": {"status": "paid"}})
    if not got:
        return "done", None
    cid = f"FPAY-{o['provider_id']}"
    if not await payments.claim_ids([cid], {"user_id": o["user_id"], "source": "fampay"}):
        return "done", None
    amt = float(o["amount"])
    try:
        await txns.insert_one({"user_id": o["user_id"], "username": o.get("username"), "full_name": o.get("full_name"),
                               "amount": amt, "method": "fampay", "utr": None, "transaction_id": cid,
                               "status": "approved", "created_at": utcnow()})
        await users.update_one({"_id": o["user_id"]}, {"$inc": {"balance": amt}})
    except Exception as e:
        print(f"[fampay] credit error: {e!r}")
        await orders.update_one({"_id": o["_id"]}, {"$set": {"status": "pending"}})
        await payments.used_utrs.delete_many({"_id": cid})
        return "error", None
    u = await users.find_one({"_id": o["user_id"]}) or {}
    return "ok", float(u.get("balance", amt))


# ------------------------------- qr helpers -------------------------------
def _qr_file(data):
    import qrcode
    from io import BytesIO
    qr = qrcode.QRCode(box_size=10, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    bio = BytesIO()
    qr.make_image(fill_color="black", back_color="white").convert("RGB").save(bio, "PNG")
    return BufferedInputFile(bio.getvalue(), "pay.png")


def _photo(qr, link):
    try:
        if qr:
            q = str(qr)
            if q.startswith("data:image") and "," in q:
                return BufferedInputFile(base64.b64decode(q.split(",", 1)[1]), "pay.png")
            if q.startswith("http"):
                return q
        if link:
            return _qr_file(str(link))
    except Exception as e:
        print(f"[fampay] qr error: {e!r}")
    return None


def _amount(text):
    try:
        v = float(text.strip().replace("₹", "").replace(",", ""))
    except Exception:
        return None
    return round(v, 2) if v > 0 else None


async def _replace(cq, text, kb):
    try:
        await cq.message.delete()
    except Exception:
        pass
    return await cq.message.answer(text, reply_markup=kb, parse_mode="HTML")


# ------------------------------- USER FLOW -------------------------------
@router.callback_query(F.data == "fp:start")
async def fp_start(cq: CallbackQuery, state: FSMContext):
    if not await fp_ready():
        return await cq.answer("FamPay is currently unavailable.", show_alert=True)
    await cq.answer()
    await state.set_state(FP.amount)
    await _replace(cq, f"💰 <b>Enter the amount to add</b>\n<i>Minimum ₹{MIN_FP} · Maximum ₹{MAX_FP:,}</i>",
                   KB([B("Back", "recharge")]))


@router.message(StateFilter(FP.amount), F.text, ~F.text.startswith("/"))
async def fp_amount(msg: Message, state: FSMContext):
    amt = _amount(msg.text)
    if amt is None or amt < MIN_FP or amt > MAX_FP:
        return await msg.answer(f"❌ Enter a valid amount between ₹{MIN_FP} and ₹{MAX_FP:,}.")
    c = await get_cfg()
    if not await fp_ready():
        await state.clear()
        return await msg.answer("⚠️ FamPay is currently unavailable.")
    wait = await msg.answer("⏳ <i>Creating your payment…</i>", parse_mode="HTML")
    oid = uuid.uuid4().hex[:16]
    st, d = await _req("POST", c["create_url"], c, json={"amount": amt, "order_id": oid})
    link = find(d, LINK_KEYS) if d is not None else None
    qr = find(d, QR_KEYS) if d is not None else None
    if not (200 <= st < 300) or not (link or qr):
        print(f"[fampay] create failed: http={st} body={str(d)[:300]}")
        await state.clear()
        return await wait.edit_text("❌ <b>Could not create the payment.</b>\nPlease try again later.", parse_mode="HTML",
                                    reply_markup=KB([B("Back", "recharge")]))
    pid = str(find(d, ORDER_KEYS) or oid)
    await orders.insert_one({"_id": oid, "user_id": msg.from_user.id, "username": msg.from_user.username,
                             "full_name": msg.from_user.full_name, "amount": amt, "provider_id": pid,
                             "status": "pending", "created_at": utcnow()})
    await state.clear()
    cap = (f"💳 <b>Pay ₹{amt:g}</b>\n{SEP}\n"
           f"<i>Pay with the QR / link below, then tap <b>Check Payment</b>. Your wallet is credited automatically.</i>\n"
           f"⚠️ Pay <b>exactly ₹{amt:g}</b>.")
    rows = []
    if link and str(link).startswith("http"):
        rows.append([B("🔗 Pay Now", url=str(link))])
    rows += [[B("✅ Check Payment", f"fp:chk:{oid}")], [B("Cancel", "recharge")]]
    kb = KB(*rows)
    try:
        await wait.delete()
    except Exception:
        pass
    photo = _photo(qr, link)
    try:
        if photo:
            await msg.answer_photo(photo, caption=cap, parse_mode="HTML", reply_markup=kb)
        else:
            raise ValueError("no photo")
    except Exception:
        extra = f"\n<code>{esc(str(link))}</code>" if link else ""
        await msg.answer(cap + extra, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("fp:chk:"))
async def fp_check(cq: CallbackQuery):
    oid = cq.data.split(":", 2)[2]
    o = await orders.find_one({"_id": oid, "user_id": cq.from_user.id})
    if not o:
        return await cq.answer("Order not found.", show_alert=True)
    if o["status"] == "paid":
        return await cq.answer("✅ This payment was already credited.", show_alert=True)
    if o["status"] == "failed":
        return await cq.answer("❌ This payment failed or expired. Start a new recharge.", show_alert=True)
    c = await get_cfg()
    s = "pending"
    for i in range(2):
        s = await check_status(c, o)
        if s != "pending":
            break
        if i == 0:
            await asyncio.sleep(2)
    if s == "paid":
        r, bal = await credit(o)
        if r == "ok":
            await cq.answer()
            return await _replace(cq, f"✅ <b>Payment received!</b>\n{SEP}\n<b>Added:</b> {fmt_curr(o['amount'])}\n"
                                      f"<b>New balance:</b> {fmt_curr(bal)}", KB([B("🏠 Home", "back_main")]))
        if r == "done":
            return await cq.answer("✅ This payment was already credited.", show_alert=True)
        return await cq.answer("⚠️ Something went wrong while crediting. Contact support.", show_alert=True)
    if s == "failed":
        await orders.update_one({"_id": oid, "status": "pending"}, {"$set": {"status": "failed"}})
        return await cq.answer("❌ Payment failed or expired. Start a new recharge.", show_alert=True)
    if s == "error":
        return await cq.answer("⚠️ Could not reach the gateway. Try again in a moment.", show_alert=True)
    await cq.answer("⏳ Payment not received yet. Pay, wait a few seconds, then tap Check Payment again.", show_alert=True)


# ------------------------------- ADMIN PANEL (/fampay) -------------------------------
PROMPTS = {
    "create_url": "🔗 Send the full FamPay <b>create-order URL</b>\n<i>e.g. https://your-domain/api/v1/fampay/create</i>",
    "status_url": "🔗 Send the <b>payment status URL</b> from the FamPay API docs.\n"
                  "<i>Put {order_id} where the order id goes, e.g. https://your-domain/api/v1/fampay/status?order_id={order_id}</i>",
    "api_key": "🔑 Send your FamPay <b>live API key</b>.\n<i>Your message will be deleted automatically.</i>",
    "header": "🏷 Send the <b>header name</b> used for the API key.\n<i>Default: x-api-key (use Authorization for Bearer keys)</i>",
}


async def panel_view():
    c = await get_cfg()
    text = (f"🟣 <b>FamPay Gateway</b>\n{SEP}\n"
            f"<b>Status:</b> {'🟢 ON' if c['active'] else '🔴 OFF'}\n"
            f"• Create URL: <code>{esc(c['create_url'] or '-')}</code>\n"
            f"• Status URL: <code>{esc(c['status_url'] or '-')}</code>\n"
            f"• API key: {'✅ set' if c['api_key'] else '❌ not set'}\n"
            f"• Auth header: <code>{esc(c['header'])}</code>")
    kb = KB([B(f"FamPay: {'ON' if c['active'] else 'OFF'}", "fpa:toggle")],
            [B("Create URL", "fpa:create_url"), B("Status URL", "fpa:status_url")],
            [B("API Key", "fpa:api_key"), B("Auth Header", "fpa:header")],
            [B("🧪 Test (₹1 order)", "fpa:test")],
            [B("Close", "fpa:close")])
    return text, kb


@router.message(Command("fampay"))
async def fampay_cmd(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    text, kb = await panel_view()
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("fpa:"))
async def admin_cb(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in ADMIN_IDS:
        return await cq.answer("Not allowed.", show_alert=True)
    act = cq.data.split(":", 1)[1]
    if act == "close":
        await cq.answer()
        await state.clear()
        try:
            await cq.message.delete()
        except Exception:
            pass
        return
    if act == "toggle":
        c = await get_cfg()
        if not c["active"] and not (c["create_url"] and c["status_url"] and c["api_key"]):
            return await cq.answer("Set Create URL, Status URL and API key first.", show_alert=True)
        await set_cfg(active=not c["active"])
        await cq.answer("Updated")
        text, kb = await panel_view()
        try:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
        return
    if act == "test":
        c = await get_cfg()
        if not (c["create_url"] and c["api_key"]):
            return await cq.answer("Set Create URL and API key first.", show_alert=True)
        await cq.answer("Testing…")
        st, d = await _req("POST", c["create_url"], c, json={"amount": 1, "order_id": "test" + uuid.uuid4().hex[:8]})
        raw = json.dumps(d, ensure_ascii=False)[:1500] if d is not None else "no response"
        link, qr, pid = find(d, LINK_KEYS), find(d, QR_KEYS), find(d, ORDER_KEYS)
        return await cq.message.answer(
            f"🧪 <b>Test result</b> (HTTP {st})\n<pre>{esc(raw)}</pre>\n"
            f"<b>Detected</b> → link: {'✅' if link else '❌'} · qr: {'✅' if qr else '❌'} · order id: {'✅' if pid else '❌'}",
            parse_mode="HTML")
    if act in PROMPTS:
        await cq.answer()
        await state.set_state(getattr(FA, act))
        return await cq.message.answer(PROMPTS[act], parse_mode="HTML")
    await cq.answer()


@router.message(StateFilter(FA.create_url, FA.status_url, FA.api_key, FA.header), F.text, ~F.text.startswith("/"))
async def admin_input(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    field = (await state.get_state()).split(":")[1]
    val = msg.text.strip()
    if field in ("create_url", "status_url") and not val.startswith("http"):
        return await msg.answer("❌ URL must start with https://")
    await set_cfg(**{field: val})
    if field == "api_key":
        try:
            await msg.delete()
        except Exception:
            pass
    await state.clear()
    text, kb = await panel_view()
    await msg.answer("✅ Saved.\n\n" + text, reply_markup=kb, parse_mode="HTML")