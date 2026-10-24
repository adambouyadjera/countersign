"""The ledger rejects invalid financial facts and preserves immutable journals."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.books.ledger import CHART_OF_ACCOUNTS, Ledger, LedgerConflictError, LedgerError
from app.contract_models import JournalEntry, JournalLine, JournalSource, Payment
from app.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "books.sqlite")
    instance.initialize()
    yield instance
    instance.close()


@pytest.fixture
def ledger(store):
    return Ledger(store)


def journal(*, identifier="entry-1", amount=12000, source_kind="seed", source_id="source-1", creator="person-controller"):
    return JournalEntry(entry_id=identifier, date="2026-10-24", memo="Accepted rental bill",
        source=JournalSource(kind=source_kind, id=source_id), created_by=creator,
        lines=[JournalLine(account_code="5100", account_name=CHART_OF_ACCOUNTS["5100"], debit_cents=amount, credit_cents=0),
               JournalLine(account_code="2000", account_name=CHART_OF_ACCOUNTS["2000"], debit_cents=0, credit_cents=amount)])


def invoice(identifier="invoice-1", *, vendor_id="VEN-001", amount=12000):
    return {"invoice_id": identifier, "invoice_number": "INV-001", "vendor_id": vendor_id,
            "vendor_name": "Harbor Light Rentals", "amount_cents": amount, "currency": "USD"}


def payment(identifier="transfer-1", *, amount=12000, status="pending"):
    return Payment(transfer_id=identifier, transfer_status=status, from_account_id="company", to_account_id="vendor",
                   to_display_number="100100001111", payee_label="Harbor Light Rentals", payee_on_file=True,
                   amount_cents=amount, bank_mode="offline")


def test_unbalanced_entry_is_rejected_without_side_effect(ledger):
    entry = journal()
    entry.lines[1].credit_cents -= 1
    with pytest.raises(LedgerError, match="balance"):
        ledger.post(entry)
    assert ledger.list_entries() == []
    assert ledger.trial_balance().balanced is True


@pytest.mark.parametrize("debit,credit", [(-1, 0), (0, -1), (1, 1), (0, 0), (True, 0), (1.0, 0)])
def test_cent_values_are_positive_exact_integers_on_one_side(ledger, debit, credit):
    entry = journal().model_dump(mode="python")
    entry["lines"][0].update(debit_cents=debit, credit_cents=credit)
    with pytest.raises(LedgerError):
        ledger.post(entry)
    assert ledger.list_entries() == []


@pytest.mark.parametrize("field", ["created_by", "memo", "entry_id"])
def test_journal_identity_memo_and_creator_are_required(ledger, field):
    entry = journal().model_copy(update={field: " "})
    with pytest.raises(LedgerError, match="required"):
        ledger.post(entry)


def test_source_identity_and_chart_names_are_not_caller_defined(ledger):
    entry = journal()
    entry.source.id = ""
    with pytest.raises(LedgerError):
        ledger.post(entry)
    entry = journal()
    entry.lines[0].account_name = "Attacker expense"
    with pytest.raises(LedgerError, match="chart"):
        ledger.post(entry)
    entry.lines[0].account_code = "9999"
    with pytest.raises(LedgerError, match="chart"):
        ledger.post(entry)


def test_sqlite_prevents_updates_and_deletes(ledger, store):
    posted = ledger.post(journal())
    for sql in ("UPDATE ledger_journal SET created_by = 'person-attacker' WHERE entry_id = ?",
                "DELETE FROM ledger_journal WHERE entry_id = ?"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            store._db().execute(sql, (posted.entry_id,))
    assert ledger.get_entry(posted.entry_id) == posted


def test_sqlite_replace_cannot_bypass_immutability(ledger, store):
    original = ledger.post(journal())
    row = dict(store._db().execute("SELECT * FROM ledger_journal").fetchone())
    row["created_by"] = "person-attacker"
    payload = json.loads(row["data"])
    payload["created_by"] = row["created_by"]
    row["data"] = json.dumps(payload)
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._db().execute("INSERT OR REPLACE INTO ledger_journal (" + columns + ") VALUES (" + placeholders + ")", list(row.values()))
    assert ledger.get_entry(original.entry_id) == original


def test_sqlite_independently_rejects_unbalanced_payload(ledger, store):
    ledger.initialize()
    payload = journal().model_dump(mode="json")
    payload["lines"][0]["debit_cents"] += 1
    with pytest.raises(sqlite3.IntegrityError, match="balance"):
        store._db().execute(
            "INSERT INTO ledger_journal (entry_id, entry_date, posted_at, source_kind, source_id, created_by, "
            "idempotency_key, cash_change_cents, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("entry-1", "2026-10-24", datetime.now(timezone.utc).isoformat(), "seed", "source-1",
             "person-controller", "key", 0, json.dumps(payload)),
        )
    assert ledger.list_entries() == []


def test_bill_and_payment_posting_rules_and_trial_balance(ledger):
    bill, fresh = ledger.post_bill_with_status(invoice(), run_id="run-1")
    assert fresh is True
    assert bill.source.kind == "invoice" and bill.source.id == "invoice-1"
    assert [(line.account_code, line.debit_cents, line.credit_cents) for line in bill.lines] == [
        ("5100", 12000, 0), ("2000", 0, 12000),
    ]
    paid, fresh = ledger.post_payment_with_status(payment(), run_id="run-1", invoice_id="invoice-1", vendor_id="VEN-001")
    assert fresh is True
    assert paid.source.kind == "payment" and paid.source.id == "transfer-1"
    assert [(line.account_code, line.debit_cents, line.credit_cents) for line in paid.lines] == [
        ("2000", 12000, 0), ("1000", 0, 12000),
    ]
    balance = ledger.trial_balance()
    assert balance.balanced is True
    accounts = {account.account_code: account for account in balance.accounts}
    assert set(accounts) == set(CHART_OF_ACCOUNTS)
    assert accounts["1000"].balance_cents == -12000
    assert accounts["2000"].balance_cents == 0
    assert accounts["5100"].balance_cents == 12000
    assert sum(account.debit_cents for account in balance.accounts) == sum(account.credit_cents for account in balance.accounts) == 24000


@pytest.mark.parametrize("vendor,expense", [("VEN-001", "5100"), ("VEN-002", "5100"), ("VEN-003", "5400"), ("VEN-004", "5200"), ("VEN-005", "5300")])
def test_trusted_vendor_classification_selects_expense_account(ledger, vendor, expense):
    assert ledger.post_bill(invoice(vendor_id=vendor), run_id="run-1").lines[0].account_code == expense


def test_approval_payment_records_the_human_and_actual_transfer(ledger):
    posted = ledger.post_payment(payment(), run_id="run-1", invoice_id="invoice-1", vendor_id="VEN-001",
                                 created_by="person-ap-manager", approval_id="approval-1")
    assert posted.created_by == "person-ap-manager"
    assert (posted.source.kind, posted.source.id) == ("approval", "approval-1")
    cash = ledger.cash_entries()
    assert len(cash) == 1
    assert cash[0]["transfer_id"] == "transfer-1"
    assert cash[0]["entry_id"] == posted.entry_id
    assert cash[0]["cash_change_cents"] == -12000
    assert cash[0]["source_id"] == "approval-1"
    assert cash[0]["bank_mode"] == "offline"
    assert ledger.cash_entries(bank_mode="live") == []


def test_entry_context_preserves_original_private_routing_metadata(ledger):
    posted = ledger.post_payment(payment(), run_id="original-run", invoice_id="original-invoice", vendor_id="VEN-001")
    assert ledger.entry_context(posted.entry_id) == {
        "run_id": "original-run", "invoice_id": "original-invoice", "vendor_id": "VEN-001", "bank_mode": "offline",
    }
    assert ledger.entry_context("missing-entry") is None
    context = ledger.entry_context(posted.entry_id)
    context["run_id"] = "rewritten-run"
    assert ledger.entry_context(posted.entry_id)["run_id"] == "original-run"


def test_repeated_sources_do_not_double_post_and_conflicts_do_not_hide(ledger):
    bill, created = ledger.post_bill_with_status(invoice(), run_id="run-1")
    assert created
    repeated, created = ledger.post_bill_with_status(invoice(), run_id="run-2", created_by="person-ap-manager")
    assert repeated == bill and created is False
    paid, created = ledger.post_payment_with_status(payment(), run_id="run-1", invoice_id="invoice-1")
    assert created
    repeated, created = ledger.post_payment_with_status(payment(), run_id="run-1", invoice_id="invoice-1")
    assert repeated == paid and created is False
    with pytest.raises(LedgerConflictError):
        ledger.post_bill(invoice(amount=12001), run_id="run-1")
    with pytest.raises(LedgerConflictError):
        ledger.post_payment(payment(amount=12001), run_id="run-1", invoice_id="invoice-1")
    with pytest.raises(LedgerConflictError):
        ledger.post_payment(payment(), run_id="run-1", invoice_id="invoice-2")
    assert len(ledger.list_entries()) == 2


def test_generic_post_is_idempotent_and_rejects_changed_entry(ledger):
    entry = journal()
    assert ledger.post_with_status(entry) == (entry, True)
    assert ledger.post_with_status(entry) == (entry, False)
    with pytest.raises(LedgerConflictError):
        ledger.post(entry.model_copy(update={"memo": "rewrite"}))


def test_filters_are_intersected_and_persist_across_store_restarts(ledger, store):
    first = ledger.post_bill(invoice(), run_id="run-1")
    second = ledger.post_bill(invoice("invoice-2"), run_id="run-2")
    assert ledger.list_entries(run_id="run-1") == [first]
    assert ledger.list_entries(invoice_id="invoice-2") == [second]
    assert ledger.list_entries(run_id="run-1", invoice_id="invoice-2") == []
    restarted = Store(store.path)
    restarted.initialize()
    try:
        assert Ledger(restarted).list_entries() == [first, second]
    finally:
        restarted.close()


def test_corrections_are_reversals_and_originals_never_change(ledger):
    original = ledger.post(journal())
    correction = ledger.reverse(original.entry_id, created_by="person-controller", memo="Reverse incorrect coding")
    assert ledger.get_entry(original.entry_id) == original
    assert correction.source.kind == "reversal" and correction.source.id == original.entry_id
    assert correction.created_by == "person-controller"
    assert [(line.debit_cents, line.credit_cents) for line in correction.lines] == [(0, 12000), (12000, 0)]
    assert ledger.reverse(original.entry_id, created_by="person-controller", memo="Reverse incorrect coding") == correction
    assert len(ledger.list_entries()) == 2
    assert all(account.balance_cents == 0 for account in ledger.trial_balance().accounts)


@pytest.mark.parametrize("status", ["failed", "rejected", "cancelled", "error", "held", "blocked"])
def test_failed_bank_submission_cannot_be_booked_as_payment(ledger, status):
    with pytest.raises(LedgerError, match="failed|rejected"):
        ledger.post_payment(payment(status=status), run_id="run-1", invoice_id="invoice-1")
    assert ledger.list_entries() == []


def test_cash_entry_filter_and_demo_reset_keep_immutable_books(ledger, store):
    before = datetime.now(timezone.utc)
    posted = ledger.post_payment(payment(), run_id="run-1", invoice_id="invoice-1")
    assert ledger.cash_entries(since=before)[0]["entry_id"] == posted.entry_id
    assert ledger.cash_entries(since=datetime.now(timezone.utc) + timedelta(seconds=1)) == []
    store.reset_demo()
    assert ledger.get_entry(posted.entry_id) == posted


def test_concurrent_store_connections_book_one_transfer_once(store):
    path = store.path

    def post_once(_):
        local = Store(path)
        local.initialize()
        try:
            return Ledger(local).post_payment_with_status(payment(), run_id="run-1", invoice_id="invoice-1")
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(post_once, range(8)))
    assert sum(fresh for _, fresh in results) == 1
    assert len(Ledger(store).list_entries()) == 1
