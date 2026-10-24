"""Offline tests for service boundaries, health probes, caching, and audit writes."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import logging

import httpx
from pydantic import TypeAdapter
import pytest

from app.adapters import (
    AgentLLM, AuditSink, BankAdapter, ExtractorLLM, HealthChecker,
    MutatorLLM, PlannerLLM, build_adapters,
)
from app.config import Settings
from app.contract_models import AuditAskRequest, RunEvent
from app.store import Store


def settings(**changes) -> Settings:
    # Explicit values prevent tests from ever reading credentials from .env or
    # making network calls because of credentials present in the test process.
    values = dict(
        _env_file=None,
        nessie_api_key="", elastic_api_key="", anthropic_api_key="",
        gemini_api_key="", bitdeer_api_key="",
        nessie_mode="live", elastic_mode="live",
        nessie_base_url="https://nessie.invalid",
        elastic_url="https://elastic.invalid",
        bitdeer_base_url="https://bitdeer.invalid/v1",
    )
    values.update(changes)
    return Settings(**values)


@pytest.fixture
def store():
    instance = Store(":memory:")
    instance.initialize()
    yield instance
    instance.close()


def no_requests(request: httpx.Request) -> httpx.Response:
    pytest.fail("A missing-key or offline service must not perform an HTTP request")


@pytest.mark.asyncio
async def test_missing_keys_are_reported_without_network_and_protocols_match(store):
    adapters = build_adapters(settings(), store, transport=httpx.MockTransport(no_requests))
    assert isinstance(adapters.bank, BankAdapter)
    assert isinstance(adapters.audit, AuditSink)
    assert isinstance(adapters.extractor, ExtractorLLM)
    assert isinstance(adapters.planner, PlannerLLM)
    assert isinstance(adapters.agent, AgentLLM)
    assert isinstance(adapters.mutator, MutatorLLM)
    result = await HealthChecker(adapters).check()
    assert set(result.model_dump()) == {"nessie", "elastic", "anthropic", "gemini", "bitdeer"}
    for name, health in result.model_dump().items():
        assert health["status"] == "missing_key"
        assert health["mode"] == ("live" if name in {"nessie", "elastic"} else "offline")
        assert "not configured" in health["detail"]
    assert adapters.mutator.mode == "offline"
    with pytest.raises(NotImplementedError):
        await adapters.mutator.mutate(template="synthetic", placement="footer")


@pytest.mark.asyncio
async def test_explicit_offline_modes_do_not_probe_even_with_keys(store):
    adapters = build_adapters(
        settings(
            nessie_mode="offline", elastic_mode="offline",
            nessie_api_key="synthetic-nessie-key", elastic_api_key="synthetic-elastic-key",
        ),
        store, transport=httpx.MockTransport(no_requests),
    )
    result = await HealthChecker(adapters).check()
    assert result.nessie.status == result.elastic.status == "offline"
    assert result.nessie.mode == result.elastic.mode == "offline"
    assert "SQLite only" in result.elastic.detail
    with pytest.raises(NotImplementedError):
        await adapters.bank.transfer(from_account_id="from", to_account_id="to", amount_cents=1)


def all_live_settings() -> Settings:
    return settings(
        nessie_api_key="synthetic-nessie-key", elastic_api_key="synthetic-elastic-key",
        anthropic_api_key="synthetic-anthropic-key", gemini_api_key="synthetic-gemini-key",
        bitdeer_api_key="synthetic-bitdeer-key",
    )


def success_response(request: httpx.Request) -> httpx.Response:
    if request.url.host == "nessie.invalid":
        assert request.url.path == "/customers"
        assert request.url.params["key"] == "synthetic-nessie-key"
        body = []
    elif request.url.host == "elastic.invalid":
        assert request.url.path == "/_security/_authenticate"
        assert request.headers["Authorization"] == "ApiKey synthetic-elastic-key"
        body = {"username": "synthetic-test-user"}
    elif request.url.host == "api.anthropic.com":
        assert request.url.path == "/v1/models"
        assert request.headers["x-api-key"] == "synthetic-anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = {"data": []}
    elif request.url.host == "generativelanguage.googleapis.com":
        assert request.url.path == "/v1beta/models"
        assert request.headers["x-goog-api-key"] == "synthetic-gemini-key"
        body = {"models": []}
    else:
        assert request.url.host == "bitdeer.invalid"
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer synthetic-bitdeer-key"
        body = {"data": []}
    assert request.method == "GET"
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_live_read_only_probes_are_cached_thirty_seconds_and_single_flight(store):
    calls = Counter()
    now = [100.0]

    def probe(request):
        calls[request.url.host] += 1
        return success_response(request)

    adapters = build_adapters(all_live_settings(), store, transport=httpx.MockTransport(probe))
    checker = HealthChecker(adapters, clock=lambda: now[0])
    results = await asyncio.gather(*(checker.check() for _ in range(8)))
    assert all(value.status == "ok" for value in results[0].__dict__.values())
    assert len(calls) == 5
    assert set(calls.values()) == {1}

    # Callers cannot mutate the cached object through the previous result.
    results[0].nessie.status = "error"
    now[0] += 29.9
    assert (await checker.check()).nessie.status == "ok"
    assert set(calls.values()) == {1}
    now[0] += 0.1
    await asyncio.gather(*(checker.check() for _ in range(5)))
    assert set(calls.values()) == {2}
    assert adapters.agent.mode == adapters.mutator.mode == "live"


@pytest.mark.asyncio
async def test_probe_failure_is_sanitized_cached_and_never_switches_mode(store):
    calls = 0

    def probe(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("synthetic-secret must not escape", request=request)

    adapters = build_adapters(
        settings(nessie_api_key="synthetic-secret"), store,
        transport=httpx.MockTransport(probe),
    )
    checker = HealthChecker(adapters)
    result = await checker.check()
    assert result.nessie.status == "error"
    assert result.nessie.mode == adapters.bank.mode == "live"
    assert "timed out" in result.nessie.detail
    assert "synthetic-secret" not in result.model_dump_json()
    assert "synthetic-secret" not in repr(adapters.bank)
    await checker.check()
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(401, {"message": "synthetic-secret"}), (200, {}), (200, "not-json")])
async def test_bad_live_responses_are_errors_without_response_body_leaks(store, status, body):
    def probe(request):
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    adapters = build_adapters(
        settings(nessie_api_key="synthetic-secret"), store,
        transport=httpx.MockTransport(probe),
    )
    health = await adapters.bank.health_check()
    assert health.status == "error"
    assert health.mode == "live"
    assert "synthetic-secret" not in health.model_dump_json()


@pytest.mark.asyncio
async def test_nessie_query_key_is_redacted_from_httpx_logs(store, caplog):
    adapters = build_adapters(
        settings(nessie_api_key="synthetic-nessie-key"), store,
        transport=httpx.MockTransport(success_response),
    )
    with caplog.at_level(logging.INFO, logger="httpx"):
        await adapters.bank.health_check()
    assert "[REDACTED]" in caplog.text
    assert "synthetic-nessie-key" not in caplog.text


def event(mode="live"):
    return TypeAdapter(RunEvent).validate_python({
        "run_id": "test-run", "seq": 1, "ts": datetime.now(timezone.utc),
        "type": "error", "lane": "system", "mode": mode,
        "payload": {"message": "Synthetic test event", "recoverable": True},
    })


@pytest.mark.asyncio
async def test_offline_audit_writes_only_sqlite_and_labels_events(store):
    adapters = build_adapters(
        settings(elastic_mode="offline"), store,
        transport=httpx.MockTransport(no_requests),
    )
    original = event()
    saved = await adapters.audit.write_event(original)
    assert original.mode == "live"
    assert saved.mode == "offline"
    assert saved.seq == original.seq
    assert saved.ts == original.ts
    assert store.list_events("test-run") == [saved]
    assert await adapters.audit.write_event(original) == saved
    assert store.list_events("test-run") == [saved]
    with pytest.raises(NotImplementedError):
        await adapters.audit.ask(AuditAskRequest(question="Synthetic question"))


@pytest.mark.asyncio
async def test_offline_audit_cannot_rewrite_previously_published_live_event(store):
    adapters = build_adapters(settings(elastic_mode="offline"), store)
    published = store.append_event("test-run", event())
    with pytest.raises(ValueError, match="Conflicting"):
        await adapters.audit.write_event(published)
    assert store.list_events("test-run") == [published]
