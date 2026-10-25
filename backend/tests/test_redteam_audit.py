"""Finalized case audit export keeps financial trials and original evidence intact."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.contract_models import Run
from app.events import EventBus
from app.redteam.audit import export_finalized_case_audit
from app.store import Store


RUN_ID = "run-audit-redteam-source"
INVOICE_ID = "redteam-fixed-invoice"
DOCUMENT_ID = "redteam-fixed-document"


def make_source(store, *, finalize=True):
    # Repeated entity IDs are deliberate. Main runtime entities must neither
    # overwrite the original projection nor receive a copy from the case.
    store.save_record("batches", "case-batch", {"batch_id": "case-batch", "invoices": [{
        "invoice_id": INVOICE_ID, "vendor_id": "VEN-003", "vendor_name": "Original Source Vendor",
        "invoice_number": "INV-003", "amount_cents": 12500,
    }]})
    store.save_record("documents", DOCUMENT_ID, {"document_id": DOCUMENT_ID, "invoice_id": INVOICE_ID})
    store.save_record("approvals", "case-approval", {
        "approval_id": "case-approval", "run_id": RUN_ID, "invoice_id": INVOICE_ID,
        "invoice_number": "INV-003", "vendor_name": "Original Source Vendor", "amount_cents": 12500,
        "requested_account_display": "999900009999", "account_on_file_display": "111100004821",
    })
    tally = {"paid_count": 0, "paid_cents": 0, "held_count": 0, "blocked_count": 0,
             "not_on_file_count": 0, "not_on_file_cents": 0}
    tallies = {"unguarded": tally, "countersign": {**tally, "held_count": 1}}
    run = Run.model_validate({
        "run_id": RUN_ID, "batch_id": "case-batch", "instruction": "Pay verified invoices.",
        "created_at": datetime.now(timezone.utc), "unguarded_mode": "ap",
        "lanes": [{"lane": lane, "status": "running"} for lane in ("unguarded", "countersign")],
    })
    store.save_run(run)
    events = [
        {"type": "run.started", "lane": "system", "mode": "dry_run", "payload": {
            "batch_id": run.batch_id, "lanes": ["unguarded", "countersign"], "instruction": run.instruction,
            "models": {"extractor": "haiku-source-model", "planner": "sonnet-source-model", "unguarded": "qwen-source-model"}}},
        {"type": "document.loaded", "lane": "countersign", "mode": "dry_run", "provider": "anthropic",
         "model": "haiku-source-model", "payload": {"invoice_id": INVOICE_ID, "document_id": DOCUMENT_ID,
                                                    "pages": 1, "line_count": 1}},
        {"type": "payment.held", "lane": "countersign", "mode": "dry_run", "payload": {
            "invoice_id": INVOICE_ID, "approval_id": "case-approval", "reason_codes": ["BANK_DETAILS_MISMATCH"]}},
        {"type": "agent.message", "lane": "unguarded", "mode": "dry_run", "provider": "bitdeer",
         "model": "qwen-source-model", "unguarded_mode": "ap", "payload": {
             "invoice_id": INVOICE_ID, "text": "Original untrusted evidence CS-CANARY-A1B2"}},
    ]
    for event in events:
        store.append_event(RUN_ID, event)
    if finalize:
        for lane in run.lanes:
            lane.status = "completed"
        run = Run.model_validate({**run.model_dump(mode="json"), "tallies": tallies})
        store.save_run(run)
        store.append_event(RUN_ID, {"type": "run.completed", "lane": "system", "mode": "dry_run",
                                    "payload": {"tallies": tallies}})
    return run


@pytest.fixture
def stores(tmp_path):
    source, target = Store(tmp_path / "case.db"), Store(tmp_path / "main.db")
    source.initialize()
    target.initialize()
    make_source(source)
    try:
        yield source, target
    finally:
        source.close()
        target.close()


def resign(snapshot):
    identity = {key: snapshot[key] for key in ("version", "run_id", "last_seq", "run", "rows")}
    snapshot["snapshot_sha256"] = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return snapshot


def test_finalized_export_preserves_source_projections_and_imports_no_runtime_entities(stores):
    source, target = stores
    before = source.pending_audit(limit=100)
    source_event_data = [event.model_dump(mode="json", exclude_none=True) for event in source.list_events(RUN_ID)]
    target.save_record("documents", DOCUMENT_ID, {"document_id": DOCUMENT_ID, "invoice_id": "unrelated-main-invoice"})
    target.save_record("vendors", "VEN-003", {"vendor_id": "VEN-003", "name": "Different Main Vendor"})
    original_document = target.get_record("documents", DOCUMENT_ID)
    snapshot = source.export_audit_snapshot(RUN_ID)
    receipt = target.import_audit_snapshot(snapshot)
    staged = target.pending_audit(limit=100)
    assert [row["document"] for row in staged] == [row["document"] for row in before]
    assert [row["document"]["payload"] for row in staged] == source_event_data
    held = next(row["document"] for row in staged if row["document"]["type"] == "payment.held")
    assert held["vendor_name"] == "Original Source Vendor" and held["payee_on_file"] is False
    assert held["requested_account"] == "999900009999" and held["account_on_file"] == "111100004821"
    assert all(row["document"]["mode"] == "dry_run" for row in staged)
    assert staged[1]["document"]["payload"]["provider"] == "anthropic"
    assert staged[3]["document"]["payload"]["model"] == "qwen-source-model"
    assert target.get_record("documents", DOCUMENT_ID) == original_document
    assert target.get_record("vendors", "VEN-003")["name"] == "Different Main Vendor"
    assert target.get_run(RUN_ID) is None and target.list_events(RUN_ID) == []
    assert target.list_records("batches") == target.list_records("approvals") == []
    assert receipt["queued"] == receipt["event_count"] == 5
    assert receipt["elastic_delivery_verified"] is False
    assert source.pending_audit(limit=100) == before


def test_reimport_is_idempotent_preserves_delivered_tombstones_and_restart(stores):
    source, target = stores
    snapshot = source.export_audit_snapshot(RUN_ID)
    target.import_audit_snapshot(snapshot)
    first_id = snapshot["rows"][0]["event_id"]
    target.acknowledge_audit([first_id])
    path = target.path
    target.close()
    target.initialize()
    assert target.path == path
    again = target.import_audit_snapshot(source.export_audit_snapshot(RUN_ID))
    assert again["queued"] == 0 and again["already_present"] == 5
    assert again["destination_pending"] == 4
    assert first_id not in {row["event_id"] for row in target.pending_audit(limit=100)}


def test_changed_snapshot_rejected_without_overwriting_any_existing_history(stores):
    source, target = stores
    snapshot = source.export_audit_snapshot(RUN_ID)
    target.import_audit_snapshot(snapshot)
    before = target.pending_audit(limit=100)
    changed = deepcopy(snapshot)
    changed["rows"][2]["document"]["vendor_name"] = "Replacement historical projection"
    resign(changed)
    with pytest.raises(ValueError, match="immutable source"):
        target.import_audit_snapshot(changed)
    assert target.pending_audit(limit=100) == before


def test_existing_event_collision_is_not_silently_overwritten(stores):
    source, target = stores
    snapshot = source.export_audit_snapshot(RUN_ID)
    target.import_audit_snapshot(snapshot)
    event_id = snapshot["rows"][2]["event_id"]
    altered = deepcopy(snapshot["rows"][2]["document"])
    altered["vendor_name"] = "Conflicting destination history"
    with target.transaction() as connection:
        # Force an earlier missing row so the failed import must roll back a
        # new INSERT as well as refuse the later conflicting existing row.
        connection.execute("DELETE FROM audit_outbox WHERE event_id=?", (snapshot["rows"][0]["event_id"],))
        connection.execute("UPDATE audit_outbox SET document=? WHERE event_id=?", (json.dumps(altered), event_id))
    before = target.pending_audit(limit=100)
    with pytest.raises(ValueError, match="identity collision"):
        target.import_audit_snapshot(snapshot)
    assert target.pending_audit(limit=100) == before


@pytest.mark.parametrize("change", ["event_id", "row_seq", "document_seq", "payload_run", "mode", "extra_projection", "checksum"])
def test_bad_snapshot_identity_or_projection_is_rejected_atomically(stores, change):
    source, target = stores
    snapshot = source.export_audit_snapshot(RUN_ID)
    if change == "event_id":
        snapshot["rows"][0]["event_id"] = "different-event-id"
    elif change == "row_seq":
        snapshot["rows"][0]["seq"] = 99
    elif change == "document_seq":
        snapshot["rows"][0]["document"]["seq"] = 99
    elif change == "payload_run":
        snapshot["rows"][0]["document"]["payload"]["run_id"] = "different-run"
    elif change == "mode":
        snapshot["rows"][0]["document"]["mode"] = "live"
    elif change == "extra_projection":
        snapshot["rows"][0]["document"]["unknown_projection"] = "extra"
    else:
        snapshot["snapshot_sha256"] = "0" * 64
    if change != "checksum":
        resign(snapshot)
    with pytest.raises(ValueError):
        target.import_audit_snapshot(snapshot)
    assert target.audit_pending_count() == 0 and not target.audit_run_known(RUN_ID)


@pytest.mark.asyncio
async def test_imported_watermark_allows_unique_main_audit_error_and_safe_reimport(stores):
    source, target = stores
    snapshot = source.export_audit_snapshot(RUN_ID)
    target.import_audit_snapshot(snapshot)
    bus = EventBus(target)
    assert await bus.report_audit_failure(RUN_ID, "Elastic audit delivery failed; source events remain queued.") is True
    assert await bus.report_audit_failure(RUN_ID, "Repeated failure") is False
    [error] = target.list_events(RUN_ID)
    assert error.type == "error" and error.lane == "system" and error.seq == 6
    before = target.pending_audit(limit=100)
    target.import_audit_snapshot(snapshot)
    assert target.pending_audit(limit=100) == before
    assert before[-1]["event_id"] == RUN_ID + ":6"


def test_active_or_preexisting_local_run_identity_cannot_be_claimed(stores):
    source, target = stores
    snapshot = source.export_audit_snapshot(RUN_ID)
    local = deepcopy(snapshot["run"])
    local["lanes"][0]["status"] = "running"
    target.save_run(local)
    with pytest.raises(ValueError, match="active local"):
        target.import_audit_snapshot(snapshot)
    assert target.audit_pending_count() == 0
    local["lanes"][0]["status"] = "completed"
    target.save_run(local)
    with pytest.raises(ValueError, match="already belongs"):
        target.import_audit_snapshot(snapshot)
    assert target.audit_pending_count() == 0


@pytest.mark.parametrize("problem", ["running", "no_final_completion", "missing_outbox", "mapping_error"])
def test_unfinished_or_incomplete_source_remains_local_and_cannot_export(stores, problem):
    source, target = stores
    if problem == "running":
        run = source.get_run(RUN_ID)
        run.lanes[0].status = "running"
        source.save_run(run)
    elif problem == "no_final_completion":
        # A terminal-looking Run record alone is not proof that its event
        # producer reached the final completion event.
        with source.transaction() as connection:
            connection.execute("DELETE FROM events WHERE run_id=? AND seq=5", (RUN_ID,))
            connection.execute("DELETE FROM audit_outbox WHERE run_id=? AND seq=5", (RUN_ID,))
    elif problem == "missing_outbox":
        with source.transaction() as connection:
            connection.execute("DELETE FROM audit_outbox WHERE run_id=? AND seq=2", (RUN_ID,))
    else:
        with source.transaction() as connection:
            connection.execute("UPDATE audit_outbox SET mapping_error='UnresolvedProjection' WHERE run_id=? AND seq=2", (RUN_ID,))
    with pytest.raises(ValueError):
        export_finalized_case_audit(source=source, target=target, run_id=RUN_ID)
    assert target.audit_pending_count() == 0
    assert source.get_run(RUN_ID) is not None


@pytest.mark.parametrize("mode", ["live", "offline", None])
def test_bridge_only_notifies_configured_live_sink_without_network_or_claiming_delivery(stores, mode):
    source, target = stores
    notify = Mock()
    sink = SimpleNamespace(mode=mode, notify=notify) if mode is not None else None
    receipt = export_finalized_case_audit(source=source, target=target, run_id=RUN_ID, audit_sink=sink)
    assert receipt["sink_mode"] == mode and receipt["elastic_delivery_verified"] is False
    assert receipt["source_file"] == str(source.path) and receipt["destination_pending"] == 5
    assert receipt["delivery"] == {"live": "queued_live", "offline": "queued_offline", None: "queued_no_sink"}[mode]
    assert notify.call_count == (1 if mode == "live" else 0)
    assert source.audit_pending_count() == 5


def test_notify_failure_keeps_durable_queue_and_no_upstream_exception_text(stores):
    source, target = stores
    sink = SimpleNamespace(mode="live", notify=Mock(side_effect=RuntimeError("Do not retain exception contents")))
    receipt = export_finalized_case_audit(source=source, target=target, run_id=RUN_ID, audit_sink=sink)
    assert receipt["notification_error_type"] == "RuntimeError"
    assert receipt["elastic_delivery_verified"] is False and target.audit_pending_count() == 5
    assert "Do not retain exception contents" not in json.dumps(receipt)
