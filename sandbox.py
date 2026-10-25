# ============================================================================
# Copyright © 2026 Valrik Thakur (@valriks)
# Created & Developed by Valrik Thakur (@valriks) | VTH NETWORK (@vthchannel)
# Authorized modifications must retain the original author/brand credits.
# ============================================================================

"""Admin-only Quick Codes sandbox smoke tests. Never changes the local user wallet."""
import json
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from config import ADMIN_IDS
from qc import qc
from utils import B, KB, SEP

router = Router()
_last_order = {}


def _pretty(data):
    return escape(json.dumps(data, ensure_ascii=False, indent=2)[:3200])


def _panel():
    return KB(
        [B("🧪 Test Order", "sandbox:order"), B("💳 Test Payment", "sandbox:payment")],
        [B("📩 Get Test OTP", "sandbox:otp"), B("⚠️ Test Error", "sandbox:error")],
    )


async def _call(server, method, path, *, json_body=None, timeout=20):
    status, data = await qc(server, method, path, json=json_body, timeout=timeout)
    return status, data


@router.message(Command("sandbox"))
async def sandbox_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer(
        "🧪 <b>QUICK CODES SANDBOX</b>\n" + SEP +
        "\nThese checks call only /v1/sandbox/* endpoints. They do not credit or debit this bot's local wallet.\n"
        "Use a valid Server 2 API key in /admin → API Keys.",
        reply_markup=_panel(),
    )


@router.callback_query(F.data.startswith("sandbox:"))
async def sandbox_action(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Admin only.", show_alert=True)
        return
    action = call.data.split(":", 1)[1]
    await call.answer("Running sandbox test…")
    await call.message.edit_text("⏳ Running sandbox test…")

    if action == "order":
        status, data = await _call("s2", "POST", "/v1/sandbox/order", json_body={"server": "s2"}, timeout=30)
        order_id = data.get("order_id") if isinstance(data, dict) else None
        if status == 200 and data.get("ok") and order_id:
            _last_order[call.from_user.id] = str(order_id)
            text = "✅ <b>Sandbox order created</b>\n" + SEP + "\n" + _pretty(data)
        else:
            text = f"❌ <b>Sandbox order failed (HTTP {status})</b>\n" + _pretty(data)
    elif action == "otp":
        order_id = _last_order.get(call.from_user.id)
        if not order_id:
            text = "ℹ️ Pehle <b>Test Order</b> dabao; OTP check karne ke liye order ID chahiye."
        else:
            status, data = await _call("s2", "GET", f"/v1/sandbox/code/{order_id}", timeout=20)
            text = ("✅" if status == 200 and data.get("ok") else "⚠️") + f" <b>Sandbox OTP (HTTP {status})</b>\n" + _pretty(data)
    elif action == "payment":
        status, data = await _call("s2", "POST", "/v1/sandbox/payment", json_body={"amount": 10}, timeout=20)
        text = ("✅" if status == 200 and data.get("ok") else "⚠️") + f" <b>Simulated payment (HTTP {status})</b>\n" + _pretty(data)
        text += "\n\n<i>This is a provider sandbox request; it does not credit this bot's local wallet.</i>"
    elif action == "error":
        status, data = await _call("s2", "GET", "/v1/sandbox/error/timeout", timeout=20)
        text = f"🧪 <b>Sandbox error response (HTTP {status})</b>\n" + _pretty(data)
        text += "\n\n<i>This confirms the response received; it does not prove every production error is handled.</i>"
    else:
        text = "Unknown sandbox test."

    await call.message.edit_text(text, reply_markup=_panel())


@router.callback_query(F.data.startswith("sandbox:"))
async def _unused_duplicate_guard(call: CallbackQuery):
    # Kept intentionally unreachable; the primary handler above owns sandbox callbacks.
    return
