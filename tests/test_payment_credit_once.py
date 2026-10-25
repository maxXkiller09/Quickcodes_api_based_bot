"""Fake-database tests for idempotent payment wallet credits."""

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import db


def run(coro):
    return asyncio.run(coro)


class FakeUsers:
    def __init__(self, user, *, fail_after_commit=False, fail_before_commit=False):
        self.user = dict(user)
        self.fail_after_commit = fail_after_commit
        self.fail_before_commit = fail_before_commit

    async def update_one(self, query, update):
        if self.user.get("_id") != query["_id"]:
            return SimpleNamespace(modified_count=0)
        key = query["credit_keys"]["$ne"]
        if key in self.user.get("credit_keys", []):
            return SimpleNamespace(modified_count=0)
        if self.fail_before_commit:
            self.fail_before_commit = False
            raise TimeoutError("simulated timeout before commit")
        self.user["balance"] = round(
            float(self.user.get("balance", 0)) + update["$inc"]["balance"], 2
        )
        self.user.setdefault("credit_keys", []).append(update["$addToSet"]["credit_keys"])
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise TimeoutError("simulated timeout after commit")
        return SimpleNamespace(modified_count=1)

    async def find_one(self, query, projection=None):
        if self.user.get("_id") != query["_id"]:
            return None
        return dict(self.user)


def test_payment_credit_and_key_are_written_together(monkeypatch):
    users = FakeUsers({"_id": 7, "balance": 10.0})
    monkeypatch.setattr(db, "users", users)

    result = run(db.credit_once(7, 6.25, "auto_upi:txn-1"))

    assert result == "applied"
    assert users.user["balance"] == 16.25
    assert users.user["credit_keys"] == ["auto_upi:txn-1"]


def test_duplicate_payment_credit_does_not_increment_twice(monkeypatch):
    users = FakeUsers({"_id": 7, "balance": 10.0})
    monkeypatch.setattr(db, "users", users)

    assert run(db.credit_once(7, 6.25, "fampay:order-1")) == "applied"
    assert run(db.credit_once(7, 6.25, "fampay:order-1")) == "already_applied"
    assert users.user["balance"] == 16.25


def test_timeout_after_commit_is_detected_as_already_applied(monkeypatch):
    users = FakeUsers(
        {"_id": 7, "balance": 10.0},
        fail_after_commit=True,
    )
    alerts = []

    async def alert(message):
        alerts.append(message)

    monkeypatch.setattr(db, "users", users)
    monkeypatch.setattr(db.notify, "alert_admins", alert)

    result = run(db.credit_once(7, 6.25, "manual:txn-1"))

    assert result == "already_applied"
    assert users.user["balance"] == 16.25
    assert not alerts


def test_timeout_before_commit_reports_failure_without_credit(monkeypatch):
    users = FakeUsers(
        {"_id": 7, "balance": 10.0},
        fail_before_commit=True,
    )
    alerts = []

    async def alert(message):
        alerts.append(message)

    monkeypatch.setattr(db, "users", users)
    monkeypatch.setattr(db.notify, "alert_admins", alert)

    result = run(db.credit_once(7, 6.25, "manual:txn-2"))

    assert result == "failed"
    assert users.user["balance"] == 10.0
    assert alerts and "PAYMENT CREDIT NEEDS REVIEW" in alerts[0]


def test_invalid_credit_amount_never_writes(monkeypatch):
    users = FakeUsers({"_id": 7, "balance": 10.0})
    monkeypatch.setattr(db, "users", users)

    assert run(db.credit_once(7, 0, "txn-zero")) == "failed"
    assert run(db.credit_once(7, -5, "txn-negative")) == "failed"
    assert users.user["balance"] == 10.0
    assert "credit_keys" not in users.user
