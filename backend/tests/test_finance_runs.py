"""Production payment/event paths drive the books, including atomic failures."""

from collections import Counter
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.contract_models import Reconciliation, Run, RunLane, TrialBalance
from app.main import create_app
from app.world.batches import create_standard_batch


def application(tmp_path):
    return create_app(Settings(
        _env_file=None, nessie_mode="offline", elastic_mode="offline", extractor_mode="offline",
        planner_mode="offline", agent_mode="offline", mutator_mode="offline",
        nessie_api_key="", elastic_api_key="", anthropic_api_key="", gemini_api_key="",
        bitdeer_api_key="", naive_api_key="", mutator_api_key="",
    ), db_path=tmp_path / "finance-run.sqlite")


async def standard_run(app, client):
    response = await client.post("/api/batches", json={"kind": "standard"})
    assert response.status_code == 200, response.text
    batch = response.json()
    response = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
    assert response.status_code == 200, response.text
    run = await app.state.run_manager.wait(response.json()["run_id"])
    assert run.lanes[0].status == "completed"
    assert (run.tallies.countersign.paid_count, run.tallies.countersign.held_count) == (4, 1)
    return batch, run


def assert_entry_events(app, run_id, entries):
    events = [event for event in app.state.store.list_events(run_id) if event.type == "ledger.posted"]
    assert len(events) == len(entries)
    assert Counter(event.payload.entry.entry_id for event in events) == Counter(entry["entry_id"] for entry in entries)
    assert all(event.lane == "countersign" and event.mode == "offline" for event in events)
    assert {event.payload.entry.entry_id: event.payload.entry.model_dump(mode="json") for event in events} == {
        entry["entry_id"]: entry for entry in entries
    }


