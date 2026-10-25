"""Fake-database tests for idempotent Auto-UPI credit retries."""
import asyncio
import os
from bson import ObjectId

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import payments


def run(coro):
    return asyncio.run(coro)


def matches(row, query):
    """Small Mongo-like matcher for the operators used by these tests."""
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, branch) for branch in expected):
                return False
        elif isinstance(expected, dict) and "$in" in expected:
            if row.get(key) not in expected["$in"]:
                return False
        elif row.get(key) != expected:
            return False
    return True


class FakeTxns:
    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]
        self.insert_calls = 0

    async def find_one(self, query):
        for row in self.rows:
            if matches(row, query):
                return dict(row)
        return None

    async def insert_one(self, row):
        self.insert_calls += 1
        stored = dict(row)
        stored["_id"] = ObjectId()
        self.rows.append(stored)
        return type("InsertResult", (), {"inserted_id": stored["_id"]})()

    async def update_one(self, query, update):
        for row in self.rows:
            if matches(row, query):
                row.update(update["$set"])
                break


class FakeClaims:
    def __init__(self):
        self.claims = {}

    async def insert_one(self, doc):
        if doc["_id"] in self.claims:
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("duplicate")
        self.claims[doc["_id"]] = dict(doc)

    async def delete_many(self, query):
        for key in query.get("_id", {}).get("$in", []):
            self.claims.pop(key, None)

    async def find_one(self, query):
        ids = query.get("_id", {}).get("$in", [])
        return next((self.claims[i] for i in ids if i in self.claims), None)


class FakeUsers:
    def __init__(self):
        self.balance = 20.0

    async def find_one(self, query):
        return {"_id": query["_id"], "balance": self.balance}


def setup(monkeypatch, rows=(), credit_result="applied"):
    txns = FakeTxns(rows)
    claims = FakeClaims()
    users = FakeUsers()
    keys = []

    async def id_in_use(ids):
        return any(i in claims.claims for i in ids)

    async def claim_ids(ids, meta):
        if any(i in claims.claims for i in ids):
            return False
        for i in dict.fromkeys(ids):
            claims.claims[i] = {"_id": i, **meta}
        return True

    async def credit_once(uid, amount, key):
        keys.append((uid, amount, key))
        return credit_result

    monkeypatch.setattr(payments, "txns", txns)
    monkeypatch.setattr(payments, "used_utrs", claims)
    monkeypatch.setattr(payments, "users", users)
    monkeypatch.setattr(payments, "id_in_use", id_in_use)
    monkeypatch.setattr(payments, "claim_ids", claim_ids)
    monkeypatch.setattr(payments, "credit_once", credit_once)
    monkeypatch.setattr(payments, "utcnow", lambda: "test-time")
    return txns, claims, users, keys


def test_auto_upi_uses_stable_credit_key_and_finalizes(monkeypatch):
    txns, claims, _, keys = setup(monkeypatch)
    status, balance = run(payments.auto_upi_credit(
        7, "tester", "Test User", 25, "123456789012",
        {"utr": "123456789012", "transaction_id": "BANKTXN123"},
    ))
    assert status == "ok"
    assert balance == 20.0
    assert txns.rows[0]["status"] == "approved"
    assert keys == [(7, 25.0, f"auto-upi:{txns.rows[0]['_id']}")]
    assert "123456789012" in claims.claims


def test_auto_upi_retry_reuses_pending_transaction_and_credit_key(monkeypatch):
    oid = ObjectId()
    row = {"_id": oid, "user_id": 7, "amount": 25.0, "method": "auto_upi",
           "utr": "123456789012", "transaction_id": "BANKTXN123", "status": "credit_pending"}
    txns, claims, _, keys = setup(monkeypatch, rows=[row], credit_result="already_applied")
    claims.claims["123456789012"] = {"_id": "123456789012", "user_id": 7, "source": "auto_upi"}
    status, _ = run(payments.auto_upi_credit(
        7, "tester", "Test User", 25, "123456789012",
        {"utr": "123456789012", "transaction_id": "BANKTXN123"},
    ))
    assert status == "ok"
    assert txns.insert_calls == 0
    assert txns.rows[0]["status"] == "approved"
    assert keys == [(7, 25.0, f"auto-upi:{oid}")]


def test_auto_upi_failed_credit_keeps_pending_record_and_claim(monkeypatch):
    txns, claims, _, _ = setup(monkeypatch, credit_result="failed")
    status, _ = run(payments.auto_upi_credit(
        7, "tester", "Test User", 25, "123456789012",
        {"utr": "123456789012", "transaction_id": "BANKTXN123"},
    ))
    assert status == "error"
    assert txns.rows[0]["status"] == "credit_pending"
    assert "123456789012" in claims.claims
