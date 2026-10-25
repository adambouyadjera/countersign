"""POST /api/audit/ask: a clear 504 after the server-side limit, and Elastic's 429 passed through."""

import asyncio
import dataclasses

import httpx
import pytest

from app.adapters.elastic_agent import AuditUnavailableError
from app.api import audit as audit_api
from app.config import Settings
from app.contract_models import AuditAnswer, AuditStep
from app.main import create_app


def application(tmp_path):
    return create_app(Settings(
        _env_file=None, nessie_mode="offline", elastic_mode="offline", extractor_mode="offline",
        planner_mode="offline", agent_mode="offline", mutator_mode="offline",
        nessie_api_key="", elastic_api_key="", anthropic_api_key="", gemini_api_key="",
        bitdeer_api_key="", naive_api_key="", mutator_api_key="",
    ), db_path=tmp_path / "audit.sqlite")


class FakeSink:
    mode = "live"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.cancelled = False

    async def ask(self, request):
        try:
            return await self.behaviour()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def ask(app, sink):
    app.state.adapters = dataclasses.replace(app.state.adapters, audit=sink)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/audit/ask", json={"question": "Who approved what today?"})


@pytest.mark.asyncio
async def test_slow_auditor_gets_a_clear_504_and_the_call_is_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_api, "AUDIT_ASK_TIMEOUT_SECONDS", 0.05)
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        sink = FakeSink(lambda: asyncio.sleep(5))
        response = await ask(app, sink)
    assert response.status_code == 504
    assert response.json() == {"error": {"code": "service_unavailable", "message": audit_api.TIMEOUT_MESSAGE}}
    assert "140 s" in audit_api.TIMEOUT_MESSAGE and sink.cancelled


@pytest.mark.asyncio
async def test_elastic_rate_limit_is_reported_as_429_not_a_generic_outage(tmp_path):
    async def throttled():
        raise AuditUnavailableError("Elastic Agent Builder converse request failed.", http_status=429)

    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        response = await ask(app, FakeSink(throttled))
    assert response.status_code == 429
    assert response.json()["error"] == {"code": "rate_limited", "message": audit_api.RATE_LIMIT_MESSAGE}


@pytest.mark.asyncio
async def test_other_failures_stay_503_and_answers_are_unchanged(tmp_path):
    async def broken():
        raise AuditUnavailableError("Elastic Agent Builder returned invalid JSON.", http_status=200)

    async def answered():
        return AuditAnswer(answer="ok", agent_id="countersign-audit-analyst",
                           steps=[AuditStep(tool_id="run_summary", esql="FROM countersign-events", row_count=1)])

    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        failed = await ask(app, FakeSink(broken))
        ok = await ask(app, FakeSink(answered))
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "service_unavailable"
    assert ok.status_code == 200 and ok.json()["steps"][0]["tool_id"] == "run_summary"
