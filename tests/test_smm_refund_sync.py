"""Mocked tests for SMM refund synchronization and duplicate-refund protection."""

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


async def provider_refund(*args, **kwargs):
    return 200, {
        "ok": True,
        "status": "partial",
        "start_count": 100,
        "remains": 400,
        "refunded": True,
        "refund_amount": 3.0,
        "charge": 10.0,
    }


class FakeSmmOrders:
    def __init__(self, order):
        self.order = dict(order)
        self.refund_claims = 0

    async def update_one(self, query, update):
        if query.get("refunded") == {"$ne": True}:
            if self.order.get("refunded") is True:
                return SimpleNamespace(modified_count=0)
            self.refund_claims += 1
        self.order.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1)

    async def find_one(self, query):
        return dict(self.order)


def sample_order(**overrides):
    order = {
        "_id": "fake-order-doc",
        "user_id": 77,
        "order_id": 1234,
        "cost": 20.0,
        "api_charge": 10.0,
        "status": "pending",
        "refunded": False,
        "refund_amount": 0.0,
    }
    order.update(overrides)
    return order


def test_partial_refund_scales_provider_amount_to_user_price(monkeypatch):
    order = sample_order()
    collection = FakeSmmOrders(order)
    refunds = []
    monkeypatch.setattr(flows, "smm_orders", collection)
    monkeypatch.setattr(flows, "qc", provider_refund)
    monkeypatch.setattr(flows, "refund_once", lambda uid, amount, key: record_refund_once(refunds, uid, amount, key))

    fresh, got = run(flows.smm_sync_order(order))

    assert got == 6.0
    assert refunds == [(77, 6.0, "smm:fake-order-doc:provider-refund")]
    assert fresh["refunded"] is True
    assert fresh["refund_amount"] == 6.0
    assert fresh["status"] == "partial"


def test_repeated_sync_does_not_issue_duplicate_refund(monkeypatch):
    order = sample_order()
    collection = FakeSmmOrders(order)
    refunds = []
    monkeypatch.setattr(flows, "smm_orders", collection)
    monkeypatch.setattr(flows, "qc", provider_refund)
    monkeypatch.setattr(flows, "refund_once", lambda uid, amount, key: record_refund_once(refunds, uid, amount, key))

    first = run(flows.smm_sync_order(order))
    second = run(flows.smm_sync_order(first[0]))

    assert first[1] == 6.0
    assert second[1] == 0.0
    assert refunds == [(77, 6.0, "smm:fake-order-doc:provider-refund")]
    assert collection.refund_claims == 1


def test_refund_is_capped_at_amount_user_paid(monkeypatch):
    order = sample_order(cost=8.0, api_charge=10.0)
    collection = FakeSmmOrders(order)
    refunds = []

    async def oversized_provider_refund(*args, **kwargs):
        status, data = await provider_refund(*args, **kwargs)
        data["refund_amount"] = 99.0
        return status, data

    monkeypatch.setattr(flows, "smm_orders", collection)
    monkeypatch.setattr(flows, "qc", oversized_provider_refund)
    monkeypatch.setattr(flows, "refund", lambda uid, amount: record(refunds, uid, amount))

    fresh, got = run(flows.smm_sync_order(order))

    assert got == 8.0
    assert fresh["refund_amount"] == 8.0
    assert refunds == [(77, 8.0, "smm:fake-order-doc:provider-refund")]


async def record_refund_once(calls, uid, amount, key):
    if any(row[2] == key for row in calls):
        return "already_applied"
    calls.append((uid, amount, key))
    return "applied"


def test_wallet_refund_failure_leaves_order_retryable(monkeypatch):
    order = sample_order()
    collection = FakeSmmOrders(order)
    outcomes = iter(["failed", "applied"])
    refunds = []

    async def flaky_refund(uid, amount, key):
        refunds.append((uid, amount, key))
        return next(outcomes)

    monkeypatch.setattr(flows, "smm_orders", collection)
    monkeypatch.setattr(flows, "qc", provider_refund)
    monkeypatch.setattr(flows, "refund_once", flaky_refund)

    first, got_first = run(flows.smm_sync_order(order))
    assert got_first == 0.0
    assert first["refunded"] is False
    assert first["refund_amount"] == 0.0

    second, got_second = run(flows.smm_sync_order(first))
    assert got_second == 6.0
    assert second["refunded"] is True
    assert second["refund_amount"] == 6.0
    assert len(refunds) == 2


def test_already_applied_wallet_refund_repairs_order_state_without_recredit(monkeypatch):
    order = sample_order()
    collection = FakeSmmOrders(order)
    monkeypatch.setattr(flows, "smm_orders", collection)
    monkeypatch.setattr(flows, "qc", provider_refund)
    monkeypatch.setattr(flows, "refund_once", lambda *args: value("already_applied"))

    fresh, got = run(flows.smm_sync_order(order))

    assert got == 0.0
    assert fresh["refunded"] is True
    assert fresh["refund_amount"] == 6.0


async def value(result):
    return result
