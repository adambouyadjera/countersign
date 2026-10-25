"""Failure notifications claim their identity atomically with the audit event."""

import asyncio
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest
import respx

from app.adapters.elastic import LiveAuditSink
from app.adapters.elastic_agent import AuditUnavailableError
from app.audit.events import INDEX_NAME
from app.contract_models import AuditAskRequest
from app.events import EventBus
from app.store import Store


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "notification.db")
    result.initialize()
    try:
        yield result
    finally:
        result.close()


def adapter(store, bus):
    result = LiveAuditSink(key=SecretStr("synthetic-elastic-key"),
                           base_url="https://elastic-notification.invalid", store=store)
    result._on_failure = bus.report_audit_failure
    return result


def notification_count(store):
    return store._db().execute("SELECT COUNT(*) FROM audit_failure_notifications").fetchone()[0]


def known_run(store, run_id):
    store.save_run({"run_id": run_id, "batch_id": "batch-synthetic", "lanes": [],
                    "instruction": "Trusted synthetic instruction", "created_at": "2026-10-24T00:00:00Z"})


@pytest.mark.asyncio
async def test_cancelled_before_bus_lock_never_consumes_a_missing_notification(store):
    bus = EventBus(store)
    sink = adapter(store, bus)
    known_run(store, "run-cancelled")
    rows = [{"run_id": "run-cancelled"}]
    await bus._lock.acquire()
    blocked = asyncio.create_task(sink._failed(rows, "Elastic audit delivery unavailable."))
    try:
        await asyncio.sleep(0)
        assert not blocked.done()
        assert notification_count(store) == 0
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
    finally:
        bus._lock.release()
    assert notification_count(store) == 0
    assert store.list_events("run-cancelled") == []
    assert store.audit_pending_count() == 0

    # The next attempt, including one in a replacement process, remains able
    # to publish the warning that the cancelled callback never committed.
    await sink._failed(rows, "Elastic audit delivery unavailable.")
    await sink._failed(rows, "Elastic audit delivery unavailable.")
    assert notification_count(store) == 1
    events = store.list_events("run-cancelled")
    assert len(events) == 1 and events[0].type == "error" and events[0].lane == "system"
    assert store.audit_pending_count() == 1


@pytest.mark.asyncio
async def test_notification_marker_and_event_rollback_together_when_append_fails(store, monkeypatch):
    bus = EventBus(store)
    sink = adapter(store, bus)
    known_run(store, "run-retry")
    original = store.append_event

    def unavailable(*args, **kwargs):
        raise RuntimeError("Synthetic event persistence failure")

    monkeypatch.setattr(store, "append_event", unavailable)
    await sink._failed([{"run_id": "run-retry"}], "Elastic audit delivery unavailable.")
    assert notification_count(store) == 0
    assert store.audit_pending_count() == 0
    assert "run-retry" not in sink._notified_runs
    monkeypatch.setattr(store, "append_event", original)
    await sink._failed([{"run_id": "run-retry"}], "Elastic audit delivery unavailable.")
    assert notification_count(store) == 1
    assert len(store.list_events("run-retry")) == 1


@pytest.mark.asyncio
async def test_independent_sink_instances_and_connections_emit_one_durable_warning(store):
    known_run(store, "run-shared")
    other = Store(store.path)
    other.initialize()
    first, second = adapter(store, EventBus(store)), adapter(other, EventBus(other))
    try:
        rows = [{"run_id": "run-shared"}]
        await asyncio.gather(first._failed(rows, "Elastic audit delivery unavailable."),
                             second._failed(rows, "Elastic audit delivery unavailable."))
        # Neither sink relies on the other sink's in-memory notification set.
        replacement = adapter(other, EventBus(other))
        await replacement._failed(rows, "Elastic audit delivery unavailable.")
        assert notification_count(store) == 1
        assert len(store.list_events("run-shared")) == 1
        assert store.audit_pending_count() == 1
    finally:
        other.close()


@pytest.mark.asyncio
async def test_atomic_failure_event_is_delivered_to_sse_and_its_own_outbox(store):
    bus = EventBus(store)
    sink = adapter(store, bus)
    known_run(store, "run-sse")
    subscription = bus.subscribe("run-sse")
    receive = asyncio.create_task(anext(subscription))
    try:
        await asyncio.sleep(0)
        await sink._failed([{"run_id": "run-sse"}], "Elastic audit delivery unavailable.")
        event = await asyncio.wait_for(receive, timeout=1)
        assert event.type == "error" and event.lane == "system" and event.mode == "live"
        assert event.payload.recoverable is True
        queued = store.pending_audit()
        assert len(queued) == 1
        assert queued[0]["document"]["payload"] == event.model_dump(mode="json", exclude_none=True)
        assert notification_count(store) == 1
        assert await bus.report_audit_failure("run-sse", "Elastic audit delivery unavailable.") is False
        assert len(store.list_events("run-sse")) == 1
    finally:
        if not receive.done():
            receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
        await subscription.aclose()


