"""Isolated SMM purchase-flow tests; all DB/provider operations are mocked."""

import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import flows


SERVICE = {
    "service": 42,
    "name": "Test likes",
    "rate": 10.0,
    "min": 100,
    "max": 10000,
    "type": "Default",
    "rate_unit": "per_1000",
    "refill": False,
}


def run(coro):
    return asyncio.run(coro)


async def value(result):
    return result


def setup_catalog(monkeypatch, service=SERVICE):
    monkeypatch.setattr(
        flows, "smm_catalog",
        lambda: value({"services": {str(service["service"]): service}}),
    )
    monkeypatch.setattr(flows, "profit_pct", lambda *_: value(0))


async def record_call(calls, name, *args):
    calls.append((name, args))
    return True


def test_buy_smm_rejects_stale_shown_price_before_charging(monkeypatch):
    setup_catalog(monkeypatch)
    calls = []
    monkeypatch.setattr(flows, "charge", lambda *a: record_call(calls, "charge", *a))

    result = run(flows.buy_smm(7, "42", "https://example.com/post", 1000, None, 1.00))

    assert result == {"kind": "price", "price": 10.0}
    assert calls == []


def test_buy_smm_reports_insufficient_balance_without_provider_call(monkeypatch):
    setup_catalog(monkeypatch)
    monkeypatch.setattr(flows, "charge", lambda *_: value(False))
    monkeypatch.setattr(flows, "get_balance", lambda *_: value(2.0))
    provider_calls = []
    monkeypatch.setattr(flows, "qc", lambda *a, **k: record_call(provider_calls, "qc", *a))

    result = run(flows.buy_smm(7, "42", "https://example.com/post", 1000, None, 10.0))

    assert result == {"kind": "funds", "need": 8.0}
    assert provider_calls == []


def test_buy_smm_refunds_when_provider_rejects_order(monkeypatch):
    setup_catalog(monkeypatch)
    charged = []
    refunded = []
    monkeypatch.setattr(flows, "charge", lambda uid, amount: record_call(charged, "charge", uid, amount))
    monkeypatch.setattr(flows, "refund", lambda uid, amount: record_call(refunded, "refund", uid, amount))
    monkeypatch.setattr(flows, "qc", lambda *a, **k: provider_failure())
    monkeypatch.setattr(flows, "explain", explain_failure)

    result = run(flows.buy_smm(7, "42", "https://example.com/post", 1000, None, 10.0))

    assert result == {"kind": "fail", "text": "Provider rejected the order"}
    assert charged == [("charge", (7, 10.0))]
    assert refunded == [("refund", (7, 10.0))]


async def provider_failure():
    return 400, {"ok": False, "error": "invalid_order"}


async def explain_failure(*args):
    return "fail", "Provider rejected the order"


def test_buy_smm_returns_unavailable_for_unknown_service_without_charging(monkeypatch):
    monkeypatch.setattr(flows, "smm_catalog", lambda: value({"services": {}}))
    calls = []
    monkeypatch.setattr(flows, "charge", lambda *a, **k: record_call(calls, "charge", *a))

    result = run(flows.buy_smm(7, "missing", "https://example.com/post", 1000, None, 10.0))

    assert result == {"kind": "fail", "text": "❌ This service is no longer available."}
    assert calls == []
