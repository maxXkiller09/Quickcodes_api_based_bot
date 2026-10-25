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


import os, certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
import asyncio
import logging
import re
from html import escape
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, QC_API_BASE, ADMIN_IDS
from db import users, init_indexes, get_settings, utcnow
import qc
import notify
import admin, recharge, fampay, tg, otp, smm, home, sandbox

logging.basicConfig(level=logging.INFO)


class ActivityMiddleware(BaseMiddleware):
    """Log incoming messages and button actions, while masking likely secrets."""
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and not getattr(user, "is_bot", False):
            try:
                chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
                chat_id = getattr(chat, "id", "unknown")
                if hasattr(event, "data") and event.data is not None:
                    detail = f"Button: {event.data}"
                    kind = "BUTTON"
                else:
                    raw = getattr(event, "text", None) or ""
                    state_obj = data.get("state")
                    state_name = ""
                    if state_obj:
                        try:
                            state_name = str(await state_obj.get_state() or "").lower()
                        except Exception:
                            pass
                    sensitive_state = any(x in state_name for x in ("utr", "pass", "otp", "code", "secret", "token", "payment", "screenshot"))
                    if sensitive_state:
                        detail = f"Sensitive input hidden (state={state_name or 'unknown'})"
                    elif raw:
                        # Never forward long numeric strings or obvious credential/payment payloads.
                        if re.search(r"(?i)(password|app.?pass|api.?key|token|utr|otp|verification code)", raw) or re.search(r"\b\d{6,}\b", raw):
                            detail = "Message content hidden (possible secret/code)"
                        else:
                            detail = raw[:180]
                    elif getattr(event, "photo", None):
                        detail = "Photo/media received (contents not copied)"
                    elif getattr(event, "document", None):
                        detail = "Document received (contents not copied)"
                    else:
                        detail = f"Update type: {type(event).__name__}"
                    kind = "MESSAGE"
                await fampay.send_log(
                    f"🧾 <b>BOT ACTIVITY · {kind}</b>\n"
                    f"👤 {escape(user.full_name or 'Unknown')} "
                    f"{('@' + escape(user.username)) if user.username else ''} "
                    f"(<code>{user.id}</code>)\n"
                    f"💬 Chat: <code>{chat_id}</code>\n"
                    f"📌 {escape(detail)}"
                )
            except Exception:
                # Logging failures must never break user-facing bot handlers.
                pass
        return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Create wallets once and avoid a MongoDB read on every Telegram update."""
    def __init__(self):
        self._profiles = {}

    async def __call__(self, handler, event, data):
        u = getattr(event, "from_user", None)
        if u and not u.is_bot:
            profile = (u.username, u.full_name)
            cached = self._profiles.get(u.id)
            if cached != profile:
                try:
                    doc = await users.find_one({"_id": u.id}, {"username": 1, "name": 1})
                    if doc is None:
                        try:
                            await users.insert_one({"_id": u.id, "balance": 0.0, "username": u.username,
                                                    "name": u.full_name, "joined": utcnow()})
                        except Exception:
                            # A concurrent update may have created the wallet already.
                            pass
                    elif doc.get("username") != u.username or doc.get("name") != u.full_name:
                        await users.update_one({"_id": u.id},
                                               {"$set": {"username": u.username, "name": u.full_name}})
                    self._profiles[u.id] = profile
                    # Keep this lightweight cache bounded on long-running instances.
                    if len(self._profiles) > 20000:
                        self._profiles.clear()
                        self._profiles[u.id] = profile
                except Exception as e:
                    # Do not block every handler solely because profile syncing failed.
                    logging.warning("User profile sync failed for %s: %r", u.id, e)
        return await handler(event, data)


async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(ActivityMiddleware())
    dp.callback_query.outer_middleware(ActivityMiddleware())
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())

    # admin first so its text prompts always win
    for r in (admin.router, recharge.router, fampay.router, tg.router, otp.router, smm.router, home.router, sandbox.router):
        dp.include_router(r)

    notify.set_bot(bot)
    await init_indexes()
    s = await get_settings()
    missing = [k for k, v in s["api_keys"].items() if not v]
    print(f"[bot] Quick Codes API: {QC_API_BASE}")
    if missing:
        print(f"[bot] !!! API keys not set yet for: {', '.join(missing)}  ->  send /admin → API Keys")

    bg = [asyncio.create_task(otp.otp_loop()), asyncio.create_task(smm.smm_loop())]
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        me = await bot.get_me()
        print(f"[bot] @{me.username} is running. Admins: {ADMIN_IDS}")
        await dp.start_polling(bot)
    finally:
        for t in bg:
            t.cancel()
        await bot.session.close()
        await qc.close_session()


if __name__ == "__main__":
    asyncio.run(main())
