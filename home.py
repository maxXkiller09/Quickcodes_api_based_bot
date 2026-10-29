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


from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from config import BOT_TITLE, SUPPORT_LINK
from db import users, orders
import stock_alerts
from utils import B, KB, esc, fmt_curr, edit, SEP, normalize_support

router = Router()

@router.message(Command("notificationoff"))
async def notification_off(message: Message):
    service = (message.text or "").partition(" ")[2].strip()
    if not service:
        return await message.answer(
            "🔔 <b>Notification settings</b>\\n"
            "Use: <code>/notificationoff service name</code>\\n"
            "Example: <code>/notificationoff WhatsApp</code>\\n"
            "To mute everything: <code>/notificationoff all</code>"
        )
    if service.lower() == "all":
        await stock_alerts.notification_preferences.update_one(
            {"user_id": message.from_user.id},
            {"$addToSet": {"disabled_services": "*"}}, upsert=True
        )
    else:
        await stock_alerts.set_service_notifications(message.from_user.id, service, False)
    await message.answer(f"🔕 Restock notifications turned off for <b>{esc(service)}</b>.")


@router.message(Command("notificationon"))
async def notification_on(message: Message):
    service = (message.text or "").partition(" ")[2].strip()
    if not service:
        return await message.answer(
            "🔔 <b>Notification settings</b>\\n"
            "Use: <code>/notificationon service name</code>\\n"
            "Example: <code>/notificationon WhatsApp</code>\\n"
            "To enable everything: <code>/notificationon all</code>"
        )
    if service.lower() == "all":
        await stock_alerts.notification_preferences.update_one(
            {"user_id": message.from_user.id},
            {"$set": {"disabled_services": []}}, upsert=True
        )
    else:
        await stock_alerts.set_service_notifications(message.from_user.id, service, True)
    await message.answer(f"🔔 Restock notifications turned on for <b>{esc(service)}</b>.")


@router.callback_query(F.data.startswith("stock:mute:"))
async def mute_stock_notifications(cq: CallbackQuery):
    service_key = cq.data.split(":", 2)[2]
    if service_key == "s2":
        service_name = "Telegram Accounts"
    else:
        catalog = await __import__("flows").s3_catalog() or {}
        service_name = str((catalog.get(service_key) or {}).get("name") or service_key)
    await stock_alerts.set_service_notifications(cq.from_user.id, service_name, False)
    await cq.answer(f"🔕 Notifications off: {service_name}", show_alert=True)



async def home_view(uid, full_name):
    u = await users.find_one({"_id": uid}, {"balance": 1}) or {}
    mention = f"<a href='tg://user?id={uid}'>{esc(full_name or 'there')}</a>"
    balance = fmt_curr(u.get('balance', 0))
    text = (
        f"✦ <b>{esc(BOT_TITLE).upper()}</b>  <code> DIGITAL HUB</code>\n"
        f"<i>One place. Your services. Your wallet.</i>\n"
        f"{SEP}\n"
        f"Hey {mention} 👋 <b>ready to get started?</b>\n\n"
        f"💳 <b>WALLET BALANCE</b>\n"
        f"<blockquote>💰 <b>{balance}</b></blockquote>\n"
        f"🛍 <b>EXPLORE SERVICES</b>\n"
        f"<i>Telegram accounts at the current provider rate — no added markup.</i>\n"
        f"\n<code>FAST ACCESS  •  CLEAR PRICING  •  ORDER TRACKING</code>"
    )
    rows = [
        [B("✈️  Telegram Accounts", "buy")],
        [B("💬  WhatsApp Accounts", "wp_terms"), B("📲  Apps OTP", "s3_user_root")],
        [B("📈  Social Media Services", "feature_smm_external")],
        [B("➕ Add Funds", "recharge"), B("👤 My Profile", "stats")],
    ]
    if SUPPORT_LINK:
        rows.append([B("🛟 Help & Support", url=normalize_support(SUPPORT_LINK))])
    return text, KB(*rows)


@router.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    await state.clear()
    text, kb = await home_view(msg.from_user.id, msg.from_user.full_name)
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "back_main")
async def back_main(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = await home_view(cq.from_user.id, cq.from_user.full_name)
    await edit(cq, text, kb)
    await cq.answer()


@router.callback_query(F.data == "stats")
async def profile(cq: CallbackQuery):
    u = await users.find_one({"_id": cq.from_user.id}, {"balance": 1}) or {}
    uname = f"@{esc(cq.from_user.username)}" if cq.from_user.username else "Not set"
    text = (
        f"👤 <b>MY PROFILE</b>\n{SEP}\n"
        f"<blockquote>🧑 <b>Name</b>\n{esc(cq.from_user.full_name or 'User')}</blockquote>\n"
        f"<blockquote>🔗 <b>Username</b>\n{uname}</blockquote>\n"
        f"<blockquote>🆔 <b>User ID</b>\n<code>{cq.from_user.id}</code></blockquote>\n"
        f"<blockquote>💳 <b>Wallet balance</b>\n<b>{fmt_curr(u.get('balance', 0))}</b></blockquote>\n"
        "<i>View your recent orders or return to the main menu.</i>"
    )
    await edit(cq, text, KB([B("📜 Order History", "history")],
                            [B("➕ Add Funds", "recharge")],
                            [B("🏠 Main Menu", "back_main")]))
    await cq.answer()


@router.callback_query(F.data == "history")
async def history(cq: CallbackQuery):
    rows = await orders.find({"user_id": cq.from_user.id}).sort("date", -1).limit(15).to_list(15)
    if not rows:
        await edit(cq, f"📜 <b>ORDER HISTORY</b>\n{SEP}\n\nYou haven't placed an order yet.\n\n<i>Browse services to place your first order.</i>",
                   KB([B("🛍 Browse Services", "back_main")], [B("👤 My Profile", "stats")]))
        return await cq.answer()
    icon = {"tg": "✈️", "wa": "💬", "otp": "📲", "smm": "🌍"}
    lines = []
    for o in rows:
        d = o.get("date")
        extra = f" · <code>{esc(o['number'])}</code>" if o.get("number") else ""
        lines.append(f"{icon.get(o.get('type'), '🧾')} <b>{esc(o.get('title', 'Order'))}</b>{extra}\n"
                     f"     {fmt_curr(o.get('price', 0))} · {d.strftime('%d %b %H:%M') if d else ''}")
    await edit(cq, f"📜 <b>ORDER HISTORY</b>\n{SEP}\n<i>Latest {len(rows)} orders</i>\n\n" + "\n\n".join(lines),
               KB([B("👤 My Profile", "stats")], [B("🏠 Main Menu", "back_main")]))
    await cq.answer()
