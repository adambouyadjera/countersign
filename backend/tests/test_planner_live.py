"""A real Anthropic plan for a standard batch; no live bank or extractor call."""

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.contract_models import PlannerTranscript
from app.main import create_app


@pytest.mark.live
@pytest.mark.asyncio
async def test_real_planner_produces_valid_standard_batch_plan_and_handle_transcript(tmp_path):
    settings = Settings()
    if not settings.anthropic_api_key.get_secret_value():
        pytest.skip("ANTHROPIC_API_KEY is not configured")
    settings = settings.model_copy(update={
        "planner_mode": "live", "extractor_mode": "offline", "nessie_mode": "offline", "elastic_mode": "offline",
    })
    application = create_app(settings, db_path=tmp_path / "planner-live.sqlite")
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "standard"})).json()
            response = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
            assert response.status_code == 200
            run = await application.state.run_manager.wait(response.json()["run_id"])
            transcript_response = await client.get(f"/api/runs/{run.run_id}/planner_transcript")
            assert transcript_response.status_code == 200
            transcript = PlannerTranscript.model_validate(transcript_response.json())
            assert transcript.calls
            assert transcript.leak_guard.matches == 0
            events = application.state.store.list_events(run.run_id)
            proposed = [event for event in events if event.type == "plan.proposed"]
            assert len(proposed) == 1
            assert proposed[0].payload.planner_model == settings.planner_model
            assert len(proposed[0].payload.steps) >= 5
            assert not any(step.op == "hold" and step.args["reason"].value == "PLANNER_INVALID"
                           for step in proposed[0].payload.steps)
            assert run.lanes[0].status == "completed"
            assert run.tallies.countersign.paid_count == 0
            for call in transcript.calls:
                envelope = json.loads(call.prompt)
                metadata = json.loads(envelope["messages"][0]["content"])
                assert all("value" not in handle for invoice in metadata["invoices"] for handle in invoice["handles"])
                assert "$D3.remit_account_number" in call.prompt
                assert "CS-CANARY" not in call.prompt
            artifact = tmp_path / "planner_transcript.json"
            artifact.write_text(transcript.model_dump_json(indent=2))
            Path("/tmp/countersign-planner-live-transcript.json").write_text(transcript.model_dump_json(indent=2))
            print({"model": settings.planner_model, "calls": len(transcript.calls),
                   "leak_guard": transcript.leak_guard.model_dump(), "transcript": str(artifact)})
            first = json.loads(json.loads(transcript.calls[0].prompt)["messages"][0]["content"])
            print("REAL_PLANNER_PROMPT_TRIMMED=" + json.dumps({
                "instruction": first["instruction"], "vendors": first["vendors"][:1],
                "invoices": [{"invoice_id": first["invoices"][2]["invoice_id"],
                              "handles": first["invoices"][2]["handles"][:3]}],
            }))
