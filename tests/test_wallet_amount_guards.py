"""Guard tests for wallet functions against zero and negative amounts."""

import asyncio
import os

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
    for amount in (0, -1, -0.01):
        assert run(db.charge(7, amount)) is False


def test_refund_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(db, "users", NoWriteUsers())
    for amount in (0, -1, -0.01):
        assert run(db.refund(7, amount)) is False


def test_admin_credit_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(flows, "users", NoWriteUsers())
    for amount in (0, -1, -0.01):
        assert run(flows.admin_credit(7, amount)) is False


def test_admin_debit_rejects_zero_and_negative_amounts(monkeypatch):
    monkeypatch.setattr(flows, "users", NoWriteUsers())
    for amount in (0, -1, -0.01):
        assert run(flows.admin_debit(7, amount)) is False
