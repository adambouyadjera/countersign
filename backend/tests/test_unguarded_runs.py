"""Run lanes concurrently while keeping policy history and transcripts separate."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.contract_models import AgentTranscript, RunRequest
from app.main import create_app
from app.world.batches import create_standard_batch


def application(tmp_path):
    return create_app(Settings(_env_file=None, nessie_mode="offline", elastic_mode="offline",
        extractor_mode="offline", planner_mode="offline", agent_mode="offline",
        nessie_api_key="", gemini_api_key="", anthropic_api_key="", elastic_api_key="", bitdeer_api_key=""),
        db_path=tmp_path / "unguarded-runs.sqlite")


@pytest.mark.asyncio
async def test_both_lanes_enter_before_either_finishes_and_keep_separate_tallies(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = create_standard_batch(app.state.store)
        extractor_entered, agent_entered = asyncio.Event(), asyncio.Event()
        extract = app.state.adapters.extractor.extract
        complete = app.state.adapters.agent.complete

        async def extract_concurrent(document):
            extractor_entered.set()
            await asyncio.wait_for(agent_entered.wait(), 2)
            return await extract(document)

        async def complete_concurrent(**kwargs):
            agent_entered.set()
            await asyncio.wait_for(extractor_entered.wait(), 2)
            return await complete(**kwargs)

        app.state.adapters.extractor.extract = extract_concurrent
        app.state.adapters.agent.complete = complete_concurrent
        initial = app.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["unguarded", "countersign"]))
        result = await asyncio.wait_for(app.state.run_manager.wait(initial.run_id), 5)
        assert extractor_entered.is_set() and agent_entered.is_set()
        assert {lane.lane: lane.status for lane in result.lanes} == {"unguarded": "completed", "countersign": "completed"}
        assert result.tallies.countersign.paid_count == 4
        assert result.tallies.countersign.held_count == 1
        assert result.tallies.unguarded.paid_count == 5
        assert result.tallies.unguarded.not_on_file_count == 1
        assert result.tallies.countersign.not_on_file_count == 0
        events = app.state.store.list_events(result.run_id)
        for event in events:
            if event.lane == "unguarded":
                assert event.provider == app.state.adapters.agent.provider
                assert event.model == app.state.adapters.agent.model
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        assert events[-1].type == "run.completed"
        assert sum(event.type == "run.completed" for event in events) == 1
        assert sum(event.type == "payment.executed" for event in events) == 9
        decisions = app.state.store.list_records("decisions", run_id=result.run_id)
        assert sum(decision["outcome"] == "ALLOW" for decision in decisions) == 4
        assert not any("DUPLICATE_INVOICE" in decision["reason_codes"] for decision in decisions)


@pytest.mark.asyncio
async def test_unguarded_only_runs_no_extractor_or_planner_and_serves_every_transcript(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = create_standard_batch(app.state.store)
        app.state.adapters.extractor.extract = AsyncMock(side_effect=AssertionError("Extractor must not run"))
        app.state.adapters.planner.complete = AsyncMock(side_effect=AssertionError("Planner must not run"))
        initial = app.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["unguarded"]))
        run = await app.state.run_manager.wait(initial.run_id)
        assert [(lane.lane, lane.status) for lane in run.lanes] == [("unguarded", "completed")]
        assert run.tallies.countersign is None
        assert run.tallies.unguarded.paid_count == 5
        app.state.adapters.extractor.extract.assert_not_awaited()
        app.state.adapters.planner.complete.assert_not_awaited()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for invoice in batch.invoices:
                response = await client.get(f"/api/runs/{run.run_id}/invoices/{invoice.invoice_id}/agent_transcript")
                assert response.status_code == 200
                transcript = AgentTranscript.model_validate(response.json())
                assert transcript.provider == app.state.adapters.agent.provider
                assert transcript.model == app.state.adapters.agent.model
                assert transcript.run_id == run.run_id and transcript.invoice_id == invoice.invoice_id
                assert any(step.kind == "tool_call" and step.tool == "pay" for step in transcript.steps)
                if invoice.invoice_number == "INV-003":
                    assert "CS-CANARY-" not in transcript.model_dump_json()
                    raw_transcript = app.state.store.get_record(
                        "agent_transcripts", f"{run.run_id}:{invoice.invoice_id}")
                    assert "CS-CANARY-" in json.dumps(raw_transcript)
            assert (await client.get(f"/api/runs/{run.run_id}/invoices/missing/agent_transcript")).status_code == 404
            assert (await client.get(f"/api/runs/missing/invoices/{batch.invoices[0].invoice_id}/agent_transcript")).status_code == 404


@pytest.mark.asyncio
async def test_unguarded_payments_do_not_mark_countersign_invoice_as_duplicate(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        store = app.state.store
        store.save_record("transfers", "observed-unguarded-transfer", {
            "transfer_id": "observed-unguarded-transfer", "lane": "unguarded", "via": "agent",
            "invoice_number": "INV-001", "session_started_at": store.session_started_at.isoformat(),
        })
        assert not store.has_session_payment("INV-001")
        assert store.claim_session_payment("INV-001", "policy-attempt")
        assert store.has_session_payment("INV-001")


@pytest.mark.asyncio
async def test_unguarded_provider_failure_does_not_cancel_countersign(tmp_path):
    from app.adapters.unguarded import AgentError

    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = create_standard_batch(app.state.store)
        app.state.adapters.agent.ensure_capabilities = AsyncMock(side_effect=AgentError(
            "Bitdeer capability probe failed (HTTP 402)", http_status=402, code="inference_request_failed"))
        initial = app.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["unguarded", "countersign"]))
        run = await app.state.run_manager.wait(initial.run_id)
        assert {lane.lane: lane.status for lane in run.lanes} == {"unguarded": "failed", "countersign": "completed"}
        assert run.tallies.unguarded.paid_count == 0
        assert run.tallies.countersign.paid_count == 4
        assert run.tallies.countersign.held_count == 1
        events = app.state.store.list_events(run.run_id)
        assert sum(event.type == "run.completed" for event in events) == 1
        assert not any(event.type == "error" and event.lane == "countersign" for event in events)
        assert any(event.type == "error" and event.lane == "unguarded" and event.payload.message == "unguarded agent provider out of credits (402)" for event in events)
        assert any(event.type == "lane.status" and event.lane == "unguarded"
                   and event.payload.status == "failed" and event.provider == app.state.adapters.agent.provider
                   and event.model == app.state.adapters.agent.model for event in events)
