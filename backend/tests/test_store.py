"""Offline storage lifecycle, reset, and atomic sequence checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3

import pytest
from pydantic import ValidationError

from app.contract_models import Run
from app.store import Store


def run_record(run_id="run-test"):
    return Run(
        run_id=run_id,
        batch_id="batch-test",
        instruction="Test storage persistence",
        created_at=datetime.now(timezone.utc),
        lanes=[{"lane": "countersign", "status": "running"}],
    )


def event_record(**overrides):
    return {
        "type": "lane.status",
        "lane": "countersign",
        "mode": "offline",
        "payload": {"status": "running"},
        **overrides,
    }


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "data" / "countersign.db")
    store.initialize()
    yield store
    store.close()


def test_initialization_creates_tables_and_session_survives_reopen(tmp_path):
    path = tmp_path / "data" / "countersign.db"
    store = Store(path)
    store.initialize()
    store.initialize()
    session_started_at = store.session_started_at
    saved_run = store.save_run(run_record())
    store.append_event(saved_run.run_id, event_record())
    store.close()

    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables >= {
        "vendors", "purchase_orders", "documents", "batches", "runs", "events",
        "decisions", "approvals", "transfers", "redteam_jobs", "metadata",
    }

    reopened = Store(path)
    reopened.initialize()
    try:
        assert reopened.session_started_at == session_started_at
        assert reopened.get_run(saved_run.run_id) == saved_run
        assert reopened.append_event(saved_run.run_id, event_record()).seq == 2
        assert reopened.get_run("unknown") is None
    finally:
        reopened.close()


def test_reset_clears_only_run_execution_state_and_persists_session(store):
    original_session = store.session_started_at
    run = store.save_run(run_record())
    store.append_event(run.run_id, event_record())
    for table, record_id in (("decisions", "decision-test"), ("approvals", "approval-test")):
        store.save_record(table, record_id, {"run_id": run.run_id})
    preserved = ("vendors", "purchase_orders", "documents", "batches", "transfers", "redteam_jobs")
    for table in preserved:
        store.save_record(table, "preserved", {"source": "test"})

    new_session = store.reset_demo()

    assert new_session > original_session
    assert store.session_started_at == new_session
    assert store.get_run(run.run_id) is None
    assert store.list_events(run.run_id) == []
    assert store.list_records("decisions") == []
    assert store.list_records("approvals") == []
    for table in preserved:
        assert store.get_record(table, "preserved") == {"source": "test"}
    path = store.path
    store.close()
    store.initialize()
    assert store.path == path
    assert store.session_started_at == new_session


def test_invalid_event_does_not_consume_sequence_or_persist(store):
    assert store.append_event("run-test", event_record()).seq == 1
    with pytest.raises(ValidationError):
        store.append_event("run-test", event_record(payload={"status": "invalid"}))
    with pytest.raises(ValidationError):
        store.append_event("run-test", event_record(type="unknown.event"))
    assert store.append_event("run-test", event_record()).seq == 2
    assert [event.seq for event in store.list_events("run-test")] == [1, 2]


def test_sequence_allocation_is_atomic_across_connections(tmp_path):
    path = tmp_path / "concurrent.db"
    stores = [Store(path), Store(path)]
    for store in stores:
        store.initialize()
    try:
        def publish(index):
            return stores[index % 2].append_event("shared-run", event_record()).seq

        with ThreadPoolExecutor(max_workers=8) as executor:
            sequences = list(executor.map(publish, range(40)))
        assert sorted(sequences) == list(range(1, 41))
        assert [event.seq for event in stores[0].list_events("shared-run", 37)] == [38, 39, 40]
        assert stores[1].append_event("another-run", event_record()).seq == 1
    finally:
        for store in stores:
            store.close()


def test_offline_audit_persistence_is_idempotent_and_rejects_conflicts(store):
    original = store.append_event("run-test", event_record())
    assert store.persist_audit_event(original) == original
    assert store.persist_audit_event(original.model_dump(mode="json")) == original
    assert len(store.list_events("run-test")) == 1

    conflicting = original.model_dump(mode="json")
    conflicting["payload"]["status"] = "completed"
    with pytest.raises(ValueError, match="Conflicting event"):
        store.persist_audit_event(conflicting)
    with pytest.raises(ValueError, match="mode='offline'"):
        store.persist_audit_event(original.model_copy(update={"mode": "live"}))

    fresh = original.model_dump(mode="json")
    fresh["run_id"] = "audit-only-run"
    assert store.persist_audit_event(fresh).seq == 1
    assert store.append_event("audit-only-run", event_record()).seq == 2
    fresh["seq"] = 4
    with pytest.raises(ValueError, match="next per-run sequence"):
        store.persist_audit_event(fresh)


def test_run_validation_and_table_allowlist(store):
    with pytest.raises(ValidationError):
        store.save_run({"run_id": "incomplete"})
    with pytest.raises(ValidationError):
        store.save_record("runs", "incomplete", {"run_id": "incomplete"})
    with pytest.raises(ValueError, match="must match"):
        store.save_record("runs", "wrong-id", run_record())
    with pytest.raises(ValueError, match="Unsupported record table"):
        store.get_record("runs; DROP TABLE runs", "anything")
    with pytest.raises(ValueError, match="does not support"):
        store.list_records("vendors", run_id="run-test")
    assert store.list_records("runs") == []
