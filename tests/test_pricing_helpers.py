"""Tests for pure pricing helpers; no wallet or provider calls."""

import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

from flows import norm, with_profit


def test_norm_cleans_case_hyphens_and_repeated_whitespace():
    assert norm("  WhatsApp-Business   API ") == "whatsapp business api"
    assert norm(None) == ""


def test_with_profit_without_markup_rounds_to_two_decimals():
    assert with_profit(10.125, 0) == 10.12
    assert with_profit("12.50", None) == 12.5


def test_with_profit_applies_percentage_markup():
    assert with_profit(100, 10) == 110.0
    assert with_profit(12.5, 20) == 15.0


def test_with_profit_whole_mode_rounds_up_fractional_values():
    assert with_profit(10, 5, whole=True) == 11
    assert with_profit(10, 0, whole=True) == 10
