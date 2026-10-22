"""FamPay gateway: order created via FamPay API, payment verified with the bot's existing UTR checker, wallet credited once."""
import asyncio
import base64
import json
import re
import ssl
import uuid
import aiohttp
import certifi
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from config import ADMIN_IDS, SUPPORT_LINK
from db import users, txns, utcnow, get_settings
import payments
from notify import get_bot
from utils import B, KB, esc, fmt_curr, normalize_support, SEP

router = Router()
cfg_col = users.database["fampay_cfg"]
orders = users.database["fampay_orders"]

MIN_FP = 5
MAX_FP = 100000
# False = users only get the "Pay Now" button (hosted page). The QR image is NOT sent,
# because it contains your UPI ID. Set True only if you want to show the QR too.
SHOW_QR = False

ORDER_KEYS = ("order_id", "orderId", "txn_id", "txnId", "id")
LINK_KEYS = ("payment_url", "paymentUrl", "checkout_url", "checkoutUrl", "payment_link", "paymentLink", "link", "url")
QR_KEYS = ("qr_data", "qr_image_url", "qr", "qr_url", "qrUrl", "qr_image", "qr_code", "qrCode")


class FP(StatesGroup):
    amount = State()
    utr = State()


class FA(StatesGroup):
    create_url = State()
    api_key = State()
    header = State()


# ------------------------------- settings -------------------------------
async def get_cfg():
    d = await cfg_col.find_one({"_id": "cfg"}) or {}
    return {"active": bool(d.get("active")), "create_url": d.get("create_url", ""),
            "api_key": d.get("api_key", ""), "header": d.get("header") or "x-api-key",
            "log_chat": d.get("log_chat")}


async def set_cfg(**kw):
    await cfg_col.update_one({"_id": "cfg"}, {"$set": kw}, upsert=True)


