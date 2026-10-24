"""Real Bitdeer tool-calling lane against the configured live Nessie sandbox."""

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.contract_models import AgentTranscript
from app.main import create_app
from app.store import Store
from app.world.seed import seed_world


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_unguarded_standard_batch_completes(tmp_path):
    settings = Settings()
    if not settings.bitdeer_api_key.get_secret_value() or not settings.nessie_api_key.get_secret_value():
        pytest.skip("BITDEER_API_KEY and NESSIE_API_KEY are required")
    settings = settings.model_copy(update={"agent_mode": "live", "nessie_mode": "live", "unguarded_dry_run": False})
    app = create_app(settings, db_path=tmp_path / "unguarded-live.sqlite")
    async with app.router.lifespan_context(app):
        existing = Store()
        existing.initialize()
        try:
            for table, key in (("bank_seeds", "seed_id"), ("bank_directory", "display_number")):
                for row in existing.list_records(table):
                    if row.get("mode") == "live":
                        app.state.store.save_record(table, row[key], row)
        finally:
            existing.close()
        await seed_world(app.state.store, app.state.adapters.bank)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "standard"})).json()
            started = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["unguarded"]})
            assert started.status_code == 200
            run = await app.state.run_manager.wait(started.json()["run_id"])
            events = app.state.store.list_events(run.run_id)
            transcripts = []
            for invoice in batch["invoices"]:
                response = await client.get(f"/api/runs/{run.run_id}/invoices/{invoice['invoice_id']}/agent_transcript")
                assert response.status_code == 200
                transcripts.append(AgentTranscript.model_validate(response.json()))
            proof = {"run": run.model_dump(mode="json"), "transcripts": [item.model_dump(mode="json") for item in transcripts],
                     "events": [event.model_dump(mode="json") for event in events]}
            Path("/tmp/countersign-unguarded-live-proof.json").write_text(json.dumps(proof, indent=2))
            errors = [event.payload.message for event in events if event.type == "error"]
            assert [(lane.lane, lane.status) for lane in run.lanes] == [("unguarded", "completed")], errors
            assert run.tallies.countersign is None
            payments = [event.payload.payment for event in events if event.type == "payment.executed"]
            assert payments, "A valid control invoice must produce a real bank submission"
            assert all(item.bank_mode == "live" and item.transfer_id for item in payments)
            assert run.tallies.unguarded.paid_count == len(payments)
            assert any(event.type == "agent.tool_call" for event in events)
            assert all(event.lane in {"unguarded", "system"} for event in events)
            readback = await app.state.adapters.bank.list_transfers(payments[0].from_account_id)
            assert {item.transfer_id for item in payments} <= {item.transfer_id for item in readback}
            print(json.dumps({"model": settings.naive_model, "paid": len(payments),
                              "not_on_file": run.tallies.unguarded.not_on_file_count,
                              "transfer_ids": [item.transfer_id for item in payments],
                              "proof": "/tmp/countersign-unguarded-live-proof.json"}))
