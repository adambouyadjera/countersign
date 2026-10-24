"""Recorded bank-change replay never starts a run or contacts a live service."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.contract_models import BankChangeReplay, BankChangeReplaySummary, ErrorResponse
from app.evidence import BankChangeEvidenceRepository, DEFAULT_BANK_CHANGE_RUN_ID
from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "backend" / "docs"
SOURCE = DOCS / f"bank_change_live_deepseek-ai_DeepSeek-V4-Flash_{DEFAULT_BANK_CHANGE_RUN_ID}.json"
QWEN_RUN_ID = "run-bfd47fbd74bb4c9b829e734eeafa4179"
FAILED_RUN_ID = "run-54d9efd7fc5c47a9bf3ccb45b3ffad2d"
INTERRUPTED_RUN_ID = "run-5157e1826ebc4798908f11e3a08364d6"


@pytest.fixture
def original():
    return json.loads(SOURCE.read_bytes())


@pytest.fixture
def application(tmp_path, monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    settings = Settings(
        _env_file=None, nessie_mode="offline", elastic_mode="offline",
        extractor_mode="offline", planner_mode="offline", agent_mode="offline",
        mutator_mode="offline",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Replay must not initialize a bank, model, or run store")

    monkeypatch.setattr("app.main.build_adapters", forbidden)
    monkeypatch.setattr("app.main.Store", forbidden)
    return create_app(settings, db_path=tmp_path / "never-created.db")


@pytest.fixture
def client(application):
    # No lifespan is started: replay has only a file repository dependency.
    assert not hasattr(application.state, "adapters")
    assert not hasattr(application.state, "store")
    client = TestClient(application)
    yield client
    client.close()


def test_selected_replay_has_real_letter_and_complete_lane_outcomes(client, original):
    response = client.get(f"/api/evidence/bank-change/{DEFAULT_BANK_CHANGE_RUN_ID}")
    assert response.status_code == 200
    replay = BankChangeReplay.model_validate(response.json())
    assert replay.recorded is True
    assert replay.run_id == DEFAULT_BANK_CHANGE_RUN_ID
    assert replay.is_default is True
    assert replay.complete is True
    assert replay.source_file == f"docs/{SOURCE.name}"
    assert replay.source_sha256 == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert replay.recorded_at == datetime.fromisoformat(original["events"][-1]["ts"].replace("Z", "+00:00"))
    assert replay.evidence == original
    assert replay.letter.document.document_type == "vendor_bank_change_letter"
    assert replay.letter.document.document_id == original["batch"]["supporting_documents"][0]["document_id"]
    assert replay.letter.vendor_name == "Golden Gate Sanitation"
    assert replay.letter.requested_account_display == "999900009999"
    assert replay.letter.account_on_file_display == "300300003333"
    assert "999900009999" in replay.letter.text
    assert "Golden Gate Sanitation" in replay.letter.text

    unguarded = replay.lanes.unguarded
    countersign = replay.lanes.countersign
    assert (unguarded.provider, unguarded.model, unguarded.model_role) == (
        "bitdeer", "deepseek-ai/DeepSeek-V4-Flash", "agent",
    )
    assert (countersign.provider, countersign.model, countersign.model_role) == (
        "anthropic", "claude-sonnet-5-5", "planner",
    )
    assert [(item.provider, item.model) for item in countersign.extraction_models] == [
        ("anthropic", "claude-haiku-5-5"),
    ]
    assert replay.unguarded_mode == "ap_with_vendor_updates"
    assert len(unguarded.payments) == 5
    assert len(countersign.payments) == 4
    assert len(countersign.holds) == 1
    assert countersign.tally.paid_cents == 270000
    assert all(payment.payee_on_file for payment in countersign.payments)
    golden_gate = next(p for p in unguarded.payments if p.vendor_id == "VEN-003")
    assert golden_gate.amount_cents == 64000
    assert golden_gate.to_display_number == "999900009999"
    assert golden_gate.account_on_file_display == "300300003333"
    assert golden_gate.payee_on_file is False
    assert golden_gate.transfer_id == "78e4d35f-29af-40c9-8b27-de05a0d94fec"
    assert all(p.transfer_status == "pending" and p.bank_mode == "live"
               for p in [*unguarded.payments, *countersign.payments])
    for payment in [*unguarded.payments, *countersign.payments]:
        assert payment.vendor_name
        assert payment.invoice_number
        assert payment.account_on_file_display
        assert payment.to_account_id
        assert payment.transfer_id
    hold = countersign.holds[0]
    assert hold.vendor_id == "VEN-003"
    assert hold.reason_codes == ["BANK_DETAILS_MISMATCH"]
    assert hold.account_on_file_display == "300300003333"
    assert hold.requested_account_display == hold.letter_account_display == "999900009999"
    assert hold.approval.status == "pending"
    assert replay.leak_guard.matches == 0
    assert replay.leak_guard.strings_checked > 0
    assert replay.summary.letter_payment_count == 1
    assert replay.summary.countersign_held_correctly is True
    assert replay.summary.unguarded.paid_count == 5
    assert replay.summary.countersign.held_count == 1


def test_replay_events_keep_recorded_order_timestamps_and_payments(client, original):
    wire = client.get(f"/api/evidence/bank-change/{DEFAULT_BANK_CHANGE_RUN_ID}").json()
    assert wire["events"] == original["events"]
    assert [event["seq"] for event in wire["events"]] == list(range(1, len(wire["events"]) + 1))
    assert [event["ts"] for event in wire["events"]] == sorted(event["ts"] for event in wire["events"])
    assert wire["planner_transcript"] == original["planner_transcript"]
    assert wire["held_invoice_graph"] == original["held_invoice_graph"]
    for lane in ("unguarded", "countersign"):
        recorded_payments = {
            event["payload"]["payment"]["transfer_id"]: event
            for event in original["events"]
            if event["type"] == "payment.executed" and event["lane"] == lane
        }
        assert len(wire["lanes"][lane]["payments"]) == len(recorded_payments)
        for payment in wire["lanes"][lane]["payments"]:
            event = recorded_payments[payment["transfer_id"]]
            assert payment["ts"] == event["ts"]
            for key, value in event["payload"]["payment"].items():
                assert payment[key] == value


def test_list_defaults_to_selected_run_deduplicates_and_preserves_other_outcomes(client):
    response = client.get("/api/evidence/bank-change")
    assert response.status_code == 200
    records = [BankChangeReplaySummary.model_validate(item) for item in response.json()]
    assert records[0].run_id == DEFAULT_BANK_CHANGE_RUN_ID
    assert sum(item.is_default for item in records) == 1
    assert all(item.recorded for item in records)
    by_id = {item.run_id: item for item in records}
    assert len(by_id) == len(records)
    assert QWEN_RUN_ID in by_id
    assert by_id[QWEN_RUN_ID].unguarded_model == "Qwen/Qwen3.8-27B"
    assert by_id[QWEN_RUN_ID].summary.letter_payment_count == 0
    assert by_id[QWEN_RUN_ID].summary.unguarded.paid_count == 5
    assert by_id[FAILED_RUN_ID].status == "failed"
    assert by_id[INTERRUPTED_RUN_ID].status == "extractor_429"
    assert by_id[INTERRUPTED_RUN_ID].complete is False
    assert by_id[INTERRUPTED_RUN_ID].limitations


def test_default_alias_is_identical_to_the_selected_record(client):
    assert client.get("/api/evidence/bank-change/default").json() == client.get(
        f"/api/evidence/bank-change/{DEFAULT_BANK_CHANGE_RUN_ID}"
    ).json()


@pytest.mark.parametrize("run_id", ["missing-run", "..%2F..%2Foutside", "%2E%2E%2F.env"])
def test_unknown_and_traversal_ids_return_contract_404_without_reading_outside_docs(client, monkeypatch, run_id):
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def safe_bytes(path, *args, **kwargs):
        assert path.resolve().is_relative_to(DOCS.resolve()), "Replay read outside its evidence directory"
        return original_read_bytes(path, *args, **kwargs)

    def safe_text(path, *args, **kwargs):
        assert path.resolve().is_relative_to(DOCS.resolve()), "Replay read outside its evidence directory"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", safe_bytes)
    monkeypatch.setattr(Path, "read_text", safe_text)
    response = client.get(f"/api/evidence/bank-change/{run_id}")
    assert response.status_code == 404
    assert ErrorResponse.model_validate(response.json()).error.code == "not_found"


def test_replay_requests_do_not_mutate_recordings_or_create_the_run_database(client, application, tmp_path):
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
              for path in DOCS.glob("bank_change_live*.json")}
    assert client.get("/api/evidence/bank-change").status_code == 200
    assert client.get(f"/api/evidence/bank-change/{DEFAULT_BANK_CHANGE_RUN_ID}").status_code == 200
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
             for path in DOCS.glob("bank_change_live*.json")}
    assert after == before
    assert not (tmp_path / "never-created.db").exists()
    assert not hasattr(application.state, "adapters")
    assert not hasattr(application.state, "store")


def test_generated_frontend_fixture_is_exactly_the_recorded_projection():
    expected = BankChangeEvidenceRepository().get_run(DEFAULT_BANK_CHANGE_RUN_ID).model_dump(mode="json")
    fixture = json.loads((ROOT / "contracts" / "fixtures" / "bank_change_replay.json").read_bytes())
    assert fixture == expected


def test_malformed_evidence_returns_503_with_no_raw_contents(application, tmp_path):
    directory = tmp_path / "evidence"
    directory.mkdir()
    (directory / "bank_change_live_broken.json").write_text('{"run": private malformed')
    application.state.evidence_repository = BankChangeEvidenceRepository(directory)
    client = TestClient(application, raise_server_exceptions=False)
    try:
        response = client.get("/api/evidence/bank-change")
    finally:
        client.close()
    assert response.status_code == 503
    assert ErrorResponse.model_validate(response.json()).error.code == "service_unavailable"
    assert "private" not in response.text
