"""The comparison lane moves through the bank but never the company books."""

from collections import Counter

import httpx
import pytest

from app.contract_models import RunRequest
from app.world.batches import create_standard_batch
from tests.test_finance_runs import application


async def run_lanes(app, lanes):
    batch = create_standard_batch(app.state.store)
    started = app.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=lanes))
    run = await app.state.run_manager.wait(started.run_id)
    assert all(lane.status == "completed" for lane in run.lanes)
    return batch, run


@pytest.mark.asyncio
async def test_unguarded_payments_create_no_company_entries_or_vendor_payment_history(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        _, run = await run_lanes(app, ["unguarded"])
        assert run.tallies.unguarded.paid_count == 5
        assert len(app.state.store.list_records("transfers")) == 5
        entries = app.state.ledger.list_entries()
        print({"unguarded_payments": 5, "company_journal_entries": len(entries)})
        assert entries == []
        assert not any(event.type == "ledger.posted" for event in app.state.store.list_events(run.run_id))
        assert all(len(history["payments"]) == 6 for history in app.state.store.list_records("vendor_history"))
        reconciliation = await app.state.reconciler.reconcile()
        assert reconciliation.difference_cents == 0 and reconciliation.items == []
        assert all(exclusion.side == "bank" for exclusion in reconciliation.excluded)
        assert sum(exclusion.count for exclusion in reconciliation.excluded) == 5


@pytest.mark.asyncio
async def test_two_lanes_book_only_four_countersign_payments_and_reconcile_to_zero(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        _, run = await run_lanes(app, ["unguarded", "countersign"])
        assert run.tallies.unguarded.paid_count == 5
        assert (run.tallies.countersign.paid_count, run.tallies.countersign.held_count) == (4, 1)
        entries = app.state.ledger.list_entries(run_id=run.run_id)
        assert Counter(entry.source.kind for entry in entries) == {"invoice": 4, "payment": 4}
        cash = app.state.ledger.cash_entries()
        assert len(cash) == 4
        assert {app.state.store.get_record("transfers", row["transfer_id"])["lane"] for row in cash} == {"countersign"}
        events = app.state.store.list_events(run.run_id)
        held = next(event.payload.decision.invoice_id for event in events
                    if event.type == "policy.decision" and event.payload.decision.outcome == "HOLD")
        assert app.state.ledger.list_entries(invoice_id=held) == []
        assert sum(event.type == "ledger.posted" for event in events) == 8
        trial = app.state.ledger.trial_balance()
        assert trial.balanced
        assert next(account.balance_cents for account in trial.accounts if account.account_code == "1000") == -270000
        reconciliation = await app.state.reconciler.reconcile()
        assert reconciliation.difference_cents == 0
        assert reconciliation.bank_balance_change_cents == reconciliation.ledger_cash_change_cents == -270000
        assert len(reconciliation.items) == 4 and all(item.status == "matched" for item in reconciliation.items)
        assert [(item.reason, item.side, item.count) for item in reconciliation.excluded] == [("unguarded_lane", "bank", 5)]


@pytest.mark.asyncio
async def test_reset_keeps_legacy_journals_and_new_session_adds_only_countersign_entries(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch, previous = await run_lanes(app, ["unguarded"])
        payment_event = next(event for event in app.state.store.list_events(previous.run_id)
                             if event.type == "payment.executed")
        invoice = next(invoice for invoice in batch.invoices if invoice.invoice_id == payment_event.payload.invoice_id)
        # A pre-fix installation may contain comparison postings. Seed that
        # history through the journal API; reset must never delete/reverse it.
        app.state.ledger.post_bill({**invoice.model_dump(mode="json"), "currency": "USD"}, run_id=previous.run_id)
        app.state.ledger.post_payment(payment_event.payload.payment, run_id=previous.run_id,
            invoice_id=invoice.invoice_id, vendor_id=invoice.vendor_id)
        before = [entry.model_dump(mode="json") for entry in app.state.ledger.list_entries()]
        assert len(before) == 2
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/demo/reset", json={"autopilot": False})
            assert response.status_code == 200
        assert [entry.model_dump(mode="json") for entry in app.state.ledger.list_entries()] == before
        _, fresh = await run_lanes(app, ["unguarded", "countersign"])
        fresh_entries = app.state.ledger.list_entries(run_id=fresh.run_id)
        assert len(fresh_entries) == 8
        assert Counter(entry.source.kind for entry in fresh_entries) == {"invoice": 4, "payment": 4}
        assert [entry.model_dump(mode="json") for entry in app.state.ledger.list_entries(run_id=previous.run_id)] == before
        assert len(app.state.ledger.list_entries()) == 10
        assert not any(entry.source.kind == "reversal" for entry in app.state.ledger.list_entries())
        assert app.state.ledger.trial_balance().balanced
        reconciliation = await app.state.reconciler.reconcile()
        assert reconciliation.difference_cents == 0 and len(reconciliation.items) == 4
