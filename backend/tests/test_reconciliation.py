"""Independent bank activity, rather than saved successes, reconciles cash."""

from datetime import datetime, timezone

import pytest

from app.adapters.bank import BankActivity, BankError
from app.books.reconciliation import Reconciler
from app.config import Settings
from app.contract_models import JournalEntry, JournalLine, JournalSource, Payment, RunRequest
from app.main import create_app
from app.world.batches import create_standard_batch


def application(tmp_path):
    return create_app(Settings(
        _env_file=None, nessie_mode="offline", elastic_mode="offline", extractor_mode="offline",
        planner_mode="offline", agent_mode="offline", mutator_mode="offline",
        nessie_api_key="", elastic_api_key="", anthropic_api_key="", gemini_api_key="",
        bitdeer_api_key="", naive_api_key="", mutator_api_key="",
    ), db_path=tmp_path / "reconcile.sqlite")


def accounts(app):
    directory = app.state.store.list_records("bank_directory")
    company = next(item for item in directory if item["role"] == "company" and item["mode"] == "offline")
    vendor = next(item for item in directory if item.get("vendor_id") == "VEN-001" and item["mode"] == "offline")
    return company["account_id"], vendor["account_id"]


async def bank_payment(app, *, amount=12345, incoming=False):
    company, vendor = accounts(app)
    return await app.state.adapters.bank.transfer(
        from_account_id=vendor if incoming else company,
        to_account_id=company if incoming else vendor,
        amount_cents=amount, description="Planted reconciliation test movement",
    )


def cash_claim(app, *, amount, transfer_id, incoming=False):
    return app.state.ledger.post(JournalEntry(
        entry_id="test-cash-" + transfer_id, date=datetime.now(timezone.utc).date(),
        memo="Deliberately planted cash journal claim for reconciliation",
        source=JournalSource(kind="payment", id=transfer_id), created_by="person-controller",
        lines=[
            JournalLine(account_code="1000", account_name="Cash - Operating",
                        debit_cents=amount if incoming else 0, credit_cents=0 if incoming else amount),
            JournalLine(account_code="2000", account_name="Accounts Payable",
                        debit_cents=0 if incoming else amount, credit_cents=amount if incoming else 0),
        ],
    ), run_id="test-reconciliation", invoice_id="test-invoice", transfer_id=transfer_id, bank_mode="offline")


@pytest.mark.asyncio
async def test_full_both_lane_run_reconciles_countersign_books_to_zero_and_reports_the_unguarded_lane(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = create_standard_batch(app.state.store)
        initial = app.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["unguarded", "countersign"]))
        run = await app.state.run_manager.wait(initial.run_id)
        assert all(lane.status == "completed" for lane in run.lanes)
        paid = [event for event in app.state.store.list_events(run.run_id) if event.type == "payment.executed"]
        countersign = {e.payload.payment.transfer_id: e.payload.payment for e in paid if e.lane == "countersign"}
        unguarded = {e.payload.payment.transfer_id: e.payload.payment for e in paid if e.lane == "unguarded"}
        assert (len(countersign), len(unguarded)) == (4, 5)
        result = await app.state.reconciler.reconcile()
        # Countersign's books against Countersign-lane transfers only.
        assert result.difference_cents == 0
        assert result.bank_balance_change_cents == -sum(item.amount_cents for item in countersign.values())
        assert result.ledger_cash_change_cents == result.bank_balance_change_cents
        assert {item.transfer_id for item in result.items} == set(countersign)
        assert all(item.status == "matched" and item.journal_entry_id for item in result.items)
        assert all(app.state.ledger.get_entry(item.journal_entry_id) for item in result.items)
        # Fresh comparison payments exist only on the bank side. Legacy
        # comparison journals remain supported by the reconciliation filter.
        excluded = {(group.reason, group.side): group for group in result.excluded}
        bank_side = excluded[("unguarded_lane", "bank")]
        assert ("unguarded_lane", "ledger") not in excluded
        assert bank_side.count == 5
        assert set(bank_side.transfer_ids) == set(unguarded)
        assert bank_side.amount_cents == sum(item.amount_cents for item in unguarded.values())
        assert ("before_books_started", "bank") not in excluded
        assert result.scope == "countersign_lane"
        assert app.state.ledger.trial_balance().balanced is True
        # Only Countersign's four accepted bills and payments are booked.
        entries = app.state.ledger.list_entries(run_id=run.run_id)
        assert sum(entry.source.kind == "invoice" for entry in entries) == 4
        assert sum(entry.source.kind == "payment" for entry in entries) == 4
        posted = [event for event in app.state.store.list_events(run.run_id) if event.type == "ledger.posted"]
        assert len(posted) == len(entries)
        assert {event.payload.entry.entry_id for event in posted} == {entry.entry_id for entry in entries}
        assert all(event.mode == "offline" and event.lane == "countersign" for event in posted)


