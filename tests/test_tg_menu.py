"""Isolated tests for the Telegram account country picker."""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import tg


def country(code, name, stock=2, price=4.5):
    return {"country_code": code, "name": name, "stock": stock, "my_price": price}


def callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def test_country_menu_empty_state_returns_home_button(monkeypatch):
    async def fake_s2_view():
        return []

    monkeypatch.setattr(tg, "s2_view", fake_s2_view)
    text, markup = asyncio.run(tg.build_menu())

    assert "No countries available" in text
    assert callbacks(markup) == ["back_main"]


def test_country_menu_paginates_and_clamps_page(monkeypatch):
    async def fake_s2_view():
        return [country(f"C{i:02}", f"Country {i}") for i in range(15)]

    monkeypatch.setattr(tg, "s2_view", fake_s2_view)
    text, markup = asyncio.run(tg.build_menu(page=999))

    assert "Page <b>2/2</b>" in text
    buttons = callbacks(markup)
    # Equal-price ties use country name as the deterministic secondary sort.
    assert "s2:country:C09" in buttons
    assert "s2:page:1" not in buttons
    assert "s2:search" in buttons
    assert "back_main" in buttons


def test_country_search_is_case_insensitive_and_escapes_query(monkeypatch):
    async def fake_s2_view():
        return [country("IN", "India"), country("US", "United States")]

    monkeypatch.setattr(tg, "s2_view", fake_s2_view)
    text, markup = asyncio.run(tg.build_menu(query="<IND"))

    assert "No countries found" in text
    assert "&lt;IND" in text
    assert callbacks(markup) == ["s2:search", "back_main"]


def test_country_search_returns_matching_country(monkeypatch):
    async def fake_s2_view():
        return [country("IN", "India"), country("US", "United States")]

    monkeypatch.setattr(tg, "s2_view", fake_s2_view)
    text, markup = asyncio.run(tg.build_menu(query="india"))

    assert "Search results" in text
    assert "s2:country:IN" in callbacks(markup)
    assert "s2:country:US" not in callbacks(markup)



def test_country_menu_sorts_by_price_low_to_high(monkeypatch):
    async def fake_s2_view():
        return [
            country("US", "United States", price=9.0),
            country("IN", "India", price=2.0),
            country("BR", "Brazil", price=5.0),
        ]

    monkeypatch.setattr(tg, "s2_view", fake_s2_view)
    _, markup = asyncio.run(tg.build_menu())
    buttons = callbacks(markup)
    assert buttons.index("s2:country:IN") < buttons.index("s2:country:BR")
    assert buttons.index("s2:country:BR") < buttons.index("s2:country:US")
