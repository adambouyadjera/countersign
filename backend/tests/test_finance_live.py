"""Real quarantined extraction/planning and Nessie register-to-books evidence."""

from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.store import Store
from app.world.seed import seed_world


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_finance_books_and_bank_register_agree(tmp_path):
    settings = Settings()
    if not all(getattr(settings, key).get_secret_value() for key in
               ("nessie_api_key", "anthropic_api_key", "gemini_api_key")):
        pytest.skip("Live Nessie, Anthropic and extractor credentials are required")
    settings = settings.model_copy(update={"nessie_mode": "live", "extractor_mode": "live",
        "planner_mode": "live", "elastic_mode": "offline", "agent_mode": "offline", "mutator_mode": "offline"})
    app = create_app(settings, db_path=tmp_path / "finance-live.sqlite")
    async with app.router.lifespan_context(app):
        existing = Store()
        existing.initialize()
        try:
            for table, key in (("bank_directory", "display_number"), ("bank_seeds", "seed_id")):
                for row in existing.list_records(table):
                    if row.get("mode") == "live":
                        app.state.store.save_record(table, row[key], row)
        finally:
            existing.close()
        await seed_world(app.state.store, app.state.adapters.bank)
        proof = {"recorded": True, "recorded_at": datetime.now(timezone.utc).isoformat(),
                 "status": "failed", "execution": "live_countersign", "bank_mode": "live",
                 "reconciliation_basis": "submitted_payment_register_including_pending"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch_response = await client.post("/api/batches", json={"kind": "standard"})
            assert batch_response.status_code == 200
            batch = batch_response.json()
            proof["batch"] = batch
            initial = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
            assert initial.status_code == 200
            run_id = initial.json()["run_id"]
            proof["run_id"] = run_id
            destination = Path("docs") / f"finance_live_{run_id}.json"
            try:
                run = await app.state.run_manager.wait(run_id)
                proof["run"] = run.model_dump(mode="json")
                events = app.state.store.list_events(run_id)
                proof["events"] = [event.model_dump(mode="json") for event in events]
                proof["decisions"] = (await client.get(f"/api/runs/{run_id}/decisions")).json()
                proof["journal_entries"] = (await client.get("/api/ledger/entries", params={"run_id": run_id})).json()
                proof["trial_balance"] = (await client.get("/api/ledger/trial_balance")).json()
                reconciliation = await client.get("/api/reconciliation")
                proof["reconciliation_http_status"] = reconciliation.status_code
                proof["reconciliation"] = reconciliation.json()
                proof["bank_balance_observation"] = await app.state.reconciler.balance_observation()
                proof["planner_transcript"] = (await client.get(f"/api/runs/{run_id}/planner_transcript")).json()
                payments = [event.payload.payment for event in events if event.type == "payment.executed"]
                assert run.lanes[0].status == "completed"
                assert run.tallies.countersign.paid_count == 4 and run.tallies.countersign.held_count == 1
                assert len(payments) == 4 and all(payment.bank_mode == "live" and payment.payee_on_file for payment in payments)
                assert proof["trial_balance"]["balanced"] is True
                assert len(proof["journal_entries"]) == 8
                assert sum(event.type == "ledger.posted" for event in events) == 8
                assert reconciliation.status_code == 200
                result = proof["reconciliation"]
                assert result["difference_cents"] == 0
                assert all(item["status"] == "matched" for item in result["items"])
                assert {payment.transfer_id for payment in payments} == {item["transfer_id"] for item in result["items"]}
                held = next(item for item in proof["decisions"] if item["outcome"] == "HOLD")
                assert held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
                assert not any(entry["source"]["id"] == held["invoice_id"] for entry in proof["journal_entries"])
                assert proof["planner_transcript"]["leak_guard"]["matches"] == 0
                proof["status"] = "passed"
            finally:
                if "events" not in proof:
                    proof["events"] = [event.model_dump(mode="json") for event in app.state.store.list_events(run_id)]
                destination.write_text(json.dumps(proof, indent=2) + "\n")
                print(json.dumps({"run_id": run_id, "status": proof["status"], "evidence": str(destination),
                    "trial_balance_balanced": proof.get("trial_balance", {}).get("balanced"),
                    "difference_cents": proof.get("reconciliation", {}).get("difference_cents")}))
