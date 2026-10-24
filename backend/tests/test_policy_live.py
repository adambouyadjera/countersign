"""Real Gemini/Anthropic/Nessie acceptance; all bank IDs come from live responses."""

import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.contract_models import PolicyDecision, ProvenanceGraph
from app.main import create_app
from app.store import Store
from app.world.seed import seed_world


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_four_transfers_one_hold_then_real_vendor_countersign(tmp_path):
    settings = Settings()
    if not all(getattr(settings, key).get_secret_value() for key in
               ("nessie_api_key", "anthropic_api_key", "gemini_api_key")):
        pytest.skip("Live Nessie, Anthropic and Gemini credentials are required")
    settings = settings.model_copy(update={"nessie_mode": "live", "planner_mode": "live",
                                          "extractor_mode": "live", "elastic_mode": "live"})
    app = create_app(settings, db_path=tmp_path / "policy-live.sqlite")
    async with app.router.lifespan_context(app):
        # Reuse the already-seeded live sandbox accounts. Only local execution
        # state is isolated, making the acceptance test repeatable per session.
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/batches", json={"kind": "standard"})
            assert response.status_code == 200
            batch = response.json()
            started = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
            assert started.status_code == 200
            run = await app.state.run_manager.wait(started.json()["run_id"])
            events = app.state.store.list_events(run.run_id)
            errors = [event.payload.message for event in events if event.type == "error"]
            assert run.lanes[0].status == "completed", errors
            assert run.tallies.countersign.paid_count == 4, errors
            assert run.tallies.countersign.held_count == 1
            assert run.tallies.countersign.not_on_file_count == 0
            decisions_response = await client.get(f"/api/runs/{run.run_id}/decisions")
            decisions = [PolicyDecision.model_validate(row) for row in decisions_response.json()]
            assert sum(row.outcome == "ALLOW" for row in decisions) == 4
            held = next(row for row in decisions if row.outcome == "HOLD")
            assert held.reason_codes == ["BANK_DETAILS_MISMATCH"]
            graph_response = await client.get(f"/api/runs/{run.run_id}/invoices/{held.invoice_id}/graph")
            assert graph_response.status_code == 200
            graph = ProvenanceGraph.model_validate(graph_response.json())
            policy_payments = [event.payload.payment for event in events if event.type == "payment.executed"]
            assert len(policy_payments) == len({row.transfer_id for row in policy_payments}) == 4
            assert all(row.bank_mode == "live" and row.payee_on_file for row in policy_payments)
            readback = await app.state.adapters.bank.list_transfers(policy_payments[0].from_account_id)
            assert {row.transfer_id for row in policy_payments} <= {row.transfer_id for row in readback}
            resolved = await client.post(f"/api/approvals/{held.approval_id}", json={
                "decision": "pay_vendor_on_file", "actor_id": "person-ap-manager",
                "note": "Live acceptance: countersign the verified vendor account on file.",
            })
            assert resolved.status_code == 200, resolved.text
            resolution = resolved.json()
            assert resolution["status"] == "resolved" and resolution["resolution"]["transfer_id"]
            final_events = app.state.store.list_events(run.run_id)
            approval_events = [event for event in final_events if event.type == "payment.executed" and event.payload.via == "approval"]
            assert len(approval_events) == 1
            payment = approval_events[0].payload.payment
            assert payment.bank_mode == "live" and payment.payee_on_file
            assert payment.to_display_number == "300300003333"
            readback = await app.state.adapters.bank.list_transfers(payment.from_account_id)
            assert payment.transfer_id in {row.transfer_id for row in readback}
            assert (await client.get(f"/api/runs/{run.run_id}")).json()["tallies"]["countersign"]["paid_count"] == 5
            proof = {
                "run_id": run.run_id, "batch_id": batch["batch_id"],
                "extractor_model": settings.extractor_model, "planner_model": settings.planner_model,
                "policy_payments": [row.model_dump(mode="json") for row in policy_payments],
                "held_decision": held.model_dump(mode="json"), "held_graph": graph.model_dump(mode="json"),
                "approval": resolution, "approved_payment": payment.model_dump(mode="json"),
            }
            destination = Path("/tmp/countersign-policy-live-proof.json")
            destination.write_text(json.dumps(proof, indent=2))
            print(json.dumps({"run_id": run.run_id, "policy_transfer_ids": [row.transfer_id for row in policy_payments],
                              "approval_transfer_id": payment.transfer_id, "approval_account": payment.to_display_number,
                              "statuses": [row.transfer_status for row in [*policy_payments, payment]],
                              "proof": str(destination)}))
