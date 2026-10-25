"""Unit tests for durable, idempotent wallet refunds using a fake users collection."""

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
    def __init__(self, users):
        self.users = {uid: dict(doc) for uid, doc in users.items()}

    async def update_one(self, query, update):
        user = self.users.get(query["_id"])
        if user is None or str(query["refund_keys"]["$ne"]) in user.get("refund_keys", []):
            return SimpleNamespace(modified_count=0)
        user["balance"] = round(float(user.get("balance", 0)) + update["$inc"]["balance"], 2)
        user.setdefault("refund_keys", [])
        key = update["$addToSet"]["refund_keys"]
        if key not in user["refund_keys"]:
            user["refund_keys"].append(key)
        return SimpleNamespace(modified_count=1)

    async def find_one(self, query, projection=None):
        user = self.users.get(query["_id"])
        return dict(user) if user else None


def test_refund_once_credits_and_records_key_atomically(monkeypatch):
    users = FakeUsers({7: {"balance": 10.0}})
    monkeypatch.setattr(db, "users", users)

    result = run(db.refund_once(7, 6.25, "smm:order-1:provider-refund"))

    assert result == "applied"
    assert users.users[7]["balance"] == 16.25
    assert users.users[7]["refund_keys"] == ["smm:order-1:provider-refund"]


def test_refund_once_duplicate_key_never_credits_twice(monkeypatch):
    users = FakeUsers({7: {"balance": 10.0}})
    monkeypatch.setattr(db, "users", users)

    first = run(db.refund_once(7, 6.25, "smm:order-1:provider-refund"))
    second = run(db.refund_once(7, 6.25, "smm:order-1:provider-refund"))

    assert first == "applied"
    assert second == "already_applied"
    assert users.users[7]["balance"] == 16.25


def test_refund_once_missing_user_reports_failure(monkeypatch):
    users = FakeUsers({})
    alerts = []
    monkeypatch.setattr(db, "users", users)
    monkeypatch.setattr(db.notify, "alert_admins", lambda message: record_alert(alerts, message))

    result = run(db.refund_once(404, 6.25, "smm:order-404:provider-refund"))

    assert result == "failed"
    assert alerts and "REFUND FAILED" in alerts[0]


async def record_alert(alerts, message):
    alerts.append(message)