@pytest.mark.asyncio
async def test_remote_only_refresh_failure_never_creates_or_targets_a_local_sequence(store):
    bus = EventBus(store)
    sink = adapter(store, bus)
    callback = AsyncMock(side_effect=bus.report_audit_failure)
    sink._on_failure = callback
    with respx.mock as router:
        router.put(sink._base_url + "/" + INDEX_NAME).respond(200, json={"acknowledged": True})
        refresh = router.post(sink._base_url + "/" + INDEX_NAME + "/_refresh").respond(
            200, json={"_shards": {"failed": 1}})
        assert await sink.flush(run_id="run-only-in-elastic", refresh=True) is False
        assert sink.runtime_health().status == "error"
        assert callback.await_count == 0
        assert await bus.report_audit_failure("run-only-in-elastic", "Elastic unavailable.") is False
        assert not store.audit_run_known("run-only-in-elastic")
        assert notification_count(store) == 0
        assert store.list_events("run-only-in-elastic") == []
        assert store.audit_pending_count() == 0
        refresh.respond(200, json={"_shards": {"failed": 0}})
        assert await sink.flush(refresh=True)
        assert not any(call.request.url.path == "/_bulk" for call in router.calls)


@pytest.mark.asyncio
async def test_known_run_refresh_failure_can_append_its_first_local_error(store):
    known_run(store, "run-local")
    sink = adapter(store, EventBus(store))
    with respx.mock as router:
        router.put(sink._base_url + "/" + INDEX_NAME).respond(200, json={"acknowledged": True})
        router.post(sink._base_url + "/" + INDEX_NAME + "/_refresh").respond(
            200, json={"_shards": {"failed": 1}})
        assert await sink.flush(run_id="run-local", refresh=True) is False
    event, = store.list_events("run-local")
    assert event.seq == 1 and event.lane == "system" and event.type == "error"
    assert notification_count(store) == 1


@pytest.mark.asyncio
async def test_same_database_reset_retains_safe_sequence_for_failure_notifications(store):
    original = store.append_event("run-reset", {"type": "error", "lane": "system", "mode": "live",
        "payload": {"message": "Synthetic pre-reset event", "recoverable": True}})
    store.acknowledge_audit([f"run-reset:{original.seq}"])
    store.reset_demo()
    assert store.get_run("run-reset") is None and store.list_events("run-reset") == []
    assert store.audit_run_known("run-reset")
    sink = adapter(store, EventBus(store))
    with respx.mock as router:
        router.put(sink._base_url + "/" + INDEX_NAME).respond(200, json={"acknowledged": True})
        router.post(sink._base_url + "/" + INDEX_NAME + "/_refresh").respond(
            200, json={"_shards": {"failed": 1}})
        assert await sink.flush(run_id="run-reset", refresh=True) is False
    later, = store.list_events("run-reset")
    assert later.seq == original.seq + 1
    assert store.pending_audit()[0]["event_id"] == f"run-reset:{later.seq}"
    replacement = adapter(store, EventBus(store))
    await replacement._failed([{"run_id": "run-reset"}], "Elastic still unavailable.")
    assert len(store.list_events("run-reset")) == notification_count(store) == 1


@pytest.mark.asyncio
async def test_converse_failure_for_local_run_emits_one_safe_durable_error(store):
    known_run(store, "run-converse")
    sink = adapter(store, EventBus(store))
    sink.agent_client = type("Agent", (), {"ask": AsyncMock(
        side_effect=AuditUnavailableError("Synthetic private upstream body"))})()
    with respx.mock as router:
        router.put(sink._base_url + "/" + INDEX_NAME).respond(200, json={"acknowledged": True})
        router.post(sink._base_url + "/" + INDEX_NAME + "/_refresh").respond(200, json={})
        router.post(sink._base_url + "/_bulk").respond(200, json={"errors": False,
            "items": [{"index": {"status": 201}}]})
        request = AuditAskRequest(question="What happened?", run_id="run-converse")
        for _ in range(2):
            with pytest.raises(AuditUnavailableError):
                await sink.ask(request)
        assert sink.agent_client.ask.await_count == 2
    event, = store.list_events("run-converse")
    assert event.type == "error" and event.lane == "system"
    assert event.payload.message == "Elastic Agent Builder audit is unavailable."
    assert "private" not in event.model_dump_json()
    assert notification_count(store) == 1
    assert sink.runtime_health().status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["run-remote-only", None])
async def test_converse_failure_without_local_context_does_not_create_a_run(store, run_id):
    bus = EventBus(store)
    sink = adapter(store, bus)
    sink._on_failure = AsyncMock(side_effect=bus.report_audit_failure)
    sink.agent_client = type("Agent", (), {"ask": AsyncMock(
        side_effect=AuditUnavailableError("Synthetic provider failure"))})()
    with respx.mock as router:
        router.put(sink._base_url + "/" + INDEX_NAME).respond(200, json={"acknowledged": True})
        router.post(sink._base_url + "/" + INDEX_NAME + "/_refresh").respond(200, json={})
        with pytest.raises(AuditUnavailableError):
            await sink.ask(AuditAskRequest(question="Historical audit?",
                **({"run_id": run_id} if run_id is not None else {})))
    assert sink.runtime_health().status == "error"
    assert sink._on_failure.await_count == 0
    assert notification_count(store) == store.audit_pending_count() == 0
    if run_id is not None:
        assert not store.audit_run_known(run_id)
        assert store.list_events(run_id) == []