@pytest.mark.asyncio
@pytest.mark.parametrize("incoming", [False, True])
async def test_planted_untracked_bank_movement_is_bank_only_with_signed_difference(tmp_path, incoming):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        if incoming:
            # Fund the vendor before the baseline so an actual return transfer can occur.
            await bank_payment(app, amount=20000)
        await app.state.reconciler.ensure_baseline()
        payment = await bank_payment(app, incoming=incoming)
        result = await app.state.reconciler.reconcile()
        assert result.bank_balance_change_cents == (12345 if incoming else -12345)
        assert result.ledger_cash_change_cents == 0
        assert result.difference_cents == result.bank_balance_change_cents
        assert len(result.items) == 1
        item = result.items[0]
        assert (item.transfer_id, item.journal_entry_id, item.status) == (payment.transfer_id, None, "bank_only")
        assert item.amount_cents == 12345 and "No cash journal" in item.note


@pytest.mark.asyncio
async def test_planted_book_only_payment_is_not_hidden_by_a_hardcoded_zero(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        await app.state.reconciler.ensure_baseline()
        entry = cash_claim(app, amount=777, transfer_id="planted-no-bank-transfer")
        result = await app.state.reconciler.reconcile()
        assert (result.bank_balance_change_cents, result.ledger_cash_change_cents, result.difference_cents) == (0, -777, 777)
        assert len(result.items) == 1
        assert result.items[0].status == "ledger_only"
        assert result.items[0].transfer_id == "planted-no-bank-transfer"
        assert result.items[0].journal_entry_id == entry.entry_id


@pytest.mark.asyncio
@pytest.mark.parametrize("book_amount,book_incoming,difference", [(12000, False, -345), (12345, True, -24690)])
async def test_same_transfer_id_with_wrong_amount_or_direction_is_unmatched_both_ways(tmp_path, book_amount, book_incoming, difference):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        await app.state.reconciler.ensure_baseline()
        payment = await bank_payment(app)
        entry = cash_claim(app, amount=book_amount, transfer_id=payment.transfer_id, incoming=book_incoming)
        result = await app.state.reconciler.reconcile()
        assert result.difference_cents == difference
        assert sorted(item.status for item in result.items) == ["bank_only", "ledger_only"]
        assert {item.transfer_id for item in result.items} == {payment.transfer_id}
        assert next(item for item in result.items if item.status == "ledger_only").journal_entry_id == entry.entry_id
        assert "amount/direction" in next(item for item in result.items if item.status == "bank_only").note


@pytest.mark.asyncio
async def test_first_baseline_excludes_prior_ids_and_is_not_recaptured_after_payment(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        old = await bank_payment(app, amount=111)
        baseline = await app.state.reconciler.ensure_baseline()
        assert baseline["prior_activity_ids"] == [old.transfer_id]
        current = await bank_payment(app, amount=222)
        app.state.ledger.post_payment(current, run_id="test-run", invoice_id="invoice-current")
        restarted = Reconciler(app.state.store, app.state.ledger, app.state.adapters.bank)
        assert await restarted.ensure_baseline() == baseline
        result = await restarted.reconcile()
        assert result.difference_cents == 0
        assert {item.transfer_id for item in result.items} == {current.transfer_id}
        assert result.bank_balance_change_cents == -222


@pytest.mark.asyncio
async def test_transfer_from_before_the_books_started_is_excluded_with_a_count_not_hidden(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        # A current-session payment saved before ledger support (no journal entry, before the baseline).
        current = await bank_payment(app, amount=333)
        record = app.state.store.get_record("transfers", current.transfer_id)
        app.state.store.save_record("transfers", current.transfer_id, {
            **record, "session_started_at": app.state.store.session_started_at.isoformat(),
        })
        baseline = await app.state.reconciler.ensure_baseline()
        assert current.transfer_id not in baseline["prior_activity_ids"]
        result = await app.state.reconciler.reconcile()
        assert result.difference_cents == 0 and result.items == []
        [group] = result.excluded
        assert (group.reason, group.side, group.label) == ("before_books_started", "bank", "before books started")
        assert (group.count, group.amount_cents, group.transfer_ids) == (1, 333, [current.transfer_id])
        # A later payment with no journal entry is still a real, unexplained bank movement.
        later = await bank_payment(app, amount=50)
        again = await app.state.reconciler.reconcile()
        assert again.difference_cents == -50
        assert [(item.transfer_id, item.status) for item in again.items] == [(later.transfer_id, "bank_only")]


@pytest.mark.asyncio
async def test_bank_activity_this_app_never_submitted_still_counts_as_unmatched(tmp_path, monkeypatch):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        await app.state.reconciler.ensure_baseline()

        async def foreign_register(account_id):
            return [BankActivity(transfer_id="submitted-elsewhere", amount_cents=4000, cash_change_cents=-4000,
                                 status="pending", kind="withdrawals", mode="offline")]

        monkeypatch.setattr(app.state.adapters.bank, "account_activity", foreign_register)
        result = await app.state.reconciler.reconcile()
        assert result.difference_cents == -4000
        assert [(item.transfer_id, item.status) for item in result.items] == [("submitted-elsewhere", "bank_only")]
        assert result.excluded == []


@pytest.mark.asyncio
async def test_reset_starts_a_new_register_scope_without_erasing_books_or_reversals(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        first_baseline = await app.state.reconciler.ensure_baseline()
        paid = await bank_payment(app, amount=444)
        original = app.state.ledger.post_payment(paid, run_id="old-run", invoice_id="old-invoice")
        assert (await app.state.reconciler.reconcile()).difference_cents == 0
        app.state.store.reset_demo()
        next_baseline = await app.state.reconciler.ensure_baseline()
        assert next_baseline["session_id"] != first_baseline["session_id"]
        assert paid.transfer_id in next_baseline["prior_activity_ids"]
        assert app.state.ledger.get_entry(original.entry_id) == original
        empty = await app.state.reconciler.reconcile()
        assert empty.items == [] and empty.difference_cents == 0
        reversal = app.state.ledger.reverse(original.entry_id, created_by="person-controller", memo="Reconciliation correction")
        corrected = await app.state.reconciler.reconcile()
        assert corrected.bank_balance_change_cents == 0
        assert corrected.ledger_cash_change_cents == 444
        assert corrected.difference_cents == -444
        assert corrected.items[0].journal_entry_id == reversal.entry_id
        assert corrected.items[0].status == "ledger_only"
        assert "Reversals" in corrected.items[0].note


@pytest.mark.asyncio
async def test_pending_register_matches_do_not_claim_settled_bank_balance_moved(tmp_path, monkeypatch):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        bank = app.state.adapters.bank
        register = []

        async def independent_pending_register(account_id):
            return list(register)

        monkeypatch.setattr(bank, "account_activity", independent_pending_register)
        await app.state.reconciler.ensure_baseline()
        company, vendor = accounts(app)
        register.append(BankActivity(transfer_id="test-pending-bank-id", amount_cents=555,
            cash_change_cents=-555, status="pending", kind="withdrawals", mode="offline"))
        pending = Payment(transfer_id="test-pending-bank-id", transfer_status="pending", from_account_id=company,
            to_account_id=vendor, to_display_number="100100001111", payee_label="Harbor Light Rentals",
            payee_on_file=True, amount_cents=555, bank_mode="offline")
        app.state.ledger.post_payment(pending, run_id="pending-run", invoice_id="pending-invoice")
        result = await app.state.reconciler.reconcile()
        observation = await app.state.reconciler.balance_observation()
        assert result.difference_cents == 0
        assert result.bank_balance_change_cents == result.ledger_cash_change_cents == -555
        assert observation["observed_balance_change_cents"] == 0
        assert observation["basis"] == "submitted_payment_register_including_pending"
        assert result.items[0].status == "matched"
        assert "pending" in result.items[0].note and "not settled bank balance" in result.items[0].note


@pytest.mark.asyncio
async def test_new_bank_item_with_unknown_direction_fails_closed(tmp_path, monkeypatch):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        await app.state.reconciler.ensure_baseline()

        async def unknown_direction(account_id):
            return [BankActivity(transfer_id="unknown-route", amount_cents=999, cash_change_cents=None,
                                 status="pending", kind="transfers", mode="offline")]

        monkeypatch.setattr(app.state.adapters.bank, "account_activity", unknown_direction)
        with pytest.raises(BankError, match="unknown routing direction"):
            await app.state.reconciler.reconcile()
