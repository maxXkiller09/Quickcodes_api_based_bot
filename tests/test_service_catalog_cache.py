"""Regression tests for provider service catalog caching."""

import asyncio
import os
import time

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")

import flows


def test_s2_country_list_is_cached_for_configured_ttl(monkeypatch):
    calls = []

    async def fake_qc(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"ok": True, "countries": [{"country_code": "IN"}]}

    monkeypatch.setattr(flows, "qc", fake_qc)
    monkeypatch.setattr(flows, "explain", _unexpected_explain)
    monkeypatch.setattr(flows, "_s2", {"t": 0.0, "rows": None})

    async def run():
        first = await flows.s2_rows()
        second = await flows.s2_rows()
        assert first == second == [{"country_code": "IN"}]
        assert len(calls) == 1

    asyncio.run(run())


def test_s3_catalog_is_cached_and_avoids_repeat_api_calls(monkeypatch):
    calls = []

    async def fake_qc(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"ok": True, "services": [{"service": 7, "name": "Instagram"}], "total": 1}

    monkeypatch.setattr(flows, "qc", fake_qc)
    monkeypatch.setattr(flows, "explain", _unexpected_explain)
    monkeypatch.setattr(flows, "_cat3", {"t": 0.0, "v": None})

    async def run():
        first = await flows.s3_catalog()
        second = await flows.s3_catalog()
        assert first == second == {"7": {"name": "Instagram"}}
        assert len(calls) == 1

    asyncio.run(run())


def test_smm_catalog_is_cached_and_avoids_repeat_api_calls(monkeypatch):
    calls = []

    async def fake_qc(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {
            "ok": True,
            "services": [{
                "service": 11, "name": "Instagram Likes", "app": "instagram",
                "category": "Likes", "rate": 1.25, "type": "Default",
            }],
            "total": 1,
        }

    monkeypatch.setattr(flows, "qc", fake_qc)
    monkeypatch.setattr(flows, "explain", _unexpected_explain)
    monkeypatch.setattr(flows, "_smm", {"t": 0.0, "v": None})

    async def run():
        first = await flows.smm_catalog()
        second = await flows.smm_catalog()
        assert first == second
        assert "11" in second["services"]
        assert len(calls) == 1

    asyncio.run(run())


async def _unexpected_explain(*args, **kwargs):
    raise AssertionError("explain() should not be called for successful fake API responses")
