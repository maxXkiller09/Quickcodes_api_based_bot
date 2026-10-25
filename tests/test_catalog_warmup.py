"""Tests for background catalog warmup using fake catalog loaders."""

import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")

import flows


def test_warmup_loads_only_configured_services(monkeypatch):
    calls = []

    async def fake_settings():
        return {"api_keys": {"s2": "key-2", "s3": "", "smm": "key-smm"}}

    async def fake_s2():
        calls.append("s2")
        await asyncio.sleep(0)
        return ["country"]

    async def fake_s3():
        calls.append("s3")
        return {"catalog": True}

    async def fake_smm():
        calls.append("smm")
        await asyncio.sleep(0)
        return {"services": {}}

    monkeypatch.setattr(flows, "get_settings", fake_settings)
    monkeypatch.setattr(flows, "s2_rows", fake_s2)
    monkeypatch.setattr(flows, "s3_catalog", fake_s3)
    monkeypatch.setattr(flows, "smm_catalog", fake_smm)

    result = asyncio.run(flows.warm_catalogs_once())

    assert set(calls) == {"s2", "smm"}
    assert result == {"s2": ["country"], "smm": {"services": {}}}


def test_warmup_handles_one_loader_failure_without_blocking_others(monkeypatch):
    async def fake_settings():
        return {"api_keys": {"s2": "key-2", "s3": "key-3", "smm": "key-smm"}}

    async def fail_s2():
        raise RuntimeError("fake provider failure")

    async def fake_s3():
        return {"3": {"name": "Instagram"}}

    async def fake_smm():
        return {"services": {"1": {"name": "Likes"}}}

    monkeypatch.setattr(flows, "get_settings", fake_settings)
    monkeypatch.setattr(flows, "s2_rows", fail_s2)
    monkeypatch.setattr(flows, "s3_catalog", fake_s3)
    monkeypatch.setattr(flows, "smm_catalog", fake_smm)

    result = asyncio.run(flows.warm_catalogs_once())

    assert result["s2"] is None
    assert result["s3"] == {"3": {"name": "Instagram"}}
    assert result["smm"] == {"services": {"1": {"name": "Likes"}}}


def test_warmup_skips_all_services_without_configured_keys(monkeypatch):
    async def fake_settings():
        return {"api_keys": {"s2": "", "s3": "", "smm": ""}}

    async def unexpected():
        raise AssertionError("no loader should run without an API key")

    monkeypatch.setattr(flows, "get_settings", fake_settings)
    monkeypatch.setattr(flows, "s2_rows", unexpected)
    monkeypatch.setattr(flows, "s3_catalog", unexpected)
    monkeypatch.setattr(flows, "smm_catalog", unexpected)

    assert asyncio.run(flows.warm_catalogs_once()) == {}
