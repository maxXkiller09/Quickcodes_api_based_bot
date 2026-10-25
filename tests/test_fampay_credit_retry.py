"""Fake-database tests for retry-safe FamPay credits; no live payment calls."""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import fampay
import payments


def run(coro):
    return asyncio.run(coro)


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, branch) for branch in expected):
                return False
        elif isinstance(expected, dict) and "$in" in expected:
            if row.get(key) not in expected["$in"]:
                return False
        elif isinstance(expected, dict) and "$ne" in expected:
            if row.get(key) == expected["$ne"]:
                return False
        elif row.get(key) != expected:
            return False
    return True


class FakeOrders:
    def __init__(self, row):
        self.row = dict(row)

    async def find_one(self, query):
        return dict(self.row) if matches(self.row, query) else None

    async def update_one(self, query, update, **kwargs):
        if matches(self.row, query):
            self.row.update(update.get("$set", {}))


class FakeTxns:
    def __init__(self):
        self.rows = []

    async def find_one(self, query):
        for row in self.rows:
            if matches(row, query):
                return dict(row)
        return None

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if matches(row, query):
                row.update(update.get("$set", {}))
                row.update(update.get("$setOnInsert", {})) if False else None
                return
        if upsert:
            self.rows.append(dict(update.get("$setOnInsert", {})))


class FakeClaims:
    def __init__(self, initial=()):
        self.rows = {row["_id"]: dict(row) for row in initial}

    async def find_one(self, query):
        row = self.rows.get(query.get("_id"))
        return dict(row) if row else None


class FakeUsers:
    async def find_one(self, query):
        return {"_id": query["_id"], "balance": 37.5}


def setup(monkeypatch, credit_results=("applied",), initial_claims=()):
    order = {"_id": "order-123", "user_id": 7, "username": "tester",
             "full_name": "Test User", "amount": 25.0, "status": "pending"}
    orders, txns = FakeOrders(order), FakeTxns()
    claims, users = FakeClaims(initial_claims), FakeUsers()
    results = iter(credit_results)
    keys = []

    async def claim_ids(ids, meta):
        if any(key in claims.rows for key in ids):
            return False
        for key in ids:
            claims.rows[key] = {"_id": key, **meta}
        return True

    async def credit_once(uid, amount, key):
        keys.append((uid, amount, key))
        return next(results)

    monkeypatch.setattr(fampay, "orders", orders)
    monkeypatch.setattr(fampay, "txns", txns)
    monkeypatch.setattr(fampay, "users", users)
    monkeypatch.setattr(fampay.payments, "used_utrs", claims)
    monkeypatch.setattr(fampay.payments, "claim_ids", claim_ids)
    monkeypatch.setattr(fampay.payments, "credit_once", credit_once)
    monkeypatch.setattr(fampay, "utcnow", lambda: "test-time")
    return order, orders, txns, claims, keys


def test_fampay_credits_with_stable_key_and_finalizes(monkeypatch):
    order, orders, txns, claims, keys = setup(monkeypatch)
    status, balance = run(fampay.credit(
        order, "123456789012", {"utr": "123456789012", "transaction_id": "BANKTXN1"}
    ))
    assert status == "ok"
    assert balance == 37.5
    assert orders.row["status"] == "paid"
    assert txns.rows[0]["status"] == "approved"
    assert keys == [(7, 25.0, "fampay:order-123")]
    assert claims.rows["123456789012"]["order_id"] == "order-123"


def test_fampay_retry_after_uncertain_credit_does_not_double_credit(monkeypatch):
    order, orders, txns, _, keys = setup(monkeypatch, credit_results=("failed", "already_applied"))
    first, _ = run(fampay.credit(
        order, "123456789012", {"utr": "123456789012", "transaction_id": "BANKTXN1"}
    ))
    assert first == "error"
    assert orders.row["status"] == "credit_pending"
    assert txns.rows[0]["status"] == "credit_pending"

    second, _ = run(fampay.credit(
        order, "123456789012", {"utr": "123456789012", "transaction_id": "BANKTXN1"}
    ))
    assert second == "ok"
    assert orders.row["status"] == "paid"
    assert txns.rows[0]["status"] == "approved"
    assert keys == [(7, 25.0, "fampay:order-123"), (7, 25.0, "fampay:order-123")]
    assert len(txns.rows) == 1


def test_fampay_rejects_utr_claimed_by_another_order(monkeypatch):
    order, orders, txns, _, keys = setup(
        monkeypatch,
        initial_claims=[{"_id": "123456789012", "source": "fampay", "order_id": "other-order"}],
    )
    status, _ = run(fampay.credit(
        order, "123456789012", {"utr": "123456789012", "transaction_id": "BANKTXN1"}
    ))
    assert status == "claimed"
    assert orders.row["status"] == "pending"
    assert txns.rows == []
    assert keys == []
