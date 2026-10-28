# ============================================================================
# Copyright © 2026 Valrik Thakur (@valriks)
#
# Created & Developed by: Valrik Thakur (@valriks)
# Brand / Network: VTH NETWORK (@vthchannel)
#
# Authorized modifications retain original author and brand credits.
# ============================================================================

"""Low-frequency Server 2 restock alerts for users who opt in."""
import asyncio
import logging
from html import escape

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import db
from flows import s2_view
import notify

subscriptions = db["stock_alert_subscriptions"]
snapshots = db["stock_alert_snapshots"]
_initialized = False


async def init_stock_alerts():
    global _initialized
    if _initialized:
        return
    await subscriptions.create_index([("user_id", 1), ("country_code", 1)], unique=True)
    await subscriptions.create_index([("active", 1), ("country_code", 1)])
    await snapshots.create_index("country_code", unique=True)
    _initialized = True


async def subscribe(user_id: int, country_code: str):
    await init_stock_alerts()
    await subscriptions.update_one(
        {"user_id": int(user_id), "country_code": str(country_code)},
        {"$set": {"active": True}},
        upsert=True,
    )


async def unsubscribe(user_id: int, country_code: str):
    await init_stock_alerts()
    await subscriptions.update_one(
        {"user_id": int(user_id), "country_code": str(country_code)},
        {"$set": {"active": False}},
        upsert=True,
    )


async def stop_all(user_id: int):
    await init_stock_alerts()
    await subscriptions.update_many({"user_id": int(user_id), "active": True}, {"$set": {"active": False}})


async def monitor_loop(interval: int = 90):
    """Poll only while subscribers exist; baseline first run avoids false alerts."""
    await init_stock_alerts()
    while True:
        try:
            if await subscriptions.count_documents({"active": True}, limit=1):
                rows = await s2_view(force=True)
                if rows is not None:
                    for row in rows:
                        code = str(row.get("country_code") or "").strip()
                        if not code:
                            continue
                        try:
                            stock = max(0, int(row.get("stock", 0) or 0))
                        except (TypeError, ValueError):
                            continue
                        old = await snapshots.find_one({"country_code": code})
                        await snapshots.update_one(
                            {"country_code": code},
                            {"$set": {"stock": stock, "name": str(row.get("name") or code),
                                      "price": float(row.get("my_price") or 0)}},
                            upsert=True,
                        )
                        if not old or int(old.get("stock", 0) or 0) > 0 or stock <= 0:
                            continue
                        subs = subscriptions.find({"country_code": code, "active": True})
                        name = escape(str(row.get("name") or code))
                        price = float(row.get("my_price") or 0)
                        text = (
                            "🔔 <b>Back in Stock!</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━\n"
                            f"🌍 <b>Country:</b> {name}\n"
                            f"📊 <b>Stock:</b> {stock} account(s)\n"
                            f"🏷️ <b>Price:</b> ₹{price:g}\n\n"
                            "<i>Grab it before it runs out!</i>"
                        )
                        markup = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🛒 Buy Now", callback_data=f"s2:country:{code}")],
                            [InlineKeyboardButton(text="🔕 Stop Notification", callback_data=f"stock:stop:{code}")],
                        ])
                        async for sub in subs:
                            try:
                                await notify.get_bot().send_message(int(sub["user_id"]), text,
                                                                    reply_markup=markup, parse_mode="HTML")
                            except Exception as exc:
                                logging.info("Restock alert failed for user %s: %r", sub.get("user_id"), exc)
            await asyncio.sleep(max(60, int(interval)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Restock monitor iteration failed")
            await asyncio.sleep(max(60, int(interval)))
