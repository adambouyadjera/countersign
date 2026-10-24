"""Exercise HTTP and the actual streaming ASGI response without opening sockets."""

import asyncio
import json

from fastapi.testclient import TestClient
import httpx
from pydantic import TypeAdapter
import pytest

from app.config import Settings
from app.contract_models import ErrorResponse, Health, ResetResponse, Run, RunEvent
from app.main import create_app


@pytest.fixture
def application(tmp_path, monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    settings = Settings(_env_file=None, nessie_mode="offline", elastic_mode="offline", extractor_mode="offline", planner_mode="offline", agent_mode="offline", mutator_mode="offline")
    return create_app(settings, db_path=tmp_path / "data" / "test.db")


def save_run(app):
    run = Run(
        run_id="test-run", batch_id="test-batch", instruction="test instruction",
        created_at="2026-10-23T00:00:00Z", lanes=[],
    )
    app.state.store.save_run(run)
    return run.run_id


def test_health_lists_every_service_and_cors(application):
    with TestClient(application) as client:
        response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200
        health = Health.model_validate(response.json())
        assert set(health.services.model_dump()) == {"nessie", "elastic", "anthropic", "gemini", "bitdeer", "mutator"}
        for service in health.services.model_dump().values():
            assert service["status"] in {"ok", "offline", "missing_key", "error"}
            assert service["mode"] == "offline"
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        preflight = client.options(
            "/api/runs/test-run/events",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "Last-Event-ID"},
        )
        assert preflight.status_code == 200
        assert "last-event-id" in preflight.headers["access-control-allow-headers"].lower()


@pytest.mark.asyncio
async def test_reset_clears_runs_events_decisions_approvals_and_updates_session(application):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        original_session = application.state.store.session_started_at
        await application.state.event_bus.publish(run_id, {"type": "run.completed", "lane": "system", "payload": {"tallies": {}}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            response = await client.post("/api/demo/reset")
            assert response.status_code == 200
            reset = ResetResponse.model_validate(response.json())
            assert reset.session_started_at > original_session
            assert application.state.store.get_run(run_id) is None
            assert application.state.store.list_events(run_id) == []
            health = Health.model_validate((await client.get("/api/health")).json())
            assert health.session_started_at == reset.session_started_at


class ASGIStream:
    """Drive an unbounded stream until the test explicitly disconnects."""

    def __init__(self, app, run_id, last_seq=None):
        self.app = app
        self.run_id = run_id
        self.last_seq = last_seq
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.buffer = b""

    async def __aenter__(self):
        headers = [(b"last-event-id", str(self.last_seq).encode())] if self.last_seq is not None else []
        path = f"/api/runs/{self.run_id}/events"
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": path, "raw_path": path.encode(), "query_string": b"",
            "headers": headers, "client": ("127.0.0.1", 1234), "server": ("test", 80),
            "root_path": "",
        }
        await self.incoming.put({"type": "http.request", "body": b"", "more_body": False})
        self.task = asyncio.create_task(self.app(scope, self.incoming.get, self.outgoing.put))
        start = await asyncio.wait_for(self.outgoing.get(), timeout=2)
        assert start["type"] == "http.response.start"
        assert start["status"] == 200
        self.headers = dict(start["headers"])
        return self

    async def frame(self, timeout=2):
        while b"\r\n\r\n" not in self.buffer:
            message = await asyncio.wait_for(self.outgoing.get(), timeout=timeout)
            assert message["type"] == "http.response.body"
            assert message.get("more_body"), "SSE closed before client disconnect"
            self.buffer += message.get("body", b"")
        frame, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        return frame.decode()

    async def event(self):
        frame = await self.frame()
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        data = json.loads(fields["data"])
        event = TypeAdapter(RunEvent).validate_python(data)
        assert fields["id"] == str(event.seq)
        assert fields["event"] == event.type
        return event

    async def __aexit__(self, *args):
        await self.incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(self.task, timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("last_seq", [None, 1, 2])
async def test_sse_replays_resumes_and_delivers_approvals_after_completed(application, last_seq):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        events = [
            {"type": "lane.status", "lane": "countersign", "payload": {"status": "running"}},
            {"type": "lane.status", "lane": "unguarded", "payload": {"status": "completed"}},
            {"type": "run.completed", "lane": "system", "payload": {"tallies": {}}},
        ]
        for event in events:
            await application.state.event_bus.publish(run_id, event)
        async with ASGIStream(application, run_id, last_seq) as stream:
            assert stream.headers[b"cache-control"] == b"no-cache"
            assert stream.headers[b"x-accel-buffering"] == b"no"
            assert stream.headers[b"content-type"].startswith(b"text/event-stream")
            replay = [await stream.event() for _ in range(3 - (last_seq or 0))]
            assert [event.seq for event in replay] == list(range((last_seq or 0) + 1, 4))
            assert replay[-1].type == "run.completed"
            published = await application.state.event_bus.publish(run_id, {
                "type": "approval.resolved", "lane": "countersign", "mode": "offline",
                "payload": {"approval_id": "test-approval", "invoice_id": "test-invoice", "decision": "reject"},
            })
            assert (await stream.event()) == published


@pytest.mark.asyncio
async def test_sse_sends_comment_heartbeat_and_waits_at_replay_cursor(application):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        async with ASGIStream(application, run_id, 0) as stream:
            assert await stream.frame(timeout=17) == ": heartbeat"
            event = await application.state.event_bus.publish(run_id, {"type": "run.completed", "lane": "system", "payload": {"tallies": {}}})
            assert (await stream.event()) == event


def test_missing_run_and_invalid_cursor_use_contract_errors(application):
    with TestClient(application) as client:
        for path, headers, status, code in [
            ("/api/runs/missing/events", {}, 404, "not_found"),
            ("/api/runs/missing/events", {"Last-Event-ID": "invalid"}, 422, "validation_error"),
            ("/api/missing", {}, 404, "not_found"),
        ]:
            response = client.get(path, headers=headers)
            assert response.status_code == status
            assert ErrorResponse.model_validate(response.json()).error.code == code


def test_unexpected_errors_hide_exception_text_and_keep_cors(application):
    @application.get("/test-failure", include_in_schema=False)
    async def fail():
        raise RuntimeError("private failure content")

    with TestClient(application) as client:
        response = client.get("/test-failure", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 500
        assert response.json() == {"error": {"code": "internal", "message": "Internal server error"}}
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_generated_openapi_describes_contract_errors_and_sse(application):
    from openapi_spec_validator import validate_spec

    schema = application.openapi()
    validate_spec(schema)
    event = schema["paths"]["/api/runs/{run_id}/events"]["get"]
    assert event["responses"]["200"]["content"]["text/event-stream"]["schema"] == {"$ref": "#/components/schemas/RunEvent"}
    assert event["responses"]["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
