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
from flows import s2_view, s3_catalog, s3_countries
import notify

subscriptions = db["stock_alert_subscriptions"]
snapshots = db["stock_alert_snapshots"]
s3_subscriptions = db["stock_alert_s3_subscriptions"]
s3_snapshots = db["stock_alert_s3_snapshots"]
_initialized = False


async def init_stock_alerts():
    global _initialized
    if _initialized:
        return
    await subscriptions.create_index([("user_id", 1), ("country_code", 1)], unique=True)
    await subscriptions.create_index([("active", 1), ("country_code", 1)])
    await snapshots.create_index("country_code", unique=True)
    await s3_subscriptions.create_index([("user_id", 1), ("service_id", 1), ("country_code", 1)], unique=True)
    await s3_subscriptions.create_index([("active", 1), ("service_id", 1), ("country_code", 1)])
    await s3_snapshots.create_index([("service_id", 1), ("country_code", 1)], unique=True)
    _initialized = True


async def subscribe(user_id: int, country_code: str):
    await init_stock_alerts()
    code = str(country_code).strip()
    await subscriptions.update_one(
        {"user_id": int(user_id), "country_code": code},
        {"$set": {"active": True}},
        upsert=True,
    )

    # Seed a fresh baseline when possible. If the monitor was idle because no
    # users were subscribed, an old zero-stock snapshot must not cause a new
    # subscriber to receive a stale alert for stock that returned beforehand.
    try:
        rows = await s2_view(force=True)
        row = next((item for item in (rows or [])
                    if str(item.get("country_code") or "").strip() == code), None)
        if row is not None:
            stock = max(0, int(row.get("stock", 0) or 0))
            await snapshots.update_one(
                {"country_code": code},
                {"$set": {"stock": stock,
                          "name": str(row.get("name") or code),
                          "price": float(row.get("my_price") or 0)}},
                upsert=True,
            )
    except Exception:
        # Subscription remains enabled even if the provider is temporarily down.
        logging.exception("Could not seed restock baseline for country %s", code)


async def unsubscribe(user_id: int, country_code: str):
    await init_stock_alerts()
    await subscriptions.update_one(
        {"user_id": int(user_id), "country_code": str(country_code)},
        {"$set": {"active": False}},
        upsert=True,
    )

async def subscribe_s3(user_id: int, service_id: str, country_code: str):
    """Subscribe to a Server 3 service/country becoming available again."""
    await init_stock_alerts()
    sid, code = str(service_id).strip(), str(country_code).strip()
    await s3_subscriptions.update_one(
        {"user_id": int(user_id), "service_id": sid, "country_code": code},
        {"$set": {"active": True}},
        upsert=True,
    )
    try:
        rows = await s3_countries(sid, full=True, force=True, include_empty=True)
        row = next((item for item in (rows or [])
                    if str(item.get("country") or "").strip() == code), None)
        if row is not None:
            await s3_snapshots.update_one(
                {"service_id": sid, "country_code": code},
                {"$set": {"stock": max(0, int(row.get("stock", 0) or 0)),
                          "name": str(row.get("name") or code),
                          "price": float(row.get("my_price") or 0)},
                "$setOnInsert": {"service_id": sid, "country_code": code}},
                upsert=True,
            )
    except Exception:
        logging.exception("Could not seed Server 3 restock baseline for %s/%s", sid, code)


async def unsubscribe_s3(user_id: int, service_id: str, country_code: str):
    await init_stock_alerts()
    await s3_subscriptions.update_one(
        {"user_id": int(user_id), "service_id": str(service_id), "country_code": str(country_code)},
        {"$set": {"active": False}},
        upsert=True,
    )


def is_restock_transition(previous_stock, current_stock) -> bool:
    """Return true only for a known zero-to-positive stock transition."""
    if previous_stock is None:
        return False
    try:
        previous = max(0, int(previous_stock or 0))
        current = max(0, int(current_stock or 0))
    except (TypeError, ValueError):
        return False
    return previous == 0 and current > 0


