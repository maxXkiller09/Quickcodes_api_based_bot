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
from db import db, users
from flows import s2_view, s3_catalog, s3_countries
import notify

subscriptions = db["stock_alert_subscriptions"]
snapshots = db["stock_alert_snapshots"]
s3_subscriptions = db["stock_alert_s3_subscriptions"]
s3_snapshots = db["stock_alert_s3_snapshots"]
notification_preferences = db["notification_preferences"]
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


async def set_service_notifications(user_id: int, service_name: str, enabled: bool):
    """Notifications are ON by default; store only per-user service opt-outs."""
    await init_stock_alerts()
    name = " ".join(str(service_name or "").strip().lower().replace("-", " ").split())
    if not name:
        return
    if enabled:
        await notification_preferences.update_one(
            {"user_id": int(user_id)}, {"$pull": {"disabled_services": name}}, upsert=True
        )
    else:
        await notification_preferences.update_one(
            {"user_id": int(user_id)}, {"$addToSet": {"disabled_services": name}}, upsert=True
        )


async def service_notifications_enabled(user_id: int, service_name: str) -> bool:
    """Use one global admin-controlled notification policy for every bot user."""
    name = " ".join(str(service_name or "").strip().lower().replace("-", " ").split())
    pref = await notification_preferences.find_one({"user_id": "global"}, {"disabled_services": 1}) or {}
    disabled = pref.get("disabled_services") or []
    if "*" in disabled:
        return False
    aliases = {name}
    if name in {"telegram accounts", "telegram account", "server 2", "s2"}:
        aliases.update({"telegram accounts", "telegram account", "server 2", "s2"})
    if name in {"whatsapp", "whatsapp accounts", "server 3", "s3"}:
        aliases.update({"whatsapp", "whatsapp accounts"})
    return not bool(aliases.intersection(
        " ".join(str(x).strip().lower().replace("-", " ").split()) for x in disabled
    ))


async def _broadcast_restock(service_name: str, text: str, markup):
    """Send a restock alert to every known bot user unless that service is muted."""
    bot = notify.get_bot()
    if bot is None:
        logging.error("Restock alert skipped: Telegram bot is not initialized")
        return
    sent = skipped = failed = 0
    async for user in users.find({}, {"_id": 1}):
        uid = user.get("_id")
        if uid is None:
            continue
        try:
            if not await service_notifications_enabled(int(uid), service_name):
                skipped += 1
                continue
            await bot.send_message(int(uid), text, reply_markup=markup, parse_mode="HTML")
            sent += 1
        except Exception as exc:
            failed += 1
            logging.warning("Restock delivery failed service=%s user=%s error=%r", service_name, uid, exc)
    logging.info("Restock alert broadcast service=%s sent=%d muted=%d failed=%d", service_name, sent, skipped, failed)


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


