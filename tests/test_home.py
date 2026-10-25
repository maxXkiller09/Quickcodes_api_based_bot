"""Fake-database tests for Nexora's main menu builder."""
import asyncio
import os

# The handler module reads configuration at import time; these are isolated test-only values.
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import home


def test_home_view_escapes_user_name_and_renders_core_navigation(monkeypatch):
    async def fake_find_one(query):
        assert query == {"_id": 42}
        return {"balance": 12.5}

    monkeypatch.setattr(home.users, "find_one", fake_find_one)
    text, keyboard = asyncio.run(home.home_view(42, "<Sam & Co>"))

    assert "&lt;Sam &amp; Co&gt;" in text
    assert "₹12.50" in text
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }
    assert {"buy", "wp_terms", "s3_user_root", "feature_smm_external", "recharge", "stats"} <= callbacks


def test_home_view_handles_missing_wallet_document(monkeypatch):
    async def fake_find_one(query):
        return None

    monkeypatch.setattr(home.users, "find_one", fake_find_one)
    text, keyboard = asyncio.run(home.home_view(99, "New User"))

    assert "₹0.00" in text
    assert "New User" in text
    assert keyboard.inline_keyboard