async def stop_all(user_id: int):
    await init_stock_alerts()
    await subscriptions.update_many({"user_id": int(user_id), "active": True}, {"$set": {"active": False}})
    await s3_subscriptions.update_many({"user_id": int(user_id), "active": True}, {"$set": {"active": False}})


async def monitor_loop(interval: int = 90):
    """Poll only subscribed services; alert on known zero-to-positive stock transitions."""
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
                        if not is_restock_transition(old.get("stock") if old else None, stock):
                            continue
                        subs = subscriptions.find({"country_code": code, "active": True})
                        name = escape(str(row.get("name") or code))
                        price = float(row.get("my_price") or 0)
                        text = (
                            "🔔 <b>Back in Stock!</b>\\n"
                            "━━━━━━━━━━━━━━━━━━━━\\n"
                            f"🌍 <b>Country:</b> {name}\\n"
                            f"📊 <b>Stock:</b> {stock} account(s)\\n"
                            f"🏷️ <b>Price:</b> ₹{price:g}\\n\\n"
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

            active_s3 = s3_subscriptions.find({"active": True})
            service_ids = set()
            async for sub in active_s3:
                if sub.get("service_id"):
                    service_ids.add(str(sub["service_id"]))
            if service_ids:
                cat = await s3_catalog() or {}
                for sid in service_ids:
                    rows = await s3_countries(sid, full=True, force=True, include_empty=True)
                    if rows is None:
                        continue
                    rows_by_code = {str(r.get("country") or "").strip(): r for r in rows}
                    active_for_service = s3_subscriptions.find({"active": True, "service_id": sid})
                    async for sub in active_for_service:
                        code = str(sub.get("country_code") or "").strip()
                        row = rows_by_code.get(code)
                        if not row:
                            continue
                        try:
                            stock = max(0, int(row.get("stock", 0) or 0))
                        except (TypeError, ValueError):
                            continue
                        old = await s3_snapshots.find_one({"service_id": sid, "country_code": code})
                        name_raw = str(row.get("name") or code)
                        price = float(row.get("my_price") or 0)
                        await s3_snapshots.update_one(
                            {"service_id": sid, "country_code": code},
                            {"$set": {"stock": stock, "name": name_raw, "price": price},
                             "$setOnInsert": {"service_id": sid, "country_code": code}},
                            upsert=True,
                        )
                        if not is_restock_transition(old.get("stock") if old else None, stock):
                            continue
                        service_name = escape(str((cat.get(sid) or {}).get("name") or "OTP Service"))
                        country_name = escape(name_raw)
                        text = (
                            "🔔 <b>Back in Stock!</b>\\n"
                            "━━━━━━━━━━━━━━━━━━━━\\n"
                            f"📱 <b>Service:</b> {service_name}\\n"
                            f"🌍 <b>Country:</b> {country_name}\\n"
                            f"📊 <b>Stock:</b> {stock} number(s)\\n"
                            f"🏷️ <b>Starting price:</b> ₹{price:g}\\n\\n"
                            "<i>Numbers are available again. Check the live offer before buying.</i>"
                        )
                        markup = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="🛒 View Numbers", callback_data=f"s3c:{sid}:{code}")],
                            [InlineKeyboardButton(text="🔕 Stop Notification", callback_data=f"stock3:stop:{sid}:{code}")],
                        ])
                        try:
                            await notify.get_bot().send_message(int(sub["user_id"]), text,
                                                                reply_markup=markup, parse_mode="HTML")
                        except Exception as exc:
                            logging.info("Server 3 restock alert failed for user %s: %r", sub.get("user_id"), exc)
            await asyncio.sleep(max(60, int(interval)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Restock monitor iteration failed")
            await asyncio.sleep(max(60, int(interval)))
