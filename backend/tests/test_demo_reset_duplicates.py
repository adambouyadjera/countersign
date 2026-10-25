"""Demo reset changes live duplicate scope without deleting the books/history."""

import httpx
import pytest

from tests.test_demo_reset import application


async def draw(client):
    response = await client.post("/api/batches", json={"kind": "standard"})
    assert response.status_code == 200, response.text
    return response.json()


async def run_batch(app, client, batch):
    response = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
    assert response.status_code == 200, response.text
    run = await app.state.run_manager.wait(response.json()["run_id"])
    assert all(lane.status == "completed" for lane in run.lanes)
    return run


@pytest.mark.asyncio
async def test_reset_ignores_pre_reset_payments_but_repeat_run_still_blocks_duplicates(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            original_batch = await draw(client)
            original = await run_batch(app, client, original_batch)
            assert (original.tallies.countersign.paid_count, original.tallies.countersign.held_count) == (4, 1)
            approval = (await client.get("/api/approvals?status=pending")).json()[0]
            approved = await client.post(f"/api/approvals/{approval['approval_id']}", json={
                "decision": "pay_vendor_on_file", "actor_id": "person-controller"})
            assert approved.status_code == 200, approved.text
            repeat = await run_batch(app, client, original_batch)
            assert repeat.tallies.countersign.blocked_count == 5
            assert repeat.tallies.countersign.paid_count == 0

            # An unfinished, explicitly session-bound reservation is cleared.
            assert app.state.store.claim_session_payment("UNFINISHED-BEFORE-RESET", "unfinished-attempt")
            journal = [entry.model_dump(mode="json") for entry in app.state.ledger.list_entries()]
            transfers = app.state.store.list_records("transfers")
            archives = {vendor["vendor_id"]: [payment for payment in vendor["payments"]
                if payment["journal_entry_id"].startswith("example-history-")]
                for vendor in app.state.store.list_records("vendor_history")}
            reset = await client.post("/api/demo/reset", json={"autopilot": False})
            assert reset.status_code == 200, reset.text
            assert app.state.store.list_records("session_payment_claims") == []
            assert app.state.store.list_records("transfers") == transfers
            assert [entry.model_dump(mode="json") for entry in app.state.ledger.list_entries()] == journal
            assert {vendor["vendor_id"]: [payment for payment in vendor["payments"]
                if payment["journal_entry_id"].startswith("example-history-")]
                for vendor in app.state.store.list_records("vendor_history")} == archives

            fresh_batch = await draw(client)
            fresh = await run_batch(app, client, fresh_batch)
            tally = fresh.tallies.countersign
            print({"after_reset": {"paid": tally.paid_count, "held": tally.held_count, "blocked": tally.blocked_count}})
            assert (tally.paid_count, tally.held_count, tally.blocked_count) == (4, 1, 0)
            decisions = (await client.get(f"/api/runs/{fresh.run_id}/decisions")).json()
            held = next(decision for decision in decisions if decision["outcome"] == "HOLD")
            assert held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
            assert all("DUPLICATE_INVOICE" not in decision["reason_codes"] for decision in decisions)
            pending = (await client.get("/api/approvals?status=pending")).json()
            assert len(pending) == 1 and pending[0]["invoice_id"] == held["invoice_id"]

            repeated = await run_batch(app, client, fresh_batch)
            tally = repeated.tallies.countersign
            assert (tally.paid_count, tally.blocked_count, tally.held_count) == (0, 4, 1)
            # The held invoice was never paid, so stays held rather than being
            # incorrectly added to the duplicate payment register.
            duplicate_decisions = (await client.get(f"/api/runs/{repeated.run_id}/decisions")).json()
            assert sum("DUPLICATE_INVOICE" in decision["reason_codes"] for decision in duplicate_decisions) == 4
