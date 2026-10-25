"""Fake-collection tests for retryable Server 3 cancellation refunds."""
import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import flows


def run(coro):
    return asyncio.run(coro)


class FakeTasks:
    def __init__(self, task):
        self.task = dict(task)
        self.updates = []

    async def find_one_and_update(self, query, update):
        if self.task.get("order_id") != query.get("order_id") or self.task.get("status") != query.get("status"):
            return None
        old = dict(self.task)
        self.task.update(update["$set"])
        return old

    async def update_one(self, query, update):
        if self.task.get("order_id") == query.get("order_id") and self.task.get("status") == query.get("status"):
            self.task.update(update["$set"])
            self.updates.append(dict(update["$set"]))
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def task():
    return {"order_id": "s3-123", "user_id": 77, "price": 15.5,
            "status": "active", "ts": 1.0}


def test_s3_cancel_refund_uses_stable_key_and_finalizes(monkeypatch):
    t = task()
    collection = FakeTasks(t)
    calls = []

    async def refund_once(uid, amount, key):
        calls.append((uid, amount, key))
        return "applied"

    monkeypatch.setattr(flows, "tasks", collection)
    monkeypatch.setattr(flows, "refund_once", refund_once)
    monkeypatch.setattr(flows, "utcnow", lambda: "test-time")

    won = run(flows.cancel_task_with_refund(t, "user"))

    assert won is True
    assert calls == [(77, 15.5, "s3:s3-123:cancel-refund")]
    assert collection.task["status"] == "cancelled"
    assert collection.task["refunded"] is True


def test_s3_failed_refund_remains_pending_then_retry_finalizes(monkeypatch):
    t = task()
    collection = FakeTasks(t)
    results = iter(["failed", "applied"])
    calls = []
    alerts = []

    async def refund_once(uid, amount, key):
        calls.append((uid, amount, key))
        return next(results)

    async def alert(message, **kwargs):
        alerts.append(message)

    monkeypatch.setattr(flows, "tasks", collection)
    monkeypatch.setattr(flows, "refund_once", refund_once)
    monkeypatch.setattr(flows.notify, "alert_admins", alert)
    monkeypatch.setattr(flows, "utcnow", lambda: "test-time")

    won = run(flows.cancel_task_with_refund(t, "provider_cancelled"))
    assert won is True
    assert collection.task["status"] == "refund_pending"
    assert collection.task.get("refunded") is not True
    assert alerts and "refund pending" in alerts[0].lower()

    run(flows._finish_task_refund(dict(collection.task)))

    assert collection.task["status"] == "cancelled"
    assert collection.task["refunded"] is True
    assert len(calls) == 2
    assert calls[0][2] == calls[1][2] == "s3:s3-123:cancel-refund"


def test_s3_duplicate_cancellation_does_not_start_second_refund(monkeypatch):
    t = task()
    collection = FakeTasks(t)
    calls = []

    async def refund_once(uid, amount, key):
        calls.append(key)
        return "applied"

    monkeypatch.setattr(flows, "tasks", collection)
    monkeypatch.setattr(flows, "refund_once", refund_once)
    monkeypatch.setattr(flows, "utcnow", lambda: "test-time")

    assert run(flows.cancel_task_with_refund(t, "user")) is True
    assert run(flows.cancel_task_with_refund(t, "user")) is False
    assert calls == ["s3:s3-123:cancel-refund"]
