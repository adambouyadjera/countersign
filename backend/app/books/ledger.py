"""Immutable double-entry books; a journal is a financial fact, not a balance edit.

Journal metadata and its complete contract payload are inserted together in
SQLite. SQL triggers prohibit updating/deleting entries and independently check
the cent totals. Corrections add an explicitly linked reversing entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib

from app.contract_models import JournalEntry, JournalLine, JournalSource, Payment, TrialBalance, TrialBalanceAccount


CHART_OF_ACCOUNTS = {
    "1000": "Cash - Operating",
    "2000": "Accounts Payable",
    "5100": "Production Costs",
    "5200": "Security and Staffing",
    "5300": "Artist Fees",
    "5400": "Facilities",
}
_EXPENSE_BY_VENDOR = {"VEN-003": "5400", "VEN-004": "5200", "VEN-005": "5300"}
_FAILED = {"failed", "rejected", "cancelled", "canceled", "error", "held", "hold", "blocked"}
_MAX_CENTS = 2**63 - 1


class LedgerError(ValueError):
    """An entry cannot be accepted into the books."""


class LedgerConflictError(LedgerError):
    """An existing financial identity describes different facts."""


def _identity(prefix, value):
    return "journal-" + hashlib.sha256(f"{prefix}:{value}".encode()).hexdigest()[:32]


def _line(account_code, *, debit=0, credit=0):
    return JournalLine(account_code=account_code, account_name=CHART_OF_ACCOUNTS[account_code],
                       debit_cents=debit, credit_cents=credit)


def _value(record, name, default=None):
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)


class Ledger:
    def __init__(self, store):
        self.store = store
        self._initialized_connection = None

    def initialize(self):
        """Create dedicated tables on the Store connection without implicit commits."""
        with self.store._lock:
            connection = self.store._db()
            if self._initialized_connection is connection:
                return
            connection.execute(
                "CREATE TABLE IF NOT EXISTS ledger_journal ("
                "entry_id TEXT PRIMARY KEY, entry_date TEXT NOT NULL, posted_at TEXT NOT NULL, "
                "source_kind TEXT NOT NULL, source_id TEXT NOT NULL, created_by TEXT NOT NULL, "
                "run_id TEXT, invoice_id TEXT, vendor_id TEXT, transfer_id TEXT UNIQUE, bank_mode TEXT, "
                "idempotency_key TEXT NOT NULL UNIQUE, cash_change_cents INTEGER NOT NULL, "
                "data TEXT NOT NULL CHECK(json_valid(data)), UNIQUE(source_kind, source_id))"
            )
            if "bank_mode" not in {row[1] for row in connection.execute("PRAGMA table_info(ledger_journal)")}:
                connection.execute("ALTER TABLE ledger_journal ADD COLUMN bank_mode TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS ledger_run ON ledger_journal(run_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS ledger_invoice ON ledger_journal(invoice_id)")
            connection.execute(
                "CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger_journal "
                "BEGIN SELECT RAISE(ABORT, 'Journal entries are immutable; post a reversal'); END"
            )
            connection.execute(
                "CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger_journal "
                "BEGIN SELECT RAISE(ABORT, 'Journal entries are immutable; post a reversal'); END"
            )
            connection.execute(
                "CREATE TRIGGER IF NOT EXISTS ledger_no_replace BEFORE INSERT ON ledger_journal "
                "WHEN EXISTS (SELECT 1 FROM ledger_journal WHERE entry_id = NEW.entry_id "
                "OR idempotency_key = NEW.idempotency_key "
                "OR (source_kind = NEW.source_kind AND source_id = NEW.source_id) "
                "OR (NEW.transfer_id IS NOT NULL AND transfer_id = NEW.transfer_id)) "
                "BEGIN SELECT RAISE(ABORT, 'Journal identities are immutable; post a reversal'); END"
            )
            connection.execute("""
                CREATE TRIGGER IF NOT EXISTS ledger_validate_insert BEFORE INSERT ON ledger_journal
                BEGIN
                    SELECT CASE WHEN json_type(NEW.data, '$.lines') != 'array'
                        OR json_array_length(NEW.data, '$.lines') < 2
                        THEN RAISE(ABORT, 'A journal requires at least two lines') END;
                    SELECT CASE WHEN EXISTS (
                        SELECT 1 FROM json_each(NEW.data, '$.lines') WHERE
                        json_type(value, '$.debit_cents') != 'integer' OR
                        json_type(value, '$.credit_cents') != 'integer' OR
                        json_extract(value, '$.debit_cents') < 0 OR
                        json_extract(value, '$.credit_cents') < 0 OR
                        (json_extract(value, '$.debit_cents') = 0 AND json_extract(value, '$.credit_cents') = 0) OR
                        (json_extract(value, '$.debit_cents') > 0 AND json_extract(value, '$.credit_cents') > 0)
                        ) THEN RAISE(ABORT, 'Invalid journal cent amounts') END;
                    SELECT CASE WHEN
                        (SELECT SUM(json_extract(value, '$.debit_cents')) FROM json_each(NEW.data, '$.lines')) !=
                        (SELECT SUM(json_extract(value, '$.credit_cents')) FROM json_each(NEW.data, '$.lines'))
                        THEN RAISE(ABORT, 'Journal debits and credits must balance') END;
                    SELECT CASE WHEN NEW.cash_change_cents != (
                        SELECT COALESCE(SUM(json_extract(value, '$.debit_cents') - json_extract(value, '$.credit_cents')), 0)
                        FROM json_each(NEW.data, '$.lines') WHERE json_extract(value, '$.account_code') = '1000')
                        THEN RAISE(ABORT, 'Cash metadata must match the journal') END;
                END
            """)
            self._initialized_connection = connection

    @staticmethod
    def _validated(entry):
        try:
            entry = JournalEntry.model_validate(entry.model_dump(mode="python")
                                                if isinstance(entry, JournalEntry) else entry).model_copy(deep=True)
        except (TypeError, ValueError):
            raise LedgerError("Journal entry does not match the contract schema") from None
        if any(not value.strip() for value in (entry.entry_id, entry.memo, entry.source.id, entry.created_by)):
            raise LedgerError("Journal identity, memo, source and created_by are required")
        if len(entry.lines) < 2:
            raise LedgerError("A journal requires at least two lines")
        for line in entry.lines:
            if line.account_code not in CHART_OF_ACCOUNTS or line.account_name != CHART_OF_ACCOUNTS[line.account_code]:
                raise LedgerError("Journal line must use the trusted chart of accounts")
            if (type(line.debit_cents) is not int or type(line.credit_cents) is not int
                    or min(line.debit_cents, line.credit_cents) < 0
                    or max(line.debit_cents, line.credit_cents) > _MAX_CENTS
                    or (line.debit_cents == 0) == (line.credit_cents == 0)):
                raise LedgerError("Each line needs one positive integer-cent debit or credit")
        debits = sum(line.debit_cents for line in entry.lines)
        credits = sum(line.credit_cents for line in entry.lines)
        if debits != credits:
            raise LedgerError("Journal debits and credits must balance")
        if debits > _MAX_CENTS:
            raise LedgerError("Journal totals exceed the supported integer-cent range")
        return entry

    def post(self, entry, **metadata) -> JournalEntry:
        return self.post_with_status(entry, **metadata)[0]

    def post_with_status(self, entry, *, run_id=None, invoice_id=None, vendor_id=None,
                         transfer_id=None, idempotency_key=None, bank_mode=None) -> tuple[JournalEntry, bool]:
        """Insert once or return the identical existing entry and ``False``.

        Caller-controlled idempotency keys cannot mask conflicting financial
        facts. ``True`` identifies the unique insertion for ledger.posted.
        """
        entry = self._validated(entry)
        key = idempotency_key or "entry:" + entry.entry_id
        if not isinstance(key, str) or not key.strip():
            raise LedgerError("A nonempty journal idempotency key is required")
        if transfer_id is not None and (not isinstance(transfer_id, str) or not transfer_id.strip()):
            raise LedgerError("A bank transfer identity must be a nonempty string")
        if bank_mode not in (None, "live", "offline", "dry_run"):
            raise LedgerError("The journal bank mode must be explicit and valid")
        self.initialize()
        with self.store._lock:
            connection = self.store._db()
            nested = connection.in_transaction
            connection.execute("SAVEPOINT ledger_post" if nested else "BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT * FROM ledger_journal WHERE entry_id = ? OR idempotency_key = ? "
                    "OR (source_kind = ? AND source_id = ?) OR (transfer_id IS NOT NULL AND transfer_id = ?)",
                    (entry.entry_id, key, entry.source.kind, entry.source.id, transfer_id),
                ).fetchall()
                if rows:
                    if len(rows) != 1:
                        raise LedgerConflictError("Journal identities refer to different existing entries")
                    row = rows[0]
                    existing = JournalEntry.model_validate_json(row["data"])
                    if (existing != entry or row["transfer_id"] != transfer_id or row["invoice_id"] != invoice_id
                            or row["vendor_id"] != vendor_id or row["bank_mode"] != bank_mode):
                        raise LedgerConflictError("Existing journal identity has different financial facts")
                    result = existing, False
                else:
                    cash = sum(line.debit_cents - line.credit_cents for line in entry.lines if line.account_code == "1000")
                    connection.execute(
                        "INSERT INTO ledger_journal (entry_id, entry_date, posted_at, source_kind, source_id, created_by, "
                        "run_id, invoice_id, vendor_id, transfer_id, bank_mode, idempotency_key, cash_change_cents, data) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (entry.entry_id, entry.date.isoformat(), datetime.now(timezone.utc).isoformat(),
                         entry.source.kind, entry.source.id, entry.created_by, run_id, invoice_id,
                         vendor_id, transfer_id, bank_mode, key, cash, entry.model_dump_json()),
                    )
                    result = entry, True
                connection.execute("RELEASE SAVEPOINT ledger_post") if nested else connection.commit()
                return result
            except BaseException:
                if nested:
                    connection.execute("ROLLBACK TO SAVEPOINT ledger_post")
                    connection.execute("RELEASE SAVEPOINT ledger_post")
                else:
                    connection.rollback()
                raise

    def get_entry(self, entry_id):
        self.initialize()
        with self.store._lock:
            row = self.store._db().execute("SELECT data FROM ledger_journal WHERE entry_id = ?", (entry_id,)).fetchone()
            return JournalEntry.model_validate_json(row["data"]) if row else None

    def entry_context(self, entry_id):
        """Return original private provenance for correction/event routing."""
        self.initialize()
        with self.store._lock:
            row = self.store._db().execute(
                "SELECT run_id, invoice_id, vendor_id, bank_mode FROM ledger_journal WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_entries(self, *, run_id=None, invoice_id=None):
        self.initialize()
        where, parameters = [], []
        for column, value in (("run_id", run_id), ("invoice_id", invoice_id)):
            if value is not None:
                where.append(column + " = ?")
                parameters.append(value)
        query = "SELECT data FROM ledger_journal" + (" WHERE " + " AND ".join(where) if where else "")
        with self.store._lock:
            rows = self.store._db().execute(query + " ORDER BY posted_at, entry_id", parameters).fetchall()
            return [JournalEntry.model_validate_json(row["data"]) for row in rows]

    def cash_entries(self, *, since=None, bank_mode=None):
        self.initialize()
        where, parameters = "cash_change_cents != 0", []
        if since is not None:
            where += " AND posted_at >= ?"
            parameters.append(since.isoformat() if isinstance(since, datetime) else str(since))
        if bank_mode is not None:
            where += " AND (bank_mode = ? OR bank_mode IS NULL)"
            parameters.append(bank_mode)
        with self.store._lock:
            rows = self.store._db().execute(
                "SELECT entry_id, entry_date, posted_at, source_kind, source_id, created_by, "
                "run_id, invoice_id, vendor_id, transfer_id, bank_mode, cash_change_cents FROM ledger_journal WHERE "
                + where + " ORDER BY posted_at, entry_id", parameters,
            ).fetchall()
            return [dict(row) for row in rows]

    def trial_balance(self):
        totals = {code: [0, 0] for code in CHART_OF_ACCOUNTS}
        for entry in self.list_entries():
            for line in entry.lines:
                totals[line.account_code][0] += line.debit_cents
                totals[line.account_code][1] += line.credit_cents
        accounts = [TrialBalanceAccount(account_code=code, account_name=name,
                    debit_cents=totals[code][0], credit_cents=totals[code][1],
                    balance_cents=totals[code][0] - totals[code][1])
                    for code, name in CHART_OF_ACCOUNTS.items()]
        return TrialBalance(as_of=datetime.now(timezone.utc), accounts=accounts,
                            balanced=sum(item.debit_cents for item in accounts) == sum(item.credit_cents for item in accounts))

    def _reuse_helper(self, entry, *, lookup, parameters, **metadata):
        entry = self._validated(entry)
        self.initialize()
        with self.store._lock:
            row = self.store._db().execute("SELECT * FROM ledger_journal WHERE " + lookup, parameters).fetchone()
            if row is not None:
                existing = JournalEntry.model_validate_json(row["data"])
                if (existing.source != entry.source or existing.lines != entry.lines or existing.memo != entry.memo
                        or row["invoice_id"] != metadata.get("invoice_id")
                        or row["vendor_id"] != metadata.get("vendor_id")
                        or row["bank_mode"] != metadata.get("bank_mode")
                        or row["transfer_id"] != metadata.get("transfer_id")):
                    raise LedgerConflictError("Existing financial source has different journal facts")
                return existing, False
            return self.post_with_status(entry, **metadata)

    def post_bill(self, invoice, *, run_id, created_by="person-ap-agent"):
        return self.post_bill_with_status(invoice, run_id=run_id, created_by=created_by)[0]

    def post_bill_with_status(self, invoice, *, run_id, created_by="person-ap-agent"):
        identifier, amount = _value(invoice, "invoice_id"), _value(invoice, "amount_cents")
        if not isinstance(identifier, str) or not identifier.strip() or type(amount) is not int or amount <= 0:
            raise LedgerError("An accepted bill needs an invoice identity and a positive integer-cent amount")
        if _value(invoice, "currency", "USD") != "USD":
            raise LedgerError("The demo ledger accepts USD bills only")
        vendor_id = _value(invoice, "vendor_id")
        vendor = self.store.get_record("vendors", vendor_id) if vendor_id else None
        expense = (vendor or {}).get("expense_account_code", _EXPENSE_BY_VENDOR.get(vendor_id, "5100"))
        if expense not in {"5100", "5200", "5300", "5400"}:
            raise LedgerError("Vendor expense classification is not in the trusted chart")
        entry = JournalEntry(entry_id=_identity("bill", identifier), date=datetime.now(timezone.utc).date(),
            memo=f"Accepted bill {_value(invoice, 'invoice_number', identifier)} — {_value(invoice, 'vendor_name', vendor_id or 'vendor')}",
            source=JournalSource(kind="invoice", id=identifier), created_by=created_by,
            lines=[_line(expense, debit=amount), _line("2000", credit=amount)])
        return self._reuse_helper(entry, lookup="source_kind = 'invoice' AND source_id = ?", parameters=(identifier,),
                                  run_id=run_id, invoice_id=identifier, vendor_id=vendor_id, idempotency_key="bill:" + identifier)

    def post_payment(self, payment, *, run_id, invoice_id, vendor_id=None,
                     created_by="person-ap-agent", approval_id=None):
        return self.post_payment_with_status(payment, run_id=run_id, invoice_id=invoice_id,
                    vendor_id=vendor_id, created_by=created_by, approval_id=approval_id)[0]

    def post_payment_with_status(self, payment, *, run_id, invoice_id, vendor_id=None,
                                 created_by="person-ap-agent", approval_id=None):
        payment = Payment.model_validate(payment)
        if payment.transfer_status.casefold() in _FAILED:
            raise LedgerError("A failed or rejected bank transfer cannot be posted as executed")
        if type(payment.amount_cents) is not int or payment.amount_cents <= 0 or not payment.transfer_id.strip():
            raise LedgerError("An executed payment requires a transfer identity and positive integer cents")
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            raise LedgerError("An executed invoice payment requires its invoice identity")
        source = JournalSource(kind="approval" if approval_id is not None else "payment",
                               id=approval_id if approval_id is not None else payment.transfer_id)
        entry = JournalEntry(entry_id=_identity("payment", payment.transfer_id), date=datetime.now(timezone.utc).date(),
            memo=f"Payment to {payment.payee_label} ({payment.to_display_number}) for invoice {invoice_id}; transfer {payment.transfer_id}",
            source=source, created_by=created_by,
            lines=[_line("2000", debit=payment.amount_cents), _line("1000", credit=payment.amount_cents)])
        return self._reuse_helper(entry, lookup="transfer_id = ?", parameters=(payment.transfer_id,),
                    run_id=run_id, invoice_id=invoice_id, vendor_id=vendor_id, transfer_id=payment.transfer_id,
                    bank_mode=payment.bank_mode,
                    idempotency_key="payment:" + payment.transfer_id)

    def reverse(self, entry_id, *, created_by, memo):
        original = self.get_entry(entry_id)
        if original is None:
            raise LedgerError("Journal entry to reverse was not found")
        entry = JournalEntry(entry_id=_identity("reversal", entry_id), date=datetime.now(timezone.utc).date(),
            memo=memo, source=JournalSource(kind="reversal", id=entry_id), created_by=created_by,
            lines=[_line(line.account_code, debit=line.credit_cents, credit=line.debit_cents) for line in original.lines])
        with self.store._lock:
            row = self.store._db().execute("SELECT run_id, invoice_id, vendor_id FROM ledger_journal WHERE entry_id = ?", (entry_id,)).fetchone()
        return self._reuse_helper(entry, lookup="source_kind = 'reversal' AND source_id = ?", parameters=(entry_id,),
                    run_id=row["run_id"], invoice_id=row["invoice_id"], vendor_id=row["vendor_id"],
                    idempotency_key="reversal:" + entry_id)[0]