async def send_log(text):
    """Post a log line to the log group (set with /setlog in the group). Silent if not set."""
    c = await get_cfg()
    if not c.get("log_chat"):
        return
    try:
        await get_bot().send_message(c["log_chat"], text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        print(f"[fampay] log error: {e!r}")


def _who(u):
    un = f" @{esc(u.username)}" if u.username else ""
    return f"{esc(u.full_name or '-')}{un} (<code>{u.id}</code>)"


async def utr_checker_ready():
    s = await get_settings()
    return bool(s.get("UTR_API_MAIL") and s.get("UTR_API_PASS"))


async def fp_ready():
    c = await get_cfg()
    return bool(c["active"] and c["create_url"] and c["api_key"] and await utr_checker_ready())


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


def _short(x):
    if isinstance(x, dict):
        return {k: _short(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_short(v) for v in x]
    if isinstance(x, str) and len(x) > 90:
        return x[:60] + "…(" + str(len(x)) + " chars)"
    return x


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


# ------------------------------- crediting -------------------------------
async def credit(o, utr, res):
    """Payment found by the UTR checker -> credit once. -> ('ok', balance) | ('claimed', None) | ('error', None)"""
    got = await orders.find_one_and_update({"_id": o["_id"], "status": "pending"}, {"$set": {"status": "paid"}})
    if not got:
        return "claimed", None
    found_utr = payments.clean_id(res.get("utr"))
    found_txn = payments.clean_id(res.get("transaction_id"))
    ids = [i for i in dict.fromkeys([utr, found_utr, found_txn]) if i]
    if await payments.id_in_use([found_utr, found_txn]) or not await payments.claim_ids(ids, {"user_id": o["user_id"], "source": "fampay"}):
        await orders.update_one({"_id": o["_id"]}, {"$set": {"status": "pending"}})
        return "claimed", None
    amt = float(o["amount"])
    try:
        await txns.insert_one({"user_id": o["user_id"], "username": o.get("username"), "full_name": o.get("full_name"),
                               "amount": amt, "method": "fampay", "utr": found_utr or utr, "transaction_id": found_txn,
                               "status": "approved", "created_at": utcnow()})
        await users.update_one({"_id": o["user_id"]}, {"$inc": {"balance": amt}})
    except Exception as e:
        print(f"[fampay] credit error: {e!r}")
        await orders.update_one({"_id": o["_id"]}, {"$set": {"status": "pending"}})
        await payments.used_utrs.delete_many({"_id": {"$in": ids}})
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
        if link and str(link).startswith("upi://"):
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
    if not await fp_ready():
        await state.clear()
        return await msg.answer("⚠️ FamPay is currently unavailable.")
    c = await get_cfg()
    wait = await msg.answer("⏳ <i>Creating your payment…</i>", parse_mode="HTML")
    oid = uuid.uuid4().hex[:16]
    st, d = await _req("POST", c["create_url"], c,
                       json={"amount": amt, "purpose": f"TG{msg.from_user.id}-{oid}", "expiry_minutes": 30})
    link = find(d, LINK_KEYS) if d is not None else None
    qr = find(d, QR_KEYS) if d is not None else None
    if not (200 <= st < 300) or not (link or qr):
        print(f"[fampay] create failed: http={st} body={str(_short(d))[:300]}")
        await state.clear()
        return await wait.edit_text("❌ <b>Could not create the payment.</b>\nPlease try again later.", parse_mode="HTML",
                                    reply_markup=KB([B("Back", "recharge")]))
    pid = str(find(d, ORDER_KEYS) or oid)
    try:
        pay = float(find(d, ("payable_amount", "payableAmount")) or amt)
    except Exception:
        pay = amt
    await orders.insert_one({"_id": oid, "user_id": msg.from_user.id, "username": msg.from_user.username,
                             "full_name": msg.from_user.full_name, "amount": amt, "payable": pay,
                             "provider_id": pid, "status": "pending", "created_at": utcnow()})
    await state.clear()
    await send_log(f"🆕 <b>FamPay order created</b>\n{SEP}\n<b>User:</b> {_who(msg.from_user)}\n"
                   f"<b>Wallet:</b> {fmt_curr(amt)} · <b>To pay:</b> {fmt_curr(pay)}\n<b>Order:</b> <code>{esc(pid)}</code>")
    note = f" (you will receive ₹{amt:g} in your wallet)" if abs(pay - amt) > 0.001 else ""
    has_link = bool(link and str(link).startswith("http"))
    use_qr = SHOW_QR or not has_link
    how = "the QR or the <b>Pay Now</b> button" if use_qr and has_link else ("the QR" if use_qr else "the <b>Pay Now</b> button")
    cap = (f"💳 <b>Pay ₹{pay:g}</b>\n{SEP}\n"
           f"<i>Pay with {how} (valid for 30 min), then tap <b>I've Paid</b> and send your UTR.</i>\n"
           f"⚠️ Pay <b>exactly ₹{pay:g}</b>{note}.")
    rows = []
    if link and str(link).startswith("http"):
        rows.append([B("🔗 Pay Now", url=str(link))])
    rows += [[B("✅ I've Paid", f"fp:paid:{oid}")], [B("Cancel", "recharge")]]
    kb = KB(*rows)
    try:
        await wait.delete()
    except Exception:
        pass
    photo = _photo(qr, link) if use_qr else None
    try:
        if not photo:
            raise ValueError("no photo")
        await msg.answer_photo(photo, caption=cap, parse_mode="HTML", reply_markup=kb)
    except Exception:
        extra = f"\n<code>{esc(str(link))}</code>" if link and not str(link).startswith("http") else ""
        await msg.answer(cap + extra, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("fp:paid:"))
async def fp_paid(cq: CallbackQuery, state: FSMContext):
    oid = cq.data.split(":", 2)[2]
    o = await orders.find_one({"_id": oid, "user_id": cq.from_user.id})
    if not o:
        return await cq.answer("Order not found. Start the recharge again.", show_alert=True)
    if o["status"] == "paid":
        return await cq.answer("✅ This payment was already credited.", show_alert=True)
    await cq.answer()
    await state.set_state(FP.utr)
    await state.update_data(oid=oid)
    await _replace(cq, "🧾 <b>Send your 12-digit UTR</b> (or the FMP… transaction ID) of the payment you just made.",
                   KB([B("Cancel", "recharge")]))


def _not_found_kb(oid):
    rows = [[B("🔄 Try Again", f"fp:paid:{oid}")]]
    sup = normalize_support(SUPPORT_LINK)
    if sup:
        rows.append([B("Contact Support", url=sup)])
    rows.append([B("Back", "recharge")])
    return KB(*rows)


@router.message(StateFilter(FP.utr), F.text, ~F.text.startswith("/"))
async def fp_utr(msg: Message, state: FSMContext):
    data = await state.get_data()
    oid = data.get("oid")
    o = await orders.find_one({"_id": oid, "user_id": msg.from_user.id}) if oid else None
    if not o or o["status"] != "pending":
        await state.clear()
        return await msg.answer("⌛ Session expired. Start the recharge again.")
    utr = payments.clean_id(msg.text)
    if not (re.fullmatch(r"\d{12}", utr) or re.fullmatch(r"FMP[A-Z0-9]{6,40}", utr)):
        return await msg.answer("❌ Invalid. Send the 12-digit UTR (digits only) or the FMP… transaction ID.")
    if await payments.id_in_use([utr]):
        await send_log(f"🚫 <b>FamPay duplicate UTR attempt</b>\n{SEP}\n<b>User:</b> {_who(msg.from_user)}\n"
                       f"<b>UTR:</b> <code>{esc(utr)}</code>")
        return await msg.answer("❌ <b>This UTR was already used.</b>", parse_mode="HTML",
                                reply_markup=KB([B("Back", "recharge")]))
    wait = await msg.answer("🔎 <i>Verifying your payment… this can take up to a minute.</i>", parse_mode="HTML")
    status, res = "notfound", None
    for i in range(payments.VERIFY_ATTEMPTS):
        status, res = await payments.utr_lookup(utr, o.get("payable") or o["amount"])
        if status == "found":
            break
        if i < payments.VERIFY_ATTEMPTS - 1:
            await asyncio.sleep(payments.VERIFY_GAP)
    if status == "found":
        r, bal = await credit(o, utr, res)
        if r == "ok":
            await state.clear()
            await send_log(f"✅ <b>FamPay payment credited</b>\n{SEP}\n<b>User:</b> {_who(msg.from_user)}\n"
                           f"<b>Added:</b> {fmt_curr(o['amount'])} · <b>Balance:</b> {fmt_curr(bal)}\n"
                           f"<b>UTR:</b> <code>{esc(utr)}</code>\n<b>Order:</b> <code>{esc(str(o['provider_id']))}</code>")
            return await wait.edit_text(
                f"✅ <b>Payment received!</b>\n{SEP}\n<b>Added:</b> {fmt_curr(o['amount'])}\n<b>New balance:</b> {fmt_curr(bal)}",
                parse_mode="HTML", reply_markup=KB([B("🏠 Home", "back_main")]))
        if r == "claimed":
            await send_log(f"🚫 <b>FamPay payment already claimed</b>\n{SEP}\n<b>User:</b> {_who(msg.from_user)}\n"
                           f"<b>UTR:</b> <code>{esc(utr)}</code>")
            return await wait.edit_text("❌ <b>This payment was already credited.</b>", parse_mode="HTML",
                                        reply_markup=KB([B("Back", "recharge")]))
        return await wait.edit_text("⚠️ <b>Something went wrong while crediting.</b> Please contact support.",
                                    parse_mode="HTML", reply_markup=_not_found_kb(oid))
    await send_log(f"⚠️ <b>FamPay UTR not found</b>\n{SEP}\n<b>User:</b> {_who(msg.from_user)}\n"
                   f"<b>UTR:</b> <code>{esc(utr)}</code> · <b>Amount:</b> {fmt_curr(o.get('payable') or o['amount'])}")
    await wait.edit_text("❌ <b>Payment not found yet.</b>\n<i>Bank updates can take a minute. Check the UTR and amount, then try again.</i>",
                         parse_mode="HTML", reply_markup=_not_found_kb(oid))


# ------------------------------- ADMIN PANEL (/fampay) -------------------------------
PROMPTS = {
    "create_url": "🔗 Send the full FamPay <b>create-order URL</b>\n<i>e.g. https://fampay-flame.vercel.app/api/v1/fampay/create</i>",
    "api_key": "🔑 Send your FamPay <b>live API key</b>.\n<i>Your message will be deleted automatically.</i>",
    "header": "🏷 Send the <b>header name</b> used for the API key.\n<i>Default: x-api-key</i>",
}


async def panel_view():
    c = await get_cfg()
    utr_ok = await utr_checker_ready()
    text = (f"🟣 <b>FamPay Gateway</b>\n{SEP}\n"
            f"<b>Status:</b> {'🟢 ON' if c['active'] else '🔴 OFF'}\n"
            f"• Create URL: <code>{esc(c['create_url'] or '-')}</code>\n"
            f"• API key: {'✅ set' if c['api_key'] else '❌ not set'}\n"
            f"• Auth header: <code>{esc(c['header'])}</code>\n"
            f"• UTR checker: {'✅ ready' if utr_ok else '❌ set UTR Mail + UTR App Pass in /admin → Payments'}\n"
            f"• Log group: {'✅ set' if c.get('log_chat') else '❌ not set (add the bot to your group, then send /setlog there)'}")
    kb = KB([B(f"FamPay: {'ON' if c['active'] else 'OFF'}", "fpa:toggle")],
            [B("Create URL", "fpa:create_url"), B("API Key", "fpa:api_key")],
            [B("Auth Header", "fpa:header"), B("🧪 Test (₹1 order)", "fpa:test")],
            [B("📋 Test Log", "fpa:logtest"), B("🗑 Log Off", "fpa:logoff")],
            [B("Close", "fpa:close")])
    return text, kb


@router.message(Command("setlog"))
async def setlog_cmd(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    if msg.chat.type not in ("group", "supergroup"):
        return await msg.answer("ℹ️ Add me to your log group, then send /setlog <b>inside that group</b>.", parse_mode="HTML")
    await set_cfg(log_chat=msg.chat.id)
    await msg.answer("✅ This group is now the <b>log group</b>.", parse_mode="HTML")


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
        if not c["active"]:
            if not (c["create_url"] and c["api_key"]):
                return await cq.answer("Set Create URL and API key first.", show_alert=True)
            if not await utr_checker_ready():
                return await cq.answer("Set UTR Mail and UTR App Pass in /admin → Payments first.", show_alert=True)
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
        st, d = await _req("POST", c["create_url"], c, json={"amount": 1, "purpose": "bot-test", "expiry_minutes": 5})
        raw = json.dumps(_short(d), ensure_ascii=False)[:1500] if d is not None else "no response"
        link, qr, pid = find(d, LINK_KEYS), find(d, QR_KEYS), find(d, ORDER_KEYS)
        return await cq.message.answer(
            f"🧪 <b>Test result</b> (HTTP {st})\n<pre>{esc(raw)}</pre>\n"
            f"<b>Detected</b> → link: {'✅' if link else '❌'} · qr: {'✅' if qr else '❌'} · order id: {'✅' if pid else '❌'}",
            parse_mode="HTML")
    if act == "logtest":
        if not (await get_cfg()).get("log_chat"):
            return await cq.answer("Log group not set. Add the bot to your group and send /setlog there.", show_alert=True)
        await send_log("🧪 <b>Log test</b>\nThe log group is working ✅")
        return await cq.answer("Test sent")
    if act == "logoff":
        await set_cfg(log_chat=None)
        await cq.answer("Log group removed")
        text, kb = await panel_view()
        try:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
        return
    if act in PROMPTS:
        await cq.answer()
        await state.set_state(getattr(FA, act))
        return await cq.message.answer(PROMPTS[act], parse_mode="HTML")
    await cq.answer()


@router.message(StateFilter(FA.create_url, FA.api_key, FA.header), F.text, ~F.text.startswith("/"))
async def admin_input(msg: Message, state: FSMContext):
    if msg.from_user.id not in ADMIN_IDS:
        return
    field = (await state.get_state()).split(":")[1]
    val = msg.text.strip()
    if field == "create_url" and not val.startswith("http"):
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