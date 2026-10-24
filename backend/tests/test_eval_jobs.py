"""Fixed-suite jobs use real decision code with explicitly offline model fakes."""

import asyncio
import json
from unittest.mock import AsyncMock

from fastapi import HTTPException
import httpx
import pytest

from app.adapters.bank import LiveBankAdapter, OfflineBankAdapter
from app.adapters.dry_run_bank import DryRunBank, DryRunViolation
from app.adapters.factory import build_adapters
from app.config import Settings
from app.contract_models import EvalJob, EvalRequest
from app.evaluation.metrics import compute_metrics, result_passed
from app.evaluation.runner import EvalManager
from app.evaluation.suite import load_suite, suite_sha256
from app.main import create_app
from app.store import Store


def settings(**overrides):
    values = dict(_env_file=None, nessie_mode="offline", extractor_mode="offline", planner_mode="offline",
                  agent_mode="offline", mutator_mode="offline", elastic_mode="offline",
                  nessie_api_key="", anthropic_api_key="", gemini_api_key="", naive_api_key="",
                  bitdeer_api_key="", mutator_api_key="", elastic_api_key="")
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def no_inherited_configuration(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


class RecordingFactory:
    def __init__(self, gate=None):
        self.calls = []
        self.extracted_document_ids = []
        self.agent_identities = []
        self.started = asyncio.Event()
        self.gate = gate

    def __call__(self, configuration, store):
        self.calls.append({"store": str(store.path), "extractor_mode": configuration.extractor_mode,
                           "nessie_mode": configuration.nessie_mode, "agent_mode": configuration.agent_mode,
                           "mutator_mode": configuration.mutator_mode})
        adapters = build_adapters(configuration, store)
        self.agent_identities.append({"provider": adapters.agent.provider, "model": adapters.agent.model})
        original = adapters.extractor.extract

        async def extract(document):
            self.extracted_document_ids.append(document.document_id)
            self.started.set()
            if self.gate is not None:
                await self.gate.wait()
            return await original(document)

        adapters.extractor.extract = extract
        return adapters


def forbidden_live_bank(*args, **kwargs):
    raise AssertionError("A planted-errors dry run cannot initialize a live bank adapter")


def main_snapshot(app):
    tables = ("runs", "documents", "batches", "session_payment_claims", "dry_run_intents", "transfers",
              "decisions", "approvals", "vendors", "purchase_orders", "bank_directory")
    return {table: app.state.store.list_records(table) for table in tables}


@pytest.mark.asyncio
async def test_full_offline_http_job_scores_all_forty_and_does_not_touch_demo_or_bank_state(tmp_path, monkeypatch):
    monkeypatch.setattr(LiveBankAdapter, "__init__", forbidden_live_bank)
    forbidden_transfer = AsyncMock(side_effect=AssertionError("Evaluation cannot call a financial transfer"))
    monkeypatch.setattr(OfflineBankAdapter, "transfer", forbidden_transfer)
    app = create_app(settings(), db_path=tmp_path / "demo.db")
    key_before = suite_sha256()
    suite = load_suite()
    async with app.router.lifespan_context(app):
        factory = RecordingFactory()
        artifacts = tmp_path / "eval-artifacts"
        app.state.eval_manager = EvalManager(app.state.store, app.state.settings,
                                            data_root=artifacts, adapter_factory=factory)
        before = main_snapshot(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/eval", json={"suite": "planted_errors_v1"})
            assert response.status_code == 200, response.text
            initial = EvalJob.model_validate(response.json())
            assert initial.execution == "dry_run" and initial.progress.total == 40
            job = await asyncio.wait_for(app.state.eval_manager.wait(initial.job_id), timeout=60)
            assert job.status == "completed"
            assert job.progress.completed == job.progress.total == len(job.items) == 40
            assert [result.item_id for result in job.items] == [item.id for item in suite.items]
            assert all(result.actual_outcome != "ERROR" for result in job.items)
            assert all(result.passed == result_passed(result) for result in job.items)
            polled = await client.get(f"/api/eval/{job.job_id}")
            assert polled.status_code == 200
            assert EvalJob.model_validate(polled.json()) == job
        assert suite_sha256() == key_before
        assert len(factory.extracted_document_ids) == len(set(factory.extracted_document_ids)) == 40
        assert all(call["extractor_mode"] == "offline" and call["nessie_mode"] == "offline"
                   and call["mutator_mode"] == "offline" for call in factory.calls)
        assert main_snapshot(app) == before
        assert app.state.ledger.list_entries() == []
        assert app.state.store.list_records("eval_receipts") == []  # Offline passes do not claim live cache provenance.
        forbidden_transfer.assert_not_awaited()
        evidence = json.loads((artifacts / job.job_id / "evidence.json").read_text())
        assert evidence["suite_sha256"] == key_before
        assert evidence["policy_hashes_before"] == evidence["policy_hashes_after"]
        assert evidence["unguarded_provider"] == evidence["unguarded_model"] == "not_run"
        assert set(evidence["cases"]) == {item.id for item in suite.items}
        assert job.metrics == compute_metrics(job.items,
            unauthorized_item_ids=set(evidence["countersign_unauthorized_item_ids"]))
        # The scorer may expose a known policy miss; it must not manufacture a
        # perfect expected score or hide unsafe would-pay decisions.
        semantic = {item.id for item in suite.items if item.duplicate_kind == "vendor_amount_date"}
        assert semantic
        observed_unsafe = {item_id for item_id in semantic
                           if any(intent["lane"] == "countersign" for intent in evidence["cases"][item_id]["intents"])}
        assert observed_unsafe <= set(evidence["countersign_unauthorized_item_ids"])
        for item in suite.items:
            case = evidence["cases"][item.id]
            assert case["execution"] == "dry_run" and case["bank_submitted"] is False
            assert case["bank_denied_calls"] == []
            assert case["result"]["expected_outcome"] == item.expected_outcome
            assert case["result"]["expected_reason_code"] == item.expected_reason_code
            assert all(event["type"] not in {"payment.executed", "ledger.posted"} for event in case["events"])
            assert all(event["mode"] == "offline" for event in case["events"])
            assert all(intent["submitted"] is False and intent["execution"] == "dry_run"
                       and "transfer_id" not in intent and "transfer_status" not in intent for intent in case["intents"])
            assert all(receipt["event"] == "offline_passthrough" for receipt in case["extraction_receipts"])
            assert "expected_outcome" not in json.dumps(case["planner_transcript"])
            assert "expected_reason_code" not in json.dumps(case["planner_transcript"])
            assert case["planner_transcript"]["leak_guard"]["matches"] == 0
        assert json.loads((artifacts / job.job_id / "job.json").read_text()) == job.model_dump(mode="json")


@pytest.mark.asyncio
async def test_optional_offline_unguarded_lane_has_separate_honest_labels_and_forty_primary_results(tmp_path, monkeypatch):
    monkeypatch.setattr(LiveBankAdapter, "__init__", forbidden_live_bank)
    app = create_app(settings(), db_path=tmp_path / "demo.db")
    async with app.router.lifespan_context(app):
        factory = RecordingFactory()
        app.state.eval_manager = EvalManager(app.state.store, app.state.settings,
            data_root=tmp_path / "artifacts", adapter_factory=factory)
        job = app.state.eval_manager.start(EvalRequest(suite="planted_errors_v1", lanes=["countersign", "unguarded"]))
        completed = await asyncio.wait_for(app.state.eval_manager.wait(job.job_id), timeout=60)
        assert completed.status == "completed" and len(completed.items) == 40
        evidence = json.loads((tmp_path / "artifacts" / job.job_id / "evidence.json").read_text())
        assert evidence["primary_lane"] == "countersign"
        actual_agent = factory.agent_identities[0]
        assert actual_agent["provider"] == "offline"
        assert evidence["unguarded_provider"] == actual_agent["provider"]
        assert evidence["unguarded_model"] == actual_agent["model"]
        assert len(evidence["unguarded_items"]) == 40
        assert "unguarded_metrics" in evidence
        for case in evidence["cases"].values():
            assert case["providers"]["unguarded"] == actual_agent
            assert all(transcript["provider"] == "offline" and transcript["model"] == actual_agent["model"]
                       for transcript in case["agent_transcripts"])
            assert all(event["provider"] == "offline" and event["model"] == actual_agent["model"]
                       for event in case["events"] if event["lane"] == "unguarded")
            assert not any(event["type"] == "payment.executed" for event in case["events"])
            assert all(intent["submitted"] is False and "transfer_id" not in intent for intent in case["intents"])
        assert app.state.store.list_records("transfers") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("configuration,lanes", [
    ({"agent_mode": "live", "naive_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
     ["countersign", "unguarded"]),
    ({"extractor_mode": "live", "extractor_cache": "off"}, ["countersign"]),
])
async def test_provider_and_quota_guards_reject_before_any_adapter_or_job_creation(tmp_path, configuration, lanes):
    store = Store(tmp_path / "guard.db")
    store.initialize()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Rejected evaluation must not initialize adapters")

    try:
        manager = EvalManager(store, settings(**configuration), data_root=tmp_path / "unused", adapter_factory=forbidden)
        with pytest.raises(HTTPException) as error:
            manager.start(EvalRequest(suite="planted_errors_v1", lanes=lanes))
        assert error.value.status_code == 422
        assert calls == [] and store.list_records("eval_jobs") == []
        assert not (tmp_path / "unused").exists()
        await manager.close()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_only_one_active_job_and_running_close_records_every_unfinished_item_as_error(tmp_path):
    app = create_app(settings(), db_path=tmp_path / "demo.db")
    async with app.router.lifespan_context(app):
        gate = asyncio.Event()
        factory = RecordingFactory(gate)
        manager = EvalManager(app.state.store, app.state.settings,
                              data_root=tmp_path / "artifacts", adapter_factory=factory, concurrency=1)
        app.state.eval_manager = manager
        job = manager.start(EvalRequest(suite="planted_errors_v1"))
        await asyncio.wait_for(factory.started.wait(), timeout=10)
        with pytest.raises(HTTPException) as error:
            manager.start(EvalRequest(suite="planted_errors_v1"))
        assert error.value.status_code == 503
        assert len(factory.calls) == 1
        await manager.close()
        failed = manager.get(job.job_id)
        assert failed.status == "failed"
        assert failed.progress.completed == failed.progress.total == len(failed.items) == 40
        assert all(item.actual_outcome == "ERROR" and not item.passed for item in failed.items)
        assert failed.metrics.accuracy == failed.metrics.unauthorized_payments == 0
        assert len(app.state.store.list_records("eval_jobs")) == 1
        assert app.state.store.list_records("transfers") == []
        assert not gate.is_set()
        with pytest.raises(HTTPException) as closed:
            manager.start(EvalRequest(suite="planted_errors_v1"))
        assert closed.value.status_code == 503


@pytest.mark.asyncio
async def test_queued_close_before_first_coroutine_instruction_still_has_a_truthful_terminal_job(tmp_path):
    store = Store(tmp_path / "queued.db")
    store.initialize()
    try:
        factory = RecordingFactory()
        manager = EvalManager(store, settings(), data_root=tmp_path / "artifacts", adapter_factory=factory)
        job = manager.start(EvalRequest(suite="planted_errors_v1"))
        await manager.close()  # No await yielded to the newly queued job before close.
        failed = manager.get(job.job_id)
        assert failed.status == "failed"
        assert len(failed.items) == failed.progress.completed == failed.progress.total == 40
        assert all(item.actual_outcome == "ERROR" and not item.passed for item in failed.items)
        assert failed.metrics.accuracy == 0
        assert factory.calls == []
        evidence = json.loads((tmp_path / "artifacts" / job.job_id / "evidence.json").read_text())
        assert evidence["job_error"] == "CancelledBeforeStart"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_dry_run_bank_transfer_capability_is_denied_without_creating_a_payment():
    bank = DryRunBank()
    with pytest.raises(DryRunViolation, match="no bank request"):
        await bank.transfer(from_account_id="directory-only-source", to_account_id="directory-only-destination", amount_cents=100)
    assert bank.denied_calls == ["transfer"]
    assert bank.intents == []
