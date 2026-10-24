"""Small SQLite persistence layer shared by the API and service adapters."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from pydantic import BaseModel, TypeAdapter

from app.contract_models import AgentTranscript, EvalJob, Person, PlannerTranscript, Run, RunEvent


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "countersign.db"
_EVENT_ADAPTER = TypeAdapter(RunEvent)
_RECORD_TABLES = {
    "companies": "company_id",
    "vendors": "vendor_id",
    "vendor_history": "vendor_id",
    "vendor_bank_changes": "change_id",
    "reconciliation_sessions": "session_id",
    "purchase_orders": "purchase_order_id",
    "bank_directory": "display_number",
    "bank_seeds": "seed_id",
    "bank_payment_attempts": "attempt_id",
    "invoice_scoring": "invoice_id",
    "attack_audit": "batch_id",
    "inbox_items": "item_id",
    "documents": "document_id",
    "extractions": "extraction_id",
    "planner_transcripts": "run_id",
    "agent_transcripts": "transcript_id",
    "batches": "batch_id",
    "runs": "run_id",
    "decisions": "decision_id",
    "approvals": "approval_id",
    "approval_contexts": "approval_id",
    "approval_attempts": "approval_id",
    "session_payment_claims": "claim_id",
    "people": "person_id",
    "graphs": "graph_id",
    "transfers": "transfer_id",
    "redteam_jobs": "job_id",
    "eval_jobs": "job_id",
    "eval_receipts": "receipt_id",
    "eval_prior_paid": "prior_id",
    "dry_run_intents": "intent_id",
}
_RUN_SCOPED_TABLES = {"decisions", "approvals", "transfers", "approval_contexts", "graphs", "agent_transcripts"}


def _event_data(event: RunEvent | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event, BaseModel):
        return event.model_dump(mode="python", exclude_none=True)
    return dict(event)


class Store:
    """Own one thread-safe connection; transactions allocate event sequences.

    Entity records stay as JSON until their feature supplies a schema. Public API
    records with established schemas (runs and events) are validated on both write
    and read. SQL identifiers come exclusively from the table allowlist above.
    """

    def __init__(self, path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        self.path = Path(path) if str(path) != ":memory:" else ":memory:"
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    def initialize(self) -> None:
        """Create the database, required tables, and persistent session clock."""
        with self._lock:
            if self._connection is not None:
                return
            if isinstance(self.path, Path):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path, check_same_thread=False, isolation_level=None, timeout=30
            )
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                for table, key in _RECORD_TABLES.items():
                    run_column = ", run_id TEXT" if table in _RUN_SCOPED_TABLES else ""
                    connection.execute(
                        f"CREATE TABLE IF NOT EXISTS {table} "
                        f"({key} TEXT PRIMARY KEY{run_column}, data TEXT NOT NULL)"
                    )
                    if table in _RUN_SCOPED_TABLES:
                        connection.execute(
                            f"CREATE INDEX IF NOT EXISTS {table}_run_id ON {table} (run_id)"
                        )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS events ("
                    "run_id TEXT NOT NULL, seq INTEGER NOT NULL CHECK (seq > 0), "
                    "ts TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (run_id, seq))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS metadata ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS attack_batch_requests ("
                    "request_id TEXT PRIMARY KEY, requested_at REAL NOT NULL)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
                    ("session_started_at", datetime.now(timezone.utc).isoformat()),
                )
            except BaseException:
                connection.close()
                raise
            self._connection = connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Store.initialize() must be called before using storage")
        return self._connection

    @contextmanager
    def transaction(self):
        """Compose journal, history and event writes in one durable transaction."""
        with self._lock:
            connection = self._db()
            nested = connection.in_transaction
            connection.execute("SAVEPOINT store_transaction" if nested else "BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("RELEASE SAVEPOINT store_transaction") if nested else connection.commit()
            except BaseException:
                if nested:
                    connection.execute("ROLLBACK TO SAVEPOINT store_transaction")
                    connection.execute("RELEASE SAVEPOINT store_transaction")
                else:
                    connection.rollback()
                raise

    @property
    def session_started_at(self) -> datetime:
        with self._lock:
            row = self._db().execute(
                "SELECT value FROM metadata WHERE key = ?", ("session_started_at",)
            ).fetchone()
            return datetime.fromisoformat(row["value"])

    def save_record(
        self, table: str, record_id: str, record: Mapping[str, Any] | BaseModel
    ) -> None:
        """Upsert a JSON entity without imposing future business schemas."""
        if table not in _RECORD_TABLES:
            raise ValueError(f"Unsupported record table: {table}")
        data = record.model_dump(mode="json", exclude_none=True) if isinstance(record, BaseModel) else dict(record)
        if table == "runs":
            run = Run.model_validate(data)
            if run.run_id != record_id:
                raise ValueError("record_id must match run.run_id")
            data = run.model_dump(mode="json", exclude_none=True)
        elif table == "planner_transcripts":
            transcript = PlannerTranscript.model_validate(data)
            if transcript.run_id != record_id:
                raise ValueError("record_id must match transcript.run_id")
            data = transcript.model_dump(mode="json")
        elif table == "people":
            # A null approval limit is a required, meaningful unlimited value.
            data = Person.model_validate(record).model_dump(mode="json")
        elif table == "eval_jobs":
            # Clean answers have a required, explicitly null reason code.
            job = EvalJob.model_validate(record)
            if job.job_id != record_id:
                raise ValueError("record_id must match job.job_id")
            data = job.model_dump(mode="json")
        elif table == "agent_transcripts":
            transcript = AgentTranscript.model_validate({
                key: value for key, value in data.items() if key != "transcript_id"
            })
            if record_id != f"{transcript.run_id}:{transcript.invoice_id}":
                raise ValueError("Transcript identity does not match its storage key")
            data = {"transcript_id": record_id, **transcript.model_dump(mode="json")}
        encoded = json.dumps(data, separators=(",", ":"), allow_nan=False)
        key = _RECORD_TABLES[table]
        with self._lock:
            if table in _RUN_SCOPED_TABLES:
                self._db().execute(
                    f"INSERT INTO {table} ({key}, run_id, data) VALUES (?, ?, ?) "
                    f"ON CONFLICT({key}) DO UPDATE SET run_id=excluded.run_id, data=excluded.data",
                    (record_id, data.get("run_id"), encoded),
                )
            else:
                self._db().execute(
                    f"INSERT INTO {table} ({key}, data) VALUES (?, ?) "
                    f"ON CONFLICT({key}) DO UPDATE SET data=excluded.data",
                    (record_id, encoded),
                )

    def get_record(self, table: str, record_id: str) -> dict[str, Any] | None:
        if table not in _RECORD_TABLES:
            raise ValueError(f"Unsupported record table: {table}")
        with self._lock:
            row = self._db().execute(
                f"SELECT data FROM {table} WHERE {_RECORD_TABLES[table]} = ?", (record_id,)
            ).fetchone()
            return json.loads(row["data"]) if row else None

    def save_records_atomically(self, records) -> None:
        """Publish one complete generated batch; a failed write leaves none."""
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table, record_id, record in records:
                    self.save_record(table, record_id, record)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def reserve_attack_batch(self, request_id: str, requested_at: float) -> int:
        """Shared rolling-window limit; return Retry-After seconds or zero.

        SQLite serializes separate worker connections as well as local threads.
        Reservations survive app restarts and demo resets; failed requests can
        release only their own server-assigned reservation.
        """
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("DELETE FROM attack_batch_requests WHERE requested_at <= ?", (requested_at - 60,))
                row = connection.execute("SELECT COUNT(*), MIN(requested_at) FROM attack_batch_requests").fetchone()
                retry_after = max(1, math.ceil(row[1] + 60 - requested_at)) if row[0] >= 10 else 0
                if not retry_after:
                    connection.execute("INSERT INTO attack_batch_requests (request_id, requested_at) VALUES (?, ?)",
                                       (request_id, requested_at))
                connection.commit()
                return retry_after
            except BaseException:
                connection.rollback()
                raise

    def release_attack_batch(self, request_id: str) -> None:
        with self._lock:
            self._db().execute("DELETE FROM attack_batch_requests WHERE request_id = ?", (request_id,))

    def claim_bank_payment_attempt(self, attempt: Mapping[str, Any]) -> bool:
        """Claim a logical payment atomically before any irreversible bank POST.

        SQLite's unique key protects against separate adapters and processes;
        an in-memory asyncio lock alone cannot prevent duplicate withdrawals.
        """
        encoded = json.dumps(dict(attempt), separators=(",", ":"), allow_nan=False)
        with self._lock:
            cursor = self._db().execute(
                "INSERT OR IGNORE INTO bank_payment_attempts (attempt_id, data) VALUES (?, ?)",
                (attempt["attempt_id"], encoded),
            )
            return cursor.rowcount == 1

    def _session_payment_key(self, invoice_number: str) -> str:
        if not isinstance(invoice_number, str) or not invoice_number.strip():
            raise ValueError("A nonempty invoice number is required")
        identity = json.dumps([self.session_started_at.isoformat(), invoice_number.strip().casefold()])
        return "invoice-payment-" + hashlib.sha256(identity.encode()).hexdigest()

    @staticmethod
    def _payment_metadata(*, vendor_id=None, amount_cents=None, invoice_date=None,
                          po_number=None) -> dict[str, Any]:
        """Normalize only real identity facts; missing values never match."""
        result = {}
        if isinstance(vendor_id, str) and vendor_id.strip():
            result["vendor_id"] = vendor_id.strip()
        if type(amount_cents) is int and amount_cents > 0:
            result["amount_cents"] = amount_cents
        if isinstance(invoice_date, date) and not isinstance(invoice_date, datetime):
            result["invoice_date"] = invoice_date.isoformat()
        elif isinstance(invoice_date, str):
            try:
                result["invoice_date"] = date.fromisoformat(invoice_date).isoformat()
            except ValueError:
                pass
        if isinstance(po_number, str) and po_number.strip():
            result["po_number"] = " ".join(po_number.split()).casefold()
        return result

    @classmethod
    def _same_payment(cls, candidate: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
        """A new number cannot evade vendor/amount plus date-or-PO matching."""
        prior = cls._payment_metadata(**{name: candidate.get(name) for name in (
            "vendor_id", "amount_cents", "invoice_date", "po_number")})
        if (not identity.get("vendor_id") or not identity.get("amount_cents")
                or prior.get("vendor_id") != identity["vendor_id"]
                or prior.get("amount_cents") != identity["amount_cents"]):
            return False
        return any(identity.get(name) is not None and identity[name] == prior.get(name)
                   for name in ("invoice_date", "po_number"))

    @staticmethod
    def _payment_unresolved_or_paid(record: Mapping[str, Any]) -> bool:
        # Unknown or uncertain status remains reserved until reconciliation.
        failed = {"failed", "rejected", "cancelled", "canceled", "error"}
        return not any(isinstance(record.get(name), str) and record[name].casefold() in failed
                       for name in ("state", "status", "transfer_status"))

    def _historical_payment_identity(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Enrich legacy payment bindings from deterministically verified sources."""
        identity = self._payment_metadata(**{name: record.get(name) for name in (
            "vendor_id", "amount_cents", "invoice_date", "po_number")})
        run_id, invoice_id = record.get("run_id"), record.get("invoice_id")
        if not isinstance(run_id, str) or not isinstance(invoice_id, str):
            return identity
        if not identity.get("vendor_id"):
            run = self.get_record("runs", run_id) or {}
            batch = self.get_record("batches", run["batch_id"]) if run.get("batch_id") else None
            invoices = [item for item in (batch or {}).get("invoices", [])
                        if item.get("invoice_id") == invoice_id]
            if len(invoices) == 1:
                identity.update(self._payment_metadata(vendor_id=invoices[0].get("vendor_id")))
        if not identity.get("invoice_date") or not identity.get("po_number"):
            # Lazy import keeps the persistence layer independent of adapters
            # and avoids treating an arbitrary document date as a paid fact.
            from app.security.duplicate_identity import verified_invoice_references
            references = verified_invoice_references(self, run_id, invoice_id)
            for name in ("invoice_date", "po_number"):
                if name not in identity and name in references:
                    identity.update(self._payment_metadata(**{name: references[name]}))
        return identity

    def has_session_payment(self, invoice_number: str, *, vendor_id=None,
                            amount_cents=None, invoice_date=None, po_number=None) -> bool:
        """Keep exact session protection and include semantic paid/pending history.

        Semantic identity survives session resets. Unguarded-lane transfers are
        a separate experiment and never contaminate Countersign's paid history.
        Old records without a date or PO still retain exact-number protection.
        """
        if self._has_exact_session_payment(invoice_number):
            return True
        identity = self._payment_metadata(vendor_id=vendor_id, amount_cents=amount_cents,
                                          invoice_date=invoice_date, po_number=po_number)
        if not identity.get("vendor_id") or not identity.get("amount_cents"):
            return False
        for record in self.list_records("session_payment_claims"):
            if (self._payment_unresolved_or_paid(record)
                    and self._same_payment(self._historical_payment_identity(record), identity)):
                return True
        for transfer in self.list_records("transfers"):
            if transfer.get("lane") == "unguarded" or transfer.get("via") == "agent":
                continue
            if (self._payment_unresolved_or_paid(transfer)
                    and self._same_payment(self._historical_payment_identity(transfer), identity)):
                return True
        return False

    def _has_exact_session_payment(self, invoice_number: str) -> bool:
        """Include reservations and invoice-bound transfers from before B6.

        Legacy runs did not create payment claims or always persist the invoice
        number on the transfer. Their durable run/batch identity supplies that
        number. Pending or otherwise unresolved submissions remain duplicates
        until reconciliation; a missing new-style claim cannot authorize a
        second payment.
        """
        if self.get_record("session_payment_claims", self._session_payment_key(invoice_number)) is not None:
            return True
        number = invoice_number.strip().casefold()
        session = self.session_started_at.astimezone(timezone.utc)

        def timestamp(value):
            if not isinstance(value, str):
                return None
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None

        for transfer in self.list_records("transfers"):
            # Side-by-side lanes are independent experiments over the same
            # invoice. Unguarded payments must not influence policy decisions.
            if transfer.get("lane") == "unguarded" or transfer.get("via") == "agent":
                continue
            if "session_started_at" in transfer:
                if timestamp(transfer["session_started_at"]) != session:
                    continue
            else:
                created = timestamp(transfer.get("ts"))
                if created is None or created < session:
                    continue
            bound_number = transfer.get("invoice_number")
            if not isinstance(bound_number, str) or not bound_number.strip():
                run_id, invoice_id = transfer.get("run_id"), transfer.get("invoice_id")
                if not run_id or not invoice_id:
                    continue
                run = self.get_record("runs", run_id) or {}
                batch = self.get_record("batches", run["batch_id"]) if run.get("batch_id") else None
                matches = [invoice for invoice in (batch or {}).get("invoices", [])
                           if invoice.get("invoice_id") == invoice_id]
                if len(matches) != 1:
                    continue
                bound_number = matches[0].get("invoice_number")
            if isinstance(bound_number, str) and bound_number.strip().casefold() == number:
                return True
        return False

    def claim_session_payment(self, invoice_number: str, attempt_id: str, *, vendor_id=None,
                              amount_cents=None, invoice_date=None, po_number=None,
                              run_id=None, invoice_id=None) -> bool:
        """Atomically reserve exact and semantic identities before a bank call."""
        key = self._session_payment_key(invoice_number)
        metadata = self._payment_metadata(vendor_id=vendor_id, amount_cents=amount_cents,
                                           invoice_date=invoice_date, po_number=po_number)
        record = {"claim_id": key, "invoice_number": invoice_number,
                  "session_started_at": self.session_started_at.isoformat(),
                  "attempt_id": attempt_id, "state": "reserved", **metadata}
        if isinstance(run_id, str) and run_id:
            record["run_id"] = run_id
        if isinstance(invoice_id, str) and invoice_id:
            record["invoice_id"] = invoice_id
        # Different invoice numbers have different primary keys. A write
        # transaction serializes the semantic read and insert across processes.
        with self.transaction():
            if self.has_session_payment(invoice_number, **metadata):
                return False
            cursor = self._db().execute(
                "INSERT OR IGNORE INTO session_payment_claims (claim_id, data) VALUES (?, ?)",
                (key, json.dumps(record, separators=(",", ":"))),
            )
            return cursor.rowcount == 1

    def finish_session_payment(self, invoice_number: str, *, state: str, transfer_id: str | None = None) -> None:
        key = self._session_payment_key(invoice_number)
        with self._lock:
            record = self.get_record("session_payment_claims", key)
            if record is None:
                raise ValueError("Payment reservation is missing")
            record.update(state=state)
            if transfer_id is not None:
                record["transfer_id"] = transfer_id
            self.save_record("session_payment_claims", key, record)

    def claim_approval_resolution(self, approval_id: str, decision: str, actor_id: str) -> bool:
        """A durable claim survives a timeout/crash, preventing double transfers."""
        record = {"approval_id": approval_id, "decision": decision, "actor_id": actor_id,
                  "state": "resolving", "started_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            cursor = self._db().execute(
                "INSERT OR IGNORE INTO approval_attempts (approval_id, data) VALUES (?, ?)",
                (approval_id, json.dumps(record, separators=(",", ":"))),
            )
            return cursor.rowcount == 1

    def list_records(self, table: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
        if table not in _RECORD_TABLES:
            raise ValueError(f"Unsupported record table: {table}")
        if run_id is not None and table not in _RUN_SCOPED_TABLES:
            raise ValueError(f"Table {table} does not support a run_id filter")
        with self._lock:
            if run_id is None:
                rows = self._db().execute(
                    f"SELECT data FROM {table} ORDER BY {_RECORD_TABLES[table]}"
                ).fetchall()
            else:
                rows = self._db().execute(
                    f"SELECT data FROM {table} WHERE run_id = ? ORDER BY {_RECORD_TABLES[table]}",
                    (run_id,),
                ).fetchall()
            return [json.loads(row["data"]) for row in rows]

    def save_run(self, run: Run | Mapping[str, Any]) -> Run:
        validated = Run.model_validate(run)
        self.save_record("runs", validated.run_id, validated)
        return validated

    def get_run(self, run_id: str) -> Run | None:
        data = self.get_record("runs", run_id)
        return Run.model_validate(data) if data is not None else None

    def append_event(self, run_id: str, event: RunEvent | Mapping[str, Any]) -> RunEvent:
        """Validate and commit the next per-run sequence in one transaction.

        Callers supply type, lane, payload, and optional mode. The store owns the
        envelope identity and timestamp. Validation errors roll back without
        consuming a sequence number, including across separate Store instances.
        """
        data = _event_data(event)
        with self.transaction() as connection:
            seq = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            data.update(run_id=run_id, seq=seq, ts=datetime.now(timezone.utc))
            validated = _EVENT_ADAPTER.validate_python(data)
            self._insert_event(connection, validated)
            return validated

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: RunEvent) -> None:
        connection.execute(
            "INSERT INTO events (run_id, seq, ts, data) VALUES (?, ?, ?, ?)",
            (event.run_id, event.seq, event.ts.isoformat(), event.model_dump_json(exclude_none=True)),
        )

    def list_events(self, run_id: str, last_seq: int = 0) -> list[RunEvent]:
        with self._lock:
            rows = self._db().execute(
                "SELECT data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, last_seq)
            ).fetchall()
            return [_EVENT_ADAPTER.validate_json(row["data"]) for row in rows]

    def persist_audit_event(self, event: RunEvent | Mapping[str, Any]) -> RunEvent:
        """Persist an offline audit event, idempotently preserving its envelope.

        The event must be labelled offline before publishing to the bus if it
        passes through the offline audit sink. A conflicting sequence is rejected
        rather than changing an event that subscribers may already have received.
        """
        validated = _EVENT_ADAPTER.validate_python(_event_data(event))
        if validated.mode != "offline":
            raise ValueError("Offline audit events must carry mode='offline'")
        if validated.seq < 1:
            raise ValueError("Persisted event sequences must be positive")
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT data FROM events WHERE run_id = ? AND seq = ?",
                    (validated.run_id, validated.seq),
                ).fetchone()
                if row is not None:
                    existing = _EVENT_ADAPTER.validate_json(row["data"])
                    if existing != validated:
                        raise ValueError("Conflicting event already exists for run_id and seq")
                    connection.commit()
                    return existing
                next_seq = connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (validated.run_id,)
                ).fetchone()[0]
                if validated.seq != next_seq:
                    raise ValueError("Audit events must use the next per-run sequence number")
                self._insert_event(connection, validated)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            return validated

    def reset_demo(self) -> datetime:
        """Clear run execution state and advance the session clock atomically.

        Vendor, purchase order, document, batch, transfer, and red-team records
        are retained. No external bank adapter is called by the store.
        """
        started_at = datetime.now(timezone.utc)
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            try:
                for table in ("events", "decisions", "approvals", "approval_contexts", "approval_attempts",
                              "session_payment_claims", "graphs", "planner_transcripts", "agent_transcripts", "runs"):
                    connection.execute(f"DELETE FROM {table}")
                connection.execute(
                    "UPDATE metadata SET value = ? WHERE key = ?",
                    (started_at.isoformat(), "session_started_at"),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return started_at
