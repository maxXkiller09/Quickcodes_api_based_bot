"""Concurrency regression tests for catalog request coalescing."""

import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")

import flows


def test_concurrent_s2_requests_share_one_provider_call(monkeypatch):
    calls = []

    async def fake_qc(*args, **kwargs):
        calls.append((args, kwargs))
        await asyncio.sleep(0.02)
        return 200, {"ok": True, "countries": [{"country_code": "IN"}]}

    async def unexpected_explain(*args, **kwargs):
        raise AssertionError("explain() should not run for a successful fake response")

    monkeypatch.setattr(flows, "qc", fake_qc)
    monkeypatch.setattr(flows, "explain", unexpected_explain)
    monkeypatch.setattr(flows, "_s2", {"t": 0.0, "rows": None})

    async def run():
        results = await asyncio.gather(flows.s2_rows(), flows.s2_rows(), flows.s2_rows())
        assert results == [[{"country_code": "IN"}]] * 3
        assert len(calls) == 1

    asyncio.run(run())


def test_unrelated_catalog_keys_can_load_concurrently(monkeypatch):
    active = 0
    peak = 0

    async def fake_qc(server, method, path, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        if path == "/v1/s3/countries":
            return 200, {"ok": True, "countries": []}
        return 200, {"ok": True, "operators": [], "country_name": "India"}

    async def unexpected_explain(*args, **kwargs):
        raise AssertionError("explain() should not run for successful fake responses")

    monkeypatch.setattr(flows, "qc", fake_qc)
    monkeypatch.setattr(flows, "explain", unexpected_explain)
    monkeypatch.setattr(flows, "_cty3", {})
    monkeypatch.setattr(flows, "_off3", {})

    async def run():
        await asyncio.gather(
            flows.s3_countries("instagram", full=False),
            flows.s3_offers("instagram", "IN"),
        )
        assert peak == 2

    asyncio.run(run())
