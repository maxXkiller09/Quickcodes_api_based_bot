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




"""/admin panel: services ON/OFF, profit %, API keys, live balances, payment settings, manual credit/debit."""
import re
from aiogram import Router, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from config import ADMIN_IDS, BOT_TITLE
from db import get_settings, set_setting, users, orders, txns, tasks, smm_orders, _client, utcnow
from flows import admin_credit, admin_debit
from qc import qc
from utils import B, KB, esc, fmt_curr, edit, SEP

router = Router()

IS_ADMIN = F.from_user.id.in_(ADMIN_IDS)


async def health_snapshot():
    """Return a safe owner health summary without exposing credentials or calling providers."""
    lines = ["🩺 <b>NEXORA · HEALTH</b>", SEP]
    try:
        await _client.admin.command("ping")
        lines.append("• MongoDB: 🟢 reachable")
    except Exception:
        lines.append("• MongoDB: 🔴 unreachable")

    try:
        settings = await get_settings()
        api_keys = settings.get("api_keys") or {}
        for key, label in (("s2", "Server 2 API key"), ("s3", "Server 3 API key"), ("smm", "SMM API key")):
            configured = bool(str(api_keys.get(key) or "").strip())
            lines.append(f"• {label}: {'🟢 configured' if configured else '🟡 missing'} (not live-tested)")
        utr_ready = bool(settings.get("UTR_API_MAIL") and settings.get("UTR_API_PASS"))
        lines.append(f"• UTR verification credentials: {'🟢 configured' if utr_ready else '🟡 missing'}")
        manual_ready = bool(settings.get("MANUAL_UPI_ID") or settings.get("MANUAL_QR_FILE_ID"))
        lines.append(f"• Manual UPI details: {'🟢 configured' if manual_ready else '🟡 missing'}")
    except Exception:
        lines.append("• Settings read: 🔴 failed")

    lines.append("")
    lines.append("<i>Configuration checks only; provider APIs and live payments are not called.</i>")
    return "\n".join(lines)

SERVICES = [("tg", "Telegram Accounts"), ("wa", "WhatsApp Accounts"), ("otp", "All Apps OTP"), ("smm", "SMM Panel")]
APIS = [("s2", "Server 2", "Telegram accounts"), ("s3", "Server 3", "OTP + WhatsApp"), ("smm", "SMM Panel", "social media")]
PAY_FIELDS = {
    "AUTO_UPI_ID": ("Auto UPI ID", "Send the UPI ID that receives Auto UPI payments (e.g. name@bank)."),
    "UTR_API_URL": ("UTR API URL", "Send the UTR verification API URL (https://…)."),
    "UTR_API_MAIL": ("UTR API Mail", "Send the e-mail used by the UTR verification API."),
    "UTR_API_PASS": ("UTR App Password", "Send the app password for that e-mail."),
    "MANUAL_UPI_ID": ("Manual UPI ID", "Send the UPI ID for manual payments (e.g. name@bank)."),
    "MANUAL_QR_FILE_ID": ("Manual QR", "Send the QR code as a PHOTO."),
}
SECRET = {"UTR_API_PASS"}


class Adm(StatesGroup):
    key = State()
    profit = State()
    value = State()
    credit = State()
    debit = State()


def mask(v, keep=6):
    v = v or ""
    return "❌ not set" if not v else (v[:keep] + "…" + v[-4:] if len(v) > keep + 6 else "••••")


async def _del(msg):
    try:
        await msg.delete()
    except Exception:
        pass


# ============================== home ==============================
async def home(target):
    text = (
        f"🛠 <b>NEXORA · ADMIN</b>\n{SEP}\n"
        f"<i>{esc(BOT_TITLE)} control centre</i>\n\n"
        "Choose a section below. Changes apply to the live bot.\n"
        "🔌 Services · pricing · API connections · wallet payments"
    )
    kb = KB([B("🔌 Services", "adm:svc"), B("📊 Pricing", "adm:profit")],
            [B("🔑 API Connections", "adm:api"), B("💰 API Balances", "adm:bal")],
            [B("👥 User Management", "adm:users:0")],
            [B("💳 Wallet Payments", "adm:pay")])
    await edit(target, text, kb)


