"""Guard tests for wallet functions against zero and negative amounts."""

import asyncio
import os
import math

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import db
import flows


def run(coro):
    return asyncio.run(coro)


class NoWriteUsers:
    async def update_one(self, *args, **kwargs):
        raise AssertionError("wallet update must not run for non-positive amounts")


def test_charge_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(db, "users", NoWriteUsers())
    for amount in (0, -1, -0.01, float('nan'), float('inf'), -float('inf')):
        assert run(db.charge(7, amount)) is False


def test_refund_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(db, "users", NoWriteUsers())
    for amount in (0, -1, -0.01, float("nan"), float("inf"), -float("inf"), None, "not-a-number", "Infinity"):
        assert run(db.refund(7, amount)) is False


def test_admin_credit_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(flows, "users", NoWriteUsers())
    for amount in (0, -1, -0.01):
        assert run(flows.admin_credit(7, amount)) is False


def test_admin_debit_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(flows, "users", NoWriteUsers())
    for amount in (0, -1, -0.01):
        assert run(flows.admin_debit(7, amount)) is False



def test_idempotent_wallet_writes_reject_non_finite_amounts(monkeypatch):
    monkeypatch.setattr(db, "users", NoWriteUsers())
    for amount in (float("nan"), float("inf"), -float("inf")):
        assert run(db.refund_once(7, amount, "refund-test")) == "failed"
        assert run(db.credit_once(7, amount, "credit-test")) == "failed"



def test_idempotent_wallet_writes_reject_malformed_amounts(monkeypatch):
    monkeypatch.setattr(db, "users", NoWriteUsers())
    for amount in (None, "not-a-number", "Infinity"):
        assert run(db.refund_once(7, amount, "bad-refund")) == "failed"
        assert run(db.credit_once(7, amount, "bad-credit")) == "failed"



def test_get_balance_handles_malformed_stored_values(monkeypatch):
    class StoredBalance:
        def __init__(self, value):
            self.value = value

        async def find_one(self, *args, **kwargs):
            return {"balance": self.value}

    for value in (None, "bad", float("nan"), float("inf"), -float("inf")):
        monkeypatch.setattr(db, "users", StoredBalance(value))
        assert run(db.get_balance(7)) == 0.0
