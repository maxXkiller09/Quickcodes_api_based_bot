"""Telegram account prices apply the configured Server 2 profit percentage."""

import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import flows


def test_telegram_account_view_applies_configured_markup(monkeypatch):
    async def fake_rows(force=False):
        return [
            {"country_code": "IN", "name": "India", "price": 4.25, "stock": 10},
            {"country_code": "US", "name": "United States", "price": "7.50", "stock": 3},
        ]

    async def configured_markup(_server):
        return 10

    monkeypatch.setattr(flows, "s2_rows", fake_rows)
    monkeypatch.setattr(flows, "profit_pct", configured_markup)

    result = asyncio.run(flows.s2_view())

    assert result[0]["my_price"] == 4.68
    assert result[1]["my_price"] == 8.25
    assert result[0]["price"] == 4.25
    assert result[1]["price"] == "7.50"


def test_telegram_account_view_keeps_unavailable_state(monkeypatch):
    async def unavailable(force=False):
        return None

    monkeypatch.setattr(flows, "s2_rows", unavailable)
    assert asyncio.run(flows.s2_view()) is None
