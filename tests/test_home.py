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
    async def fake_find_one(query, projection=None):
        assert query == {"_id": 42}
        return {"balance": 12.5}

    monkeypatch.setattr(home.users, "find_one", fake_find_one)
    monkeypatch.setattr(home, "SUPPORT_LINK", "")
    text, keyboard = asyncio.run(home.home_view(42, "<Sam & Co>"))

    assert "&lt;Sam &amp; Co&gt;" in text
    assert "₹12.50" in text
    assert "current provider rate" in text
    assert "no added markup" in text
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }
    assert {"buy", "wp_terms", "s3_user_root", "feature_smm_external", "recharge", "stats"} <= callbacks


def test_home_view_handles_missing_wallet_document(monkeypatch):
    async def fake_find_one(query, projection=None):
        return None

    monkeypatch.setattr(home.users, "find_one", fake_find_one)
    monkeypatch.setattr(home, "SUPPORT_LINK", "")
    text, keyboard = asyncio.run(home.home_view(99, "New User"))

    assert "₹0.00" in text
    assert "New User" in text
    assert keyboard.inline_keyboard


def test_profile_handler_escapes_profile_fields_and_shows_navigation(monkeypatch):
    class User:
        id = 42
        username = "<sam>"
        full_name = "<Sam & Co>"

    class Callback:
        from_user = User()

        def __init__(self):
            self.answered = False

        async def answer(self):
            self.answered = True

    async def fake_find_one(query, projection=None):
        assert query == {"_id": 42}
        return {"balance": 7}

    captured = {}

    async def fake_edit(target, text, markup=None):
        captured["text"] = text
        captured["markup"] = markup

    monkeypatch.setattr(home.users, "find_one", fake_find_one)
    monkeypatch.setattr(home, "edit", fake_edit)
    cq = Callback()
    asyncio.run(home.profile(cq))

    assert "&lt;Sam &amp; Co&gt;" in captured["text"]
    assert "@&lt;sam&gt;" in captured["text"]
    assert "₹7.00" in captured["text"]
    callbacks = {
        button.callback_data
        for row in captured["markup"].inline_keyboard
        for button in row
        if button.callback_data
    }
    assert {"history", "recharge", "back_main"} <= callbacks
    assert cq.answered is True


def test_history_empty_state_offers_browse_and_profile(monkeypatch):
    class FakeCursor:
        def sort(self, *args):
            return self

        def limit(self, count):
            return self

        async def to_list(self, count):
            return []

    class User:
        id = 7

    class Callback:
        from_user = User()

        def __init__(self):
            self.answered = False

        async def answer(self):
            self.answered = True

    captured = {}

    def fake_find(query):
        assert query == {"user_id": 7}
        return FakeCursor()

    async def fake_edit(target, text, markup=None):
        captured["text"] = text
        captured["markup"] = markup

    monkeypatch.setattr(home.orders, "find", fake_find)
    monkeypatch.setattr(home, "edit", fake_edit)
    cq = Callback()
    asyncio.run(home.history(cq))

    assert "haven't placed an order yet" in captured["text"]
    callbacks = {
        button.callback_data
        for row in captured["markup"].inline_keyboard
        for button in row
        if button.callback_data
    }
    assert {"back_main", "stats"} <= callbacks
    assert cq.answered is True


def test_home_view_adds_support_button_only_when_configured(monkeypatch):
    async def fake_find_one(query, projection=None):
        return {"balance": 0}

    monkeypatch.setattr(home.users, "find_one", fake_find_one)
    monkeypatch.setattr(home, "SUPPORT_LINK", "@nexora_support")
    _, keyboard = asyncio.run(home.home_view(42, "Sam"))

    support = [
        button for row in keyboard.inline_keyboard for button in row
        if button.text == "🛟 Help & Support"
    ]
    assert len(support) == 1
    assert support[0].url == "https://t.me/nexora_support"
