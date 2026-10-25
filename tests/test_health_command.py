"""Tests for the owner health summary; no live database or providers are contacted."""
import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

import admin


def run(coro):
    return asyncio.run(coro)


class FakeAdmin:
    def __init__(self, fail=False):
        self.fail = fail

    async def command(self, name):
        assert name == "ping"
        if self.fail:
            raise RuntimeError("fake DB failure")
        return {"ok": 1}


class FakeClient:
    def __init__(self, fail=False):
        self.admin = FakeAdmin(fail)


def test_health_snapshot_reports_configured_components_without_secrets(monkeypatch):
    monkeypatch.setattr(admin, "_client", FakeClient())
    async def settings():
        return {
            "api_keys": {"s2": "secret-a", "s3": "secret-b", "smm": ""},
            "UTR_API_MAIL": "mail@example.invalid",
            "UTR_API_PASS": "secret-pass",
            "MANUAL_UPI_ID": "test@bank",
            "MANUAL_QR_FILE_ID": None,
        }
    monkeypatch.setattr(admin, "get_settings", settings)
    result = run(admin.health_snapshot())
    assert "MongoDB: 🟢 reachable" in result
    assert "Server 2 API key: 🟢 configured" in result
    assert "Server 3 API key: 🟢 configured" in result
    assert "SMM API key: 🟡 missing" in result
    assert "UTR verification credentials: 🟢 configured" in result
    assert "secret-a" not in result
    assert "secret-pass" not in result
    assert "not live-tested" in result


def test_health_snapshot_keeps_reporting_when_database_ping_fails(monkeypatch):
    monkeypatch.setattr(admin, "_client", FakeClient(fail=True))
    async def settings():
        return {"api_keys": {}, "UTR_API_MAIL": "", "UTR_API_PASS": ""}
    monkeypatch.setattr(admin, "get_settings", settings)
    result = run(admin.health_snapshot())
    assert "MongoDB: 🔴 unreachable" in result
    assert "Server 2 API key: 🟡 missing" in result
    assert "UTR verification credentials: 🟡 missing" in result