async def monitor_loop(interval: int = 180):
    """Broadcast stock transitions to all users by default; users may mute each service."""
    await init_stock_alerts()
    logging.info("Restock monitor started; interval=%ss; notifications default ON", max(180, int(interval)))
    while True:
        try:
            checked_s2 = checked_s3 = alerts = 0
            # Server 2: provider country endpoint includes stock counts, including zero-stock rows.
            rows = await s2_view(force=True)
            if rows is not None:
                seen_s2_codes = set()
                for row in rows:
                    code = str(row.get("country_code") or "").strip()
                    if code:
                        seen_s2_codes.add(code)
                    if not code:
                        continue
                    try:
                        stock = max(0, int(row.get("stock", 0) or 0))
                    except (TypeError, ValueError):
                        continue
                    checked_s2 += 1
                    old = await snapshots.find_one({"country_code": code})
                    await snapshots.update_one(
                        {"country_code": code},
                        {"$set": {"stock": stock, "name": str(row.get("name") or code),
                                  "price": float(row.get("my_price") or 0)}},
                        upsert=True,
                    )
                    if not is_restock_transition(old.get("stock") if old else None, stock):
                        continue
                    name_raw = str(row.get("name") or code)
                    name = escape(name_raw)
                    price = float(row.get("my_price") or 0)
                    text = (
                        "🔔 <b>Back in Stock!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🌍 <b>Country:</b> {name}\n"
                        f"📊 <b>Stock:</b> {stock} accounts\n"
                        f"🏷️ <b>Price:</b> ₹{price:g}\n\n"
                        "<i>Grab it before it runs out!</i>"
                    )
                    markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🛒 Buy Now", callback_data=f"s2:country:{code}")],
                        [InlineKeyboardButton(text="🔕 Turn off Telegram Accounts", callback_data="stock:mute:s2")],
                    ])
                    alerts += 1
                    await _broadcast_restock("Telegram Accounts", text, markup)

                # Some provider endpoints omit zero-stock countries entirely.
                # Treat a previously known country missing from a successful response as zero,
                # so its next appearance can trigger a genuine restock transition.
                known_s2 = snapshots.find({})
                async for known in known_s2:
                    known_code = str(known.get("country_code") or "").strip()
                    if known_code and known_code not in seen_s2_codes and int(known.get("stock", 0) or 0) > 0:
                        await snapshots.update_one(
                            {"country_code": known_code},
                            {"$set": {"stock": 0}},
                        )

            # Server 3: check each catalog service, so WhatsApp and every Apps OTP service are covered.
            catalog = await s3_catalog() or {}
            for sid, service in catalog.items():
                service_name = str(service.get("name") or f"Service {sid}")
                countries = await s3_countries(str(sid), full=True, force=True, include_empty=True)
                if countries is None:
                    continue
                seen_s3_codes = set()
                for row in countries:
                    code = str(row.get("country") or "").strip()
                    if code:
                        seen_s3_codes.add(code)
                    if not code:
                        continue
                    try:
                        stock = max(0, int(row.get("stock", 0) or 0))
                    except (TypeError, ValueError):
                        continue
                    checked_s3 += 1
                    old = await s3_snapshots.find_one({"service_id": str(sid), "country_code": code})
                    name_raw = str(row.get("name") or code)
                    price = float(row.get("my_price") or 0)
                    await s3_snapshots.update_one(
                        {"service_id": str(sid), "country_code": code},
                        {"$set": {"stock": stock, "name": name_raw, "price": price},
                         "$setOnInsert": {"service_id": str(sid), "country_code": code}},
                        upsert=True,
                    )
                    if not is_restock_transition(old.get("stock") if old else None, stock):
                        continue
                    text = (
                        "🔔 <b>Back in Stock!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"📱 <b>Service:</b> {escape(service_name)}\n"
                        f"🌍 <b>Country:</b> {escape(name_raw)}\n"
                        f"📊 <b>Stock:</b> {stock} numbers\n"
                        f"🏷️ <b>Starting price:</b> ₹{price:g}\n\n"
                        "<i>Numbers are available again. Check live offers before buying.</i>"
                    )
                    markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🛒 View Numbers", callback_data=f"s3c:{sid}:{code}")],
                        [InlineKeyboardButton(text=f"🔕 Turn off {service_name[:30]}", callback_data=f"stock:mute:{sid}")],
                    ])
                    alerts += 1
                    await _broadcast_restock(service_name, text, markup)

                # Also handle APIs that only return countries currently in stock.
                known_s3 = s3_snapshots.find({"service_id": str(sid)})
                async for known in known_s3:
                    known_code = str(known.get("country_code") or "").strip()
                    if known_code and known_code not in seen_s3_codes and int(known.get("stock", 0) or 0) > 0:
                        await s3_snapshots.update_one(
                            {"service_id": str(sid), "country_code": known_code},
                            {"$set": {"stock": 0}},
                        )
            logging.info("Restock poll complete: s2_countries=%d s3_countries=%d alerts=%d", checked_s2, checked_s3, alerts)
            await asyncio.sleep(max(180, int(interval)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Restock monitor iteration failed")
            await asyncio.sleep(max(180, int(interval)))