@router.message(Command("health"), IS_ADMIN)
async def health_cmd(msg: Message):
    await msg.answer(await health_snapshot(), parse_mode="HTML")


@router.message(Command("admin"), IS_ADMIN)
async def admin_cmd(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        f"🛠 <b>NEXORA · ADMIN</b>\n{SEP}\n<i>{esc(BOT_TITLE)} control centre</i>\n\n"
        "Choose a section below. Changes apply to the live bot.\n"
        "🔌 Services · pricing · API connections · wallet payments",
        parse_mode="HTML",
        reply_markup=KB([B("🔌 Services", "adm:svc"), B("📊 Pricing", "adm:profit")],
                        [B("🔑 API Connections", "adm:api"), B("💰 API Balances", "adm:bal")],
                        [B("👥 User Management", "adm:users:0")],
                        [B("💳 Wallet Payments", "adm:pay")]))


@router.callback_query(F.data == "adm:home", IS_ADMIN)
async def home_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.answer()
    await home(cq)


# ============================== services ==============================
async def render_services(cq):
    s = await get_settings()
    rows = [[B(f"{'✅' if s['services'].get(k, True) else '❌'} {n}", f"adm:tog:{k}")] for k, n in SERVICES]
    rows.append([B("🏠 Admin Home", "adm:home")])
    await edit(cq, f"🛎 <b>Services</b>\n{SEP}\n<i>Tap to switch a service ON / OFF for your users.</i>", KB(*rows))


@router.callback_query(F.data == "adm:svc", IS_ADMIN)
async def svc(cq: CallbackQuery):
    await cq.answer()
    await render_services(cq)


@router.callback_query(F.data.startswith("adm:tog:"), IS_ADMIN)
async def svc_toggle(cq: CallbackQuery):
    key = cq.data.split(":")[2]
    if key not in dict(SERVICES):
        return await cq.answer()
    s = await get_settings()
    new = not s["services"].get(key, True)
    await set_setting(**{f"services.{key}": new})
    await cq.answer(f"{dict(SERVICES)[key]}: {'ON' if new else 'OFF'}")
    await render_services(cq)


# ============================== profit % ==============================
async def render_profit(cq):
    s = await get_settings()
    lines = [
        "• <b>Telegram Accounts</b> (cheapest provider rate): <b>0%</b> — fixed",
        *[f"• <b>{n}</b> ({d}): <b>{float(s['profit'].get(k, 0)):g}%</b>" for k, n, d in APIS if k != "s2"],
    ]
    rows = [[B(f"✏️ {n} profit %", f"adm:pset:{k}")] for k, n, d in APIS if k != "s2"]
    rows.append([B("⬅️ Back", "adm:home")])
    await edit(cq, f"📈 <b>Profit %</b>\n{SEP}\n" + "\n".join(lines) +
               "\n\n<i>Telegram Accounts use the current Quick Codes API price with no added markup. Other services add their configured percentage.</i>", KB(*rows))