@pytest.mark.asyncio
async def test_standard_http_run_posts_only_accepted_invoices_and_reconciles_actual_cash(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch, run = await standard_run(app, client)
            response = await client.get("/api/ledger/entries", params={"run_id": run.run_id})
            assert response.status_code == 200, response.text
            entries = response.json()
            assert len(entries) == 8
            assert Counter(entry["source"]["kind"] for entry in entries) == {"invoice": 4, "payment": 4}
            assert all(entry["created_by"] == "person-ap-agent" for entry in entries)
            assert all(sum(line["debit_cents"] for line in entry["lines"]) == sum(line["credit_cents"] for line in entry["lines"])
                       for entry in entries)
            assert_entry_events(app, run.run_id, entries)
            decisions = (await client.get(f"/api/runs/{run.run_id}/decisions")).json()
            held = next(decision for decision in decisions if decision["outcome"] == "HOLD")
            assert held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
            assert (await client.get("/api/ledger/entries", params={"invoice_id": held["invoice_id"]})).json() == []
            expected_expense = {"VEN-001": "5100", "VEN-002": "5100", "VEN-004": "5200", "VEN-005": "5300"}
            for invoice in batch["invoices"]:
                filtered = (await client.get("/api/ledger/entries", params={
                    "run_id": run.run_id, "invoice_id": invoice["invoice_id"],
                })).json()
                vendor = (await client.get(f"/api/vendors/{invoice['vendor_id']}")).json()
                actual_history = [payment for payment in vendor["payments"]
                                  if not payment["journal_entry_id"].startswith("example-history-")]
                if invoice["invoice_id"] == held["invoice_id"]:
                    assert filtered == [] and actual_history == []
                    continue
                assert len(filtered) == 2
                bill = next(entry for entry in filtered if entry["source"]["kind"] == "invoice")
                payment = next(entry for entry in filtered if entry["source"]["kind"] == "payment")
                assert bill["source"]["id"] == invoice["invoice_id"]
                assert [(line["account_code"], line["debit_cents"], line["credit_cents"]) for line in bill["lines"]] == [
                    (expected_expense[invoice["vendor_id"]], invoice["amount_cents"], 0),
                    ("2000", 0, invoice["amount_cents"]),
                ]
                assert [(line["account_code"], line["debit_cents"], line["credit_cents"]) for line in payment["lines"]] == [
                    ("2000", invoice["amount_cents"], 0), ("1000", 0, invoice["amount_cents"]),
                ]
                assert len(actual_history) == 1
                assert actual_history[0]["journal_entry_id"] == payment["entry_id"]
                assert actual_history[0]["account_display"] == vendor["account_on_file_display"]
                assert actual_history[0]["amount_cents"] == invoice["amount_cents"]
            assert (await client.get("/api/ledger/entries", params={"run_id": "missing-run"})).json() == []
            trial = TrialBalance.model_validate((await client.get("/api/ledger/trial_balance")).json())
            assert trial.balanced is True
            assert {account.account_code for account in trial.accounts} == {"1000", "2000", "5100", "5200", "5300", "5400"}
            assert sum(account.debit_cents for account in trial.accounts) == sum(account.credit_cents for account in trial.accounts) == 540000
            balances = {account.account_code: account.balance_cents for account in trial.accounts}
            assert balances == {"1000": -270000, "2000": 0, "5100": 210000, "5200": 42000, "5300": 18000, "5400": 0}
            reconciliation = Reconciliation.model_validate((await client.get("/api/reconciliation")).json())
            assert reconciliation.difference_cents == 0
            assert reconciliation.bank_balance_change_cents == reconciliation.ledger_cash_change_cents == -270000
            assert len(reconciliation.items) == 4
            assert all(item.status == "matched" and item.journal_entry_id for item in reconciliation.items)
            assert {item.transfer_id for item in reconciliation.items} == {
                entry["source"]["id"] for entry in entries if entry["source"]["kind"] == "payment"
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["reject", "pay_vendor_on_file"])
async def test_human_resolution_posts_nothing_for_rejection_and_uses_actual_actor_for_payment(tmp_path, decision):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            _, run = await standard_run(app, client)
            approval = (await client.get("/api/approvals?status=pending")).json()[0]
            assert (await client.get("/api/ledger/entries", params={"invoice_id": approval["invoice_id"]})).json() == []
            result = await client.post(f"/api/approvals/{approval['approval_id']}", json={
                "decision": decision, "actor_id": "person-ap-manager", "note": "Independent review of trusted on-file account.",
            })
            assert result.status_code == 200, result.text
            resolved = result.json()
            entries = (await client.get("/api/ledger/entries", params={"run_id": run.run_id})).json()
            filtered = (await client.get("/api/ledger/entries", params={"invoice_id": approval["invoice_id"]})).json()
            if decision == "reject":
                assert filtered == [] and len(entries) == 8
                assert "transfer_id" not in resolved["resolution"]
            else:
                assert len(filtered) == 2 and len(entries) == 10
                assert all(entry["created_by"] == "person-ap-manager" for entry in filtered)
                cash = next(entry for entry in filtered if entry["source"]["kind"] == "approval")
                assert cash["source"]["id"] == approval["approval_id"]
                metadata = next(item for item in app.state.ledger.cash_entries() if item["entry_id"] == cash["entry_id"])
                assert metadata["transfer_id"] == resolved["resolution"]["transfer_id"]
                payment = app.state.store.get_record("transfers", metadata["transfer_id"])
                assert payment["to_display_number"] == approval["account_on_file_display"]
                assert payment["created_by"] == "person-ap-manager"
            assert_entry_events(app, run.run_id, entries)
            assert (await client.get("/api/ledger/trial_balance")).json()["balanced"] is True
            reconciliation = (await client.get("/api/reconciliation")).json()
            assert reconciliation["difference_cents"] == 0
            assert all(item["status"] == "matched" for item in reconciliation["items"])


def save_test_run(app, batch, *, lane):
    run = Run(run_id="run-atomic-finance", batch_id=batch.batch_id, instruction="Atomic finance integration test",
              created_at=batch.created_at, lanes=[RunLane(lane=lane, status="running")])
    app.state.store.save_run(run)
    return run


@pytest.mark.asyncio
async def test_payment_event_failure_rolls_back_books_history_and_posted_events_but_flags_real_bank_money(tmp_path, monkeypatch):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = create_standard_batch(app.state.store)
        invoice = batch.invoices[0]
        run = save_test_run(app, batch, lane="unguarded")
        await app.state.reconciler.ensure_baseline()
        directory = app.state.store.list_records("bank_directory")
        company = next(row for row in directory if row["role"] == "company" and row["mode"] == "offline")
        target = next(row for row in directory if row.get("vendor_id") == invoice.vendor_id and row["mode"] == "offline")
        payment = await app.state.adapters.bank.transfer(from_account_id=company["account_id"],
            to_account_id=target["account_id"], amount_cents=invoice.amount_cents,
            description="Actual offline bank movement before deliberately failed journal event")
        previous = app.state.store.get_record("transfers", payment.transfer_id)
        app.state.store.save_record("transfers", payment.transfer_id, {
            **previous, "run_id": run.run_id, "invoice_id": invoice.invoice_id,
            "vendor_id": invoice.vendor_id, "invoice_number": invoice.invoice_number,
            "created_by": "person-ap-agent", "session_started_at": app.state.store.session_started_at.isoformat(),
        })
        original_insert = app.state.store._insert_event

        def fail_payment_posted(connection, event):
            if event.type == "ledger.posted" and event.payload.entry.source.kind == "payment":
                raise RuntimeError("Planted ledger.posted insert failure")
            return original_insert(connection, event)

        monkeypatch.setattr(app.state.store, "_insert_event", fail_payment_posted)
        with pytest.raises(RuntimeError, match="Planted ledger.posted"):
            await app.state.event_bus.publish(run.run_id, {
                "type": "payment.executed", "lane": "unguarded", "mode": "offline",
                "provider": "offline", "model": "offline-atomic-test", "unguarded_mode": "ap",
                "payload": {"invoice_id": invoice.invoice_id, "via": "agent", "payment": payment.model_dump(mode="python")},
            })
        assert app.state.store.list_events(run.run_id) == []
        assert app.state.ledger.list_entries(run_id=run.run_id) == []
        assert len(app.state.store.get_record("vendor_history", invoice.vendor_id)["payments"]) == 6
        assert app.state.store.get_record("transfers", payment.transfer_id) is not None
        result = await app.state.reconciler.reconcile()
        assert result.difference_cents == -invoice.amount_cents
        assert [(item.transfer_id, item.status) for item in result.items] == [(payment.transfer_id, "bank_only")]
        assert app.state.ledger.trial_balance().balanced is True


@pytest.mark.asyncio
async def test_accepted_bill_event_failure_prevents_bank_transfer_and_rolls_back_invoice_claim(tmp_path, monkeypatch):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = create_standard_batch(app.state.store)
        invoice = batch.invoices[0]
        run = save_test_run(app, batch, lane="countersign")
        vendor = app.state.store.get_record("vendors", invoice.vendor_id)
        bank = app.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        original_insert = app.state.store._insert_event

        def fail_bill_posted(connection, event):
            if event.type == "ledger.posted" and event.payload.entry.source.kind == "invoice":
                raise RuntimeError("Planted accepted-bill event failure")
            return original_insert(connection, event)

        monkeypatch.setattr(app.state.store, "_insert_event", fail_bill_posted)
        arguments = dict(run_id=run.run_id, invoice_id=invoice.invoice_id, invoice_number=invoice.invoice_number,
                         vendor_id=invoice.vendor_id, amount_cents=invoice.amount_cents,
                         to_display_number=vendor["account_on_file_display"], via="policy", effective_mode="offline")
        with pytest.raises(RuntimeError, match="Planted accepted-bill"):
            await app.state.payment_executor.execute(**arguments)
        bank.transfer.assert_not_awaited()
        assert not app.state.store.has_session_payment(invoice.invoice_number)
        assert app.state.ledger.list_entries(run_id=run.run_id) == []
        assert app.state.store.list_events(run.run_id) == []
        assert len(app.state.store.get_record("vendor_history", invoice.vendor_id)["payments"]) == 6
        assert (await app.state.reconciler.reconcile()).difference_cents == 0
        monkeypatch.setattr(app.state.store, "_insert_event", original_insert)
        payment = await app.state.payment_executor.execute(**arguments)
        assert bank.transfer.await_count == 1
        assert app.state.store.has_session_payment(invoice.invoice_number)
        assert len(app.state.ledger.list_entries(run_id=run.run_id)) == 2
        result = await app.state.reconciler.reconcile()
        assert result.difference_cents == 0
        assert [(item.transfer_id, item.status) for item in result.items] == [(payment.transfer_id, "matched")]


@pytest.mark.asyncio
async def test_reversal_adds_one_immutable_entry_and_event_in_the_original_run(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            _, run = await standard_run(app, client)
        original = next(entry for entry in app.state.ledger.list_entries(run_id=run.run_id)
                        if entry.source.kind == "invoice")
        args = dict(created_by="person-controller", memo="Correct the accepted bill with an appended reversal")
        correction = await app.state.event_bus.reverse_entry(original.entry_id, **args)
        assert correction.source.kind == "reversal" and correction.source.id == original.entry_id
        assert correction.created_by == "person-controller"
        assert [(line.debit_cents, line.credit_cents) for line in correction.lines] == [
            (line.credit_cents, line.debit_cents) for line in original.lines]
        assert await app.state.event_bus.reverse_entry(original.entry_id, **args) == correction
        assert app.state.ledger.get_entry(original.entry_id) == original
        events = [event for event in app.state.store.list_events(run.run_id)
                  if event.type == "ledger.posted" and event.payload.entry.entry_id == correction.entry_id]
        assert len(events) == 1 and events[0].mode == "offline"
        assert len(app.state.ledger.list_entries(run_id=run.run_id)) == 9
        assert app.state.ledger.trial_balance().balanced is True
