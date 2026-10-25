"""Pure input-validation tests for wallet recharge amounts."""
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import recharge


def test_parse_amount_accepts_rupee_and_comma_formatting():
    assert recharge.parse_amount("₹1,250") == 1250.0
    assert recharge.parse_amount(" 250.50 ") == 250.5


def test_parse_amount_rounds_to_two_decimal_places():
    assert recharge.parse_amount("10.129") == 10.13
    assert recharge.parse_amount("10.125") == 10.12


def test_parse_amount_rejects_zero_negative_and_invalid_input():
    assert recharge.parse_amount("0") is None
    assert recharge.parse_amount("-10") is None
    assert recharge.parse_amount("abc") is None
    assert recharge.parse_amount("") is None