@router.callback_query(F.data == "adm:profit", IS_ADMIN)
async def profit(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.answer()
    await render_profit(cq)


@router.callback_query(F.data.startswith("adm:pset:"), IS_ADMIN)
async def profit_prompt(cq: CallbackQuery, state: FSMContext):
    k = cq.data.split(":")[2]
    if k not in {a[0] for a in APIS if a[0] != "s2"}:
        return await cq.answer()
    await cq.answer()
    await state.set_state(Adm.profit)
    await state.update_data(srv=k)
    n = next(a[1] for a in APIS if a[0] == k)
    await edit(cq, f"✏️ <b>{n} profit %</b>\nSend a number from 0 to 1000 (example: <code>15</code> or <code>12.5</code>).",
               KB([B("⬅️ Back", "adm:profit")]))


@router.message(StateFilter(Adm.profit), IS_ADMIN, F.text, ~F.text.startswith("/"))
async def profit_save(msg: Message, state: FSMContext):
    try:
        v = float(msg.text.strip().replace("%", ""))
        assert 0 <= v <= 1000
    except Exception:
        return await msg.answer("❌ Send a number between 0 and 1000.")
    k = (await state.get_data()).get("srv")
    await state.clear()
    if k not in {"s3", "smm"}:
        return await msg.answer("❌ Telegram Accounts use the cheapest provider rate with 0% markup.")
    await set_setting(**{f"profit.{k}": round(v, 2)})
    await msg.answer(f"✅ Profit for <b>{next(a[1] for a in APIS if a[0] == k)}</b> set to <b>{v:g}%</b>.", parse_mode="HTML",
                     reply_markup=KB([B("📈 Profit %", "adm:profit")]))


# ============================== API keys ==============================
async def render_api(cq):
    s = await get_settings()
    lines = [f"• <b>{n}</b> ({d})\n   <code>{esc(mask(s['api_keys'].get(k)))}</code>" for k, n, d in APIS]
    rows = [[B(f"✏️ Change {n} key", f"adm:kset:{k}")] for k, n, d in APIS]
    rows.append([B("⬅️ Back", "adm:home")])
    await edit(cq, f"🔑 <b>Quick Codes API Keys</b>\n{SEP}\n" + "\n".join(lines) +
               "\n\n<i>Get the keys in the Quick Codes bot → 🔑 API Keys.</i>", KB(*rows))


@router.callback_query(F.data == "adm:api", IS_ADMIN)
async def api(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.answer()
    await render_api(cq)


@router.callback_query(F.data.startswith("adm:kset:"), IS_ADMIN)
async def key_prompt(cq: CallbackQuery, state: FSMContext):
    k = cq.data.split(":")[2]
    if k not in {a[0] for a in APIS}:
        return await cq.answer()
    await cq.answer()
    await state.set_state(Adm.key)
    await state.update_data(srv=k)
    n = next(a[1] for a in APIS if a[0] == k)
    await edit(cq, f"✏️ <b>Send your {n} API key</b>\nIt starts with <code>qc_{k}_</code>.\n"
                   "<i>Your message will be deleted right after for safety.</i>", KB([B("⬅️ API Connections", "adm:api")]))


@router.message(StateFilter(Adm.key), IS_ADMIN, F.text, ~F.text.startswith("/"))
async def key_save(msg: Message, state: FSMContext):
    k = (await state.get_data()).get("srv")
    key = msg.text.strip()
    await _del(msg)
    name = next(a[1] for a in APIS if a[0] == k)
    if not key.startswith(f"qc_{k}_"):
        return await msg.answer(f"❌ That is not a {name} key. It must start with <code>qc_{k}_</code>. Send it again.", parse_mode="HTML")
    st, d = await qc(k, "GET", "/v1/balance", key=key)
    if st in (401, 403):
        return await msg.answer(f"❌ <b>Key rejected:</b> {esc(d.get('message'))}\nSend a valid key.", parse_mode="HTML")
    await state.clear()
    await set_setting(**{f"api_keys.{k}": key})
    note = (f"💰 Quick Codes wallet: <b>{fmt_curr(d.get('balance'))}</b>" if d.get("ok")
            else f"⚠️ Saved, but the API could not be checked right now ({esc(d.get('message'))}).")
    await msg.answer(f"✅ <b>{name} key saved.</b>\n{note}", parse_mode="HTML", reply_markup=KB([B("🔑 API Keys", "adm:api")]))


# ============================== live balances ==============================
@router.callback_query(F.data == "adm:bal", IS_ADMIN)
async def balances(cq: CallbackQuery):
    await cq.answer("Checking…")
    lines = []
    for k, n, d in APIS:
        st, r = await qc(k, "GET", "/v1/balance")
        if r.get("ok"):
            lines.append(f"✅ <b>{n}</b> — Quick Codes wallet: <b>{fmt_curr(r.get('balance'))}</b>")
        elif r.get("error") == "no_key":
            lines.append(f"➖ <b>{n}</b> — key not set")
        else:
            lines.append(f"❌ <b>{n}</b> — {esc(r.get('message') or r.get('error'))}")
    await edit(cq, f"💰 <b>Live API Balances</b>\n{SEP}\n" + "\n".join(lines) +
               "\n\n<i>This is your Quick Codes wallet. Recharge it in the Quick Codes bot when it runs low.</i>",
               KB([B("🔄 Refresh", "adm:bal")], [B("⬅️ Back", "adm:home")]))


# ============================== payments ==============================
async def render_pay(cq):
    s = await get_settings()
    qr = "✅ set" if s["MANUAL_QR_FILE_ID"] else "❌ not set"
    text = (f"💳 <b>Payments</b>\n{SEP}\n"
            f"<b>Auto UPI:</b> {'🟢 ON' if s['auto_upi_active'] else '🔴 OFF'}\n"
            f"• UPI ID: <code>{esc(s['AUTO_UPI_ID'] or '-')}</code>\n"
            f"• API URL: <code>{esc(s['UTR_API_URL'] or '-')}</code>\n"
            f"• Mail: <code>{esc(s['UTR_API_MAIL'] or '-')}</code>\n"
            f"• App password: {'✅ set' if s['UTR_API_PASS'] else '❌ not set'}\n\n"
            f"<b>Manual UPI:</b> {'🟢 ON' if s['manual_upi_active'] else '🔴 OFF'}\n"
            f"• UPI ID: <code>{esc(s['MANUAL_UPI_ID'] or '-')}</code>\n• QR: {qr}")
    kb = KB([B(f"Auto UPI: {'ON' if s['auto_upi_active'] else 'OFF'}", "adm:ptog:auto_upi_active"),
             B(f"Manual UPI: {'ON' if s['manual_upi_active'] else 'OFF'}", "adm:ptog:manual_upi_active")],
            [B("Auto UPI ID", "adm:fset:AUTO_UPI_ID"), B("UTR API URL", "adm:fset:UTR_API_URL")],
            [B("UTR Mail", "adm:fset:UTR_API_MAIL"), B("UTR App Pass", "adm:fset:UTR_API_PASS")],
            [B("Manual UPI ID", "adm:fset:MANUAL_UPI_ID"), B("Manual QR", "adm:fset:MANUAL_QR_FILE_ID")],
            [B("⬅️ Back", "adm:home")])
    await edit(cq, text, kb)


@router.callback_query(F.data == "adm:pay", IS_ADMIN)
async def pay(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.answer()
    await render_pay(cq)


@router.callback_query(F.data.startswith("adm:ptog:"), IS_ADMIN)
async def pay_toggle(cq: CallbackQuery):
    k = cq.data.split(":")[2]
    if k not in ("auto_upi_active", "manual_upi_active"):
        return await cq.answer()
    s = await get_settings()
    await set_setting(**{k: not s[k]})
    await cq.answer("Updated")
    await render_pay(cq)


@router.callback_query(F.data.startswith("adm:fset:"), IS_ADMIN)
async def field_prompt(cq: CallbackQuery, state: FSMContext):
    f = cq.data.split(":")[2]
    if f not in PAY_FIELDS:
        return await cq.answer()
    await cq.answer()
    await state.set_state(Adm.value)
    await state.update_data(field=f)
    await edit(cq, f"✏️ <b>{PAY_FIELDS[f][0]}</b>\n{PAY_FIELDS[f][1]}", KB([B("⬅️ Wallet Payments", "adm:pay")]))


@router.message(StateFilter(Adm.value), IS_ADMIN, F.photo)
async def field_photo(msg: Message, state: FSMContext):
    f = (await state.get_data()).get("field")
    if f != "MANUAL_QR_FILE_ID":
        return await msg.answer("❌ Send text for this field.")
    await state.clear()
    await set_setting(MANUAL_QR_FILE_ID=msg.photo[-1].file_id)
    await msg.answer("✅ Manual QR saved.", reply_markup=KB([B("💳 Payments", "adm:pay")]))


@router.message(StateFilter(Adm.value), IS_ADMIN, F.text, ~F.text.startswith("/"))
async def field_text(msg: Message, state: FSMContext):
    f = (await state.get_data()).get("field")
    v = msg.text.strip()
    if f in SECRET:
        await _del(msg)
    if f == "MANUAL_QR_FILE_ID":
        return await msg.answer("📸 Please send the QR as a photo.")
    if f in ("AUTO_UPI_ID", "MANUAL_UPI_ID", "UTR_API_MAIL") and "@" not in v:
        return await msg.answer("❌ That does not look right (missing @). Send it again.")
    if f == "UTR_API_URL" and not v.startswith(("http://", "https://")):
        return await msg.answer("❌ The URL must start with http:// or https://")
    await state.clear()
    await set_setting(**{f: v})
    await msg.answer(f"✅ <b>{PAY_FIELDS[f][0]}</b> saved.", parse_mode="HTML", reply_markup=KB([B("💳 Payments", "adm:pay")]))


# ============================== manual credit / debit ==============================
def _parse(text):
    m = re.match(r"^\s*(\d{1,15})[\s,]+(\d+(?:\.\d+)?)\s*$", text or "")
    return (int(m.group(1)), float(m.group(2))) if m else None


async def _apply(msg, text, credit):
    p = _parse(text)
    if not p or p[1] <= 0:
        return await msg.answer("❌ Format: <code>user_id amount</code>  (example: <code>123456789 50</code>)", parse_mode="HTML")
    uid, amt = p
    u = await users.find_one({"_id": uid})
    if not u:
        return await msg.answer("❌ User not found (they must have started the bot).")
    before = float(u.get("balance", 0) or 0)
    ok = await (admin_credit if credit else admin_debit)(uid, amt)
    if not ok:
        return await msg.answer("❌ Balance update failed. Please retry.")
    updated = await users.find_one({"_id": uid}) or {}
    nb = float(updated.get("balance", 0) or 0)
    actual = round(max(0, nb - before) if credit else max(0, before - nb), 2)
    if actual > 0:
        await orders.insert_one({"user_id": uid, "type": "wallet_adjustment",
                                 "action": "credit" if credit else "debit",
                                 "amount": actual, "admin_id": msg.from_user.id, "date": utcnow()})
    await msg.answer(f"✅ {'Credited' if credit else 'Debited'} {fmt_curr(actual)} {'to' if credit else 'from'} <code>{uid}</code>\n"
                     f"New balance: <b>{fmt_curr(nb)}</b>", parse_mode="HTML")


@router.message(Command("credit"), IS_ADMIN)
async def credit_cmd(msg: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    if command.args:
        return await _apply(msg, command.args, True)
    await state.set_state(Adm.credit)
    await msg.answer("➕ <b>Add balance</b>\nSend: <code>user_id amount</code>", parse_mode="HTML")


@router.message(Command("debit"), IS_ADMIN)
async def debit_cmd(msg: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    if command.args:
        return await _apply(msg, command.args, False)
    await state.set_state(Adm.debit)
    await msg.answer("➖ <b>Deduct balance</b>\nSend: <code>user_id amount</code>", parse_mode="HTML")


@router.message(StateFilter(Adm.credit), IS_ADMIN, F.text, ~F.text.startswith("/"))
async def credit_state(msg: Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("target_uid")
    await state.clear()
    await _apply(msg, f"{uid} {msg.text.strip()}" if uid is not None else msg.text, True)


@router.message(StateFilter(Adm.debit), IS_ADMIN, F.text, ~F.text.startswith("/"))
async def debit_state(msg: Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("target_uid")
    await state.clear()
    await _apply(msg, f"{uid} {msg.text.strip()}" if uid is not None else msg.text, False)


# ============================== user management ==============================
USER_PAGE_SIZE = 8


def _user_label(user):
    name = esc(str(user.get("name") or "Unknown")[:40])
    username = user.get("username")
    return f"{name} (@{esc(username)})" if username else name


async def _render_users(target, page=0):
    total = await users.count_documents({})
    pages = max(1, (total + USER_PAGE_SIZE - 1) // USER_PAGE_SIZE)
    page = max(0, min(int(page), pages - 1))
    cursor = users.find({}, {"_id": 1, "name": 1, "username": 1, "balance": 1, "joined": 1})
    cursor = cursor.sort([("joined", -1), ("_id", -1)]).skip(page * USER_PAGE_SIZE).limit(USER_PAGE_SIZE)
    rows = await cursor.to_list(length=USER_PAGE_SIZE)
    lines = [f"👥 <b>NEXORA USERS</b> · {total:,} total", SEP,
             f"<i>Page {page + 1}/{pages} · newest first</i>", ""]
    buttons = []
    for user in rows:
        uid = int(user["_id"])
        lines.append(f"• {_user_label(user)}\n  🆔 <code>{uid}</code> · 💰 <b>{fmt_curr(user.get('balance', 0))}</b>")
        buttons.append([B(f"👤 {str(user.get('name') or uid)[:22]} · {uid}", f"adm:user:{uid}")])
    if not rows:
        lines.append("No users found yet.")
    nav = []
    if page > 0:
        nav.append(B("⬅️ Previous", f"adm:users:{page - 1}"))
    if page < pages - 1:
        nav.append(B("Next ➡️", f"adm:users:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([B("🔄 Refresh", f"adm:users:{page}"), B("⬅️ Admin Home", "adm:home")])
    await edit(target, "\n".join(lines), KB(*buttons))


@router.message(Command("users"), IS_ADMIN)
async def users_cmd(msg: Message, state: FSMContext):
    await state.clear()
    total = await users.count_documents({})
    await msg.answer(f"👥 <b>Total registered users:</b> {total:,}", parse_mode="HTML",
                     reply_markup=KB([B("📋 Browse Users", "adm:users:0")], [B("🛠 Admin Panel", "adm:home")]))


@router.callback_query(F.data.startswith("adm:users:"), IS_ADMIN)
async def users_page(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.answer()
    await _render_users(cq, int(cq.data.split(":")[2]))


@router.callback_query(F.data.startswith("adm:user:"), IS_ADMIN)
async def user_detail(cq: CallbackQuery):
    uid = int(cq.data.split(":")[2])
    user = await users.find_one({"_id": uid})
    if not user:
        return await cq.answer("User not found.", show_alert=True)

    deposits = await txns.find({"user_id": uid, "status": "approved"}).to_list(length=5000)
    deposit_total = sum(float(item.get("amount") or 0) for item in deposits)
    adjustments = await orders.find({"user_id": uid, "type": "wallet_adjustment"}).to_list(length=5000)
    manual_added = sum(float(x.get("amount") or 0) for x in adjustments if x.get("action") == "credit")
    manual_removed = sum(float(x.get("amount") or 0) for x in adjustments if x.get("action") == "debit")
    tg_orders = await orders.find({"user_id": uid, "type": "tg"}).to_list(length=5000)
    s3_orders = await tasks.find({"user_id": uid}).to_list(length=5000)
    smm_rows = await smm_orders.find({"user_id": uid}).to_list(length=5000)
    tg_total = sum(float(x.get("price") or 0) for x in tg_orders)
    s3_total = sum(float(x.get("price") or 0) for x in s3_orders)
    smm_total = sum(float(x.get("charge") or x.get("price") or 0) for x in smm_rows)
    joined = user.get("joined")
    joined_text = joined.strftime("%d %b %Y") if hasattr(joined, "strftime") else "Unknown"
    text = (
        f"👤 <b>USER DETAILS</b>\n{SEP}\n"
        f"<b>Name:</b> {_user_label(user)}\n"
        f"<b>User ID:</b> <code>{uid}</code>\n"
        f"<b>Joined:</b> {joined_text}\n"
        f"<b>Current balance:</b> <b>{fmt_curr(user.get('balance', 0))}</b>\n\n"
        f"💳 <b>Confirmed deposits:</b> {fmt_curr(deposit_total)}\n"
        f"➕ <b>Admin credits:</b> {fmt_curr(manual_added)}\n"
        f"➖ <b>Admin deductions:</b> {fmt_curr(manual_removed)}\n\n"
        f"🛍 <b>Telegram account purchases:</b> {len(tg_orders)} · {fmt_curr(tg_total)}\n"
        f"📲 <b>WhatsApp/OTP orders:</b> {len(s3_orders)} · {fmt_curr(s3_total)}\n"
        f"📈 <b>SMM orders:</b> {len(smm_rows)} · {fmt_curr(smm_total)}\n\n"
        f"<i>Use /credit {uid} AMOUNT to add funds or /debit {uid} AMOUNT to deduct funds. "
        "Order totals reflect records currently stored in this bot database.</i>"
    )
    await cq.answer()
    await edit(cq, text, KB([B("➕ Add Balance", f"adm:usercredit:{uid}"),
                             B("➖ Deduct Balance", f"adm:userdebit:{uid}")],
                            [B("📋 Back to Users", "adm:users:0"), B("🛠 Admin Home", "adm:home")]))


@router.callback_query(F.data.startswith("adm:usercredit:"), IS_ADMIN)
async def user_credit_prompt(cq: CallbackQuery, state: FSMContext):
    uid = int(cq.data.split(":")[2])
    await state.clear()
    await state.set_state(Adm.credit)
    await state.update_data(target_uid=uid)
    await cq.answer()
    await edit(cq, f"➕ <b>Add balance to user</b> <code>{uid}</code>\nSend the amount in INR (e.g. <code>100</code>).",
               KB([B("⬅️ Cancel", f"adm:user:{uid}")]))


@router.callback_query(F.data.startswith("adm:userdebit:"), IS_ADMIN)
async def user_debit_prompt(cq: CallbackQuery, state: FSMContext):
    uid = int(cq.data.split(":")[2])
    await state.clear()
    await state.set_state(Adm.debit)
    await state.update_data(target_uid=uid)
    await cq.answer()
    await edit(cq, f"➖ <b>Deduct balance from user</b> <code>{uid}</code>\nSend the amount in INR (e.g. <code>50</code>).",
               KB([B("⬅️ Cancel", f"adm:user:{uid}")]))


# ============================== force-join channels ==============================
@router.message(Command("setforcechannel"), IS_ADMIN)
async def set_force_channel_cmd(msg: Message, command: CommandObject):
    """Set or remove either force-join channel without editing hosting variables."""
    args = (command.args or "").split()
    if not args or args[0].lower() in {"help", "?"}:
        return await msg.answer(
            "📢 <b>Force-join channel setup</b>\n\n"
            "Set public channel: <code>/setforcechannel 1 @channelusername</code>\n"
            "Or include link: <code>/setforcechannel 1 @channelusername https://t.me/channelusername</code>\n"
            "Private channel: <code>/setforcechannel 2 -1001234567890 https://t.me/+invitecode</code>\n\n"
            "Remove one: <code>/setforcechannel off 1</code>\n"
            "Remove both: <code>/setforcechannel off all</code>\n"
            "<i>Use slot 1 or 2. The bot must be an administrator in each required channel.</i>",
            parse_mode="HTML",
        )

    if args[0].lower() in {"off", "disable", "remove"}:
        if len(args) != 2 or args[1].lower() not in {"1", "2", "all"}:
            return await msg.answer("❌ Use <code>/setforcechannel off 1</code>, <code>off 2</code>, or <code>off all</code>.", parse_mode="HTML")
        slots = ("1", "2") if args[1].lower() == "all" else (args[1],)
        updates = {}
        for slot in slots:
            updates[f"force_join.channel_{slot}"] = ""
            updates[f"force_join.link_{slot}"] = ""
        await set_setting(**updates)
        return await msg.answer(f"✅ Force-join requirement removed for slot(s): {', '.join(slots)}. Changes apply within a few seconds.")

    if args[0] not in {"1", "2"} or len(args) not in {2, 3}:
        return await msg.answer("❌ Use <code>/setforcechannel 1 @channelusername [https://t.me/channelusername]</code>.", parse_mode="HTML")

    slot, channel = args[0], args[1]
    if not (channel.startswith("@") or re.fullmatch(r"-?\d{5,20}", channel)):
        return await msg.answer("❌ Channel must be a public @username or numeric chat ID (private channel usually starts with -100).", parse_mode="HTML")

    link = args[2] if len(args) == 3 else (f"https://t.me/{channel[1:]}" if channel.startswith("@") else "")
    if not link.startswith(("https://t.me/", "https://telegram.me/")):
        return await msg.answer("❌ Provide a valid https://t.me/ invite link. Private channels require their invite link.", parse_mode="HTML")

    await set_setting(**{
        f"force_join.channel_{slot}": channel,
        f"force_join.link_{slot}": link,
    })
    await msg.answer(
        f"✅ <b>Force-join Channel {slot} saved.</b>\n"
        f"Channel: <code>{esc(channel)}</code>\nJoin link: {esc(link)}\n\n"
        "⚠️ Make sure the bot is an administrator in that channel so Telegram membership checks work. "
        "This setting is stored in the bot database; no Render environment-variable edit is needed.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
