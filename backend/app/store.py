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

from app.contract_models import AgentTranscript, Autopilot, EvalJob, InboxItem, Person, PlannerTranscript, RedTeamJob, Run, RunEvent


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
    "autopilot_state": "state_id",
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
    "redteam_job_details": "job_id",
    "redteam_variants": "variant_id",
    "redteam_planner_cache": "cache_key",
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
                    "CREATE TABLE IF NOT EXISTS audit_outbox ("
                    "event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                    "document TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, mapping_error TEXT)"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS audit_pending ON audit_outbox (delivered, run_id, seq)")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS audit_event_sequences (run_id TEXT PRIMARY KEY, last_seq INTEGER NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS audit_failure_notifications (run_id TEXT PRIMARY KEY)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS attack_batch_requests ("
                    "request_id TEXT PRIMARY KEY, requested_at REAL NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS inbox_document_keys ("
                    "document_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE)"
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
        elif table == "redteam_jobs":
            job = RedTeamJob.model_validate(record)
            if job.job_id != record_id:
                raise ValueError("record_id must match job.job_id")
            data = job.model_dump(mode="json", exclude_none=True)
        elif table == "autopilot_state":
            state = Autopilot.model_validate({
                key: value for key, value in dict(record).items() if key != "state_id"
            })
            if record_id != "autopilot":
                raise ValueError("Autopilot state must use its singleton storage key")
            data = {"state_id": record_id, **state.model_dump(mode="json")}
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
        with self.transaction():
            for table, record_id, record in records:
                self.save_record(table, record_id, record)

    def get_autopilot_state(self) -> Autopilot:
        record = self.get_record("autopilot_state", "autopilot")
        if record is None:
            return Autopilot(enabled=False, lanes=["countersign", "unguarded"],
                             run_id=None, last_checked_at=None)
        return Autopilot.model_validate({key: value for key, value in record.items() if key != "state_id"})

    def save_autopilot_state(self, state: Autopilot) -> None:
        self.save_record("autopilot_state", "autopilot", {
            "state_id": "autopilot", **Autopilot.model_validate(state).model_dump(mode="json"),
        })

    def _inbox_identity(self, document_id: str) -> InboxItem | None:
        """Find the durable document key, including inbox rows made before it."""
        key = self._db().execute(
            "SELECT item_id FROM inbox_document_keys WHERE document_id = ?", (document_id,),
        ).fetchone()
        if key is not None:
            record = self.get_record("inbox_items", key["item_id"])
            if record is None or record.get("document_id") != document_id:
                raise ValueError("Inbox document idempotency record requires review")
            return InboxItem.model_validate(record)
        matches = [InboxItem.model_validate(row) for row in self.list_records("inbox_items")
                   if row.get("document_id") == document_id]
        if not matches:
            return None
        # A historical processed or pending attempt must win over a duplicate
        # queued row; never make a prior attempt eligible for another payment.
        rank = {"processed": 0, "processing": 1, "queued": 2}
        item = min(matches, key=lambda candidate: (rank[candidate.status], candidate.received_at, candidate.item_id))
        self._db().execute("INSERT INTO inbox_document_keys (document_id, item_id) VALUES (?, ?)",
                           (document_id, item.item_id))
        return item

    def enqueue_inbox(self, document_id: str, sender: str, received_at=None, *, source="inbox") -> InboxItem:
        """Accept one durable inbox item per document, even across workers."""
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("A nonempty inbox document ID is required")
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError("A nonempty inbox sender is required")
        if source not in {"inbox", "attack_lab"}:
            raise ValueError("Inbox source must be inbox or attack_lab")
        with self.transaction():
            existing = self._inbox_identity(document_id)
            if existing is not None and existing.status != "queued":
                return existing
            # A manual run may have accepted this document before it arrived
            # in the inbox. Bind that durable attempt rather than scheduling
            # the same invoice again, including after an application restart.
            attempts = []
            for raw in self.list_records("runs"):
                try:
                    run = Run.model_validate(raw)
                except (ValueError, TypeError):
                    continue
                if not run.lanes:
                    continue
                batch = self.get_record("batches", run.batch_id)
                if batch is None or not any(invoice.get("document_id") == document_id
                                             for invoice in batch.get("invoices", [])
                                             if isinstance(invoice, Mapping)):
                    continue
                processing = any(lane.status == "running" for lane in run.lanes)
                attempts.append((processing, run.created_at, run.run_id))
            prior = min(attempts) if attempts else None
            if existing is not None:
                if prior is not None:
                    existing = existing.model_copy(update={
                        "status": "processing" if prior[0] else "processed", "run_id": prior[2],
                    })
                    self.save_record("inbox_items", existing.item_id, existing)
                return existing
            item = InboxItem(item_id="inbox-" + hashlib.sha256(document_id.encode()).hexdigest(),
                             document_id=document_id, sender=sender,
                             received_at=received_at or datetime.now(timezone.utc),
                             source=source,
                             status=("processing" if prior[0] else "processed") if prior else "queued",
                             **({"run_id": prior[2]} if prior else {}))
            self.save_record("inbox_items", item.item_id, item)
            self._db().execute("INSERT INTO inbox_document_keys (document_id, item_id) VALUES (?, ?)",
                               (document_id, item.item_id))
            return item

    def next_attack_invoice_number(self, base_number: str) -> str:
        """Allocate a per-base suffix in the caller's batch transaction.

        The metadata counter survives reset/restart and serializes independent
        SQLite connections. Failed document/batch creation rolls it back when
        the caller owns an outer transaction.
        """
        if not isinstance(base_number, str) or not base_number.strip() or len(base_number) > 100:
            raise ValueError("A server-assigned base invoice number is required")
        key = "attack_invoice_sequence:" + base_number
        with self.transaction() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            previous = int(row["value"]) if row is not None else 0
            if previous < 0:
                raise ValueError("Attack invoice sequence requires review")
            sequence = previous + 1
            connection.execute("INSERT INTO metadata(key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(sequence)))
            return f"{base_number}-A{sequence}"

    def claim_inbox(self, run_id: str, document_ids=None) -> list[InboxItem]:
        """Permanently admit queued documents once before any external work."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Inbox claims require their actual run ID")
        if isinstance(document_ids, str):
            raise ValueError("Inbox document selection must be a collection of document IDs")
        selected = set(document_ids) if document_ids is not None else None
        if selected is not None and any(not isinstance(value, str) or not value.strip() for value in selected):
            raise ValueError("Inbox document selection requires nonempty document IDs")
        with self.transaction():
            queued = sorted((InboxItem.model_validate(row) for row in self.list_records("inbox_items")
                             if row.get("status") == "queued"
                             and (selected is None or row.get("document_id") in selected)),
                            key=lambda item: (item.received_at, item.item_id))
            result = []
            for item in queued:
                canonical = self._inbox_identity(item.document_id)
                if canonical.item_id != item.item_id or canonical.status != "queued":
                    continue
                claimed = item.model_copy(update={"status": "processing", "run_id": run_id})
                self.save_record("inbox_items", claimed.item_id, claimed)
                result.append(claimed)
            return result

    def finish_inbox(self, item_id: str) -> InboxItem | None:
        """A terminal processing attempt is never automatically made queued."""
        with self.transaction():
            raw = self.get_record("inbox_items", item_id)
            if raw is None:
                return None
            item = InboxItem.model_validate(raw)
            if item.status == "processing":
                item = item.model_copy(update={"status": "processed"})
                self.save_record("inbox_items", item.item_id, item)
            return item

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
            seq = self._next_event_sequence(connection, run_id)
            data.update(run_id=run_id, seq=seq, ts=datetime.now(timezone.utc))
            validated = _EVENT_ADAPTER.validate_python(data)
            self._insert_event(connection, validated)
            return validated

    @staticmethod
    def _next_event_sequence(connection, run_id):
        visible = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (run_id,)).fetchone()[0]
        previous = connection.execute("SELECT last_seq FROM audit_event_sequences WHERE run_id = ?", (run_id,)).fetchone()
        return max(visible, previous[0] if previous else 0) + 1

    def _insert_event(self, connection: sqlite3.Connection, event: RunEvent) -> None:
        connection.execute(
            "INSERT INTO events (run_id, seq, ts, data) VALUES (?, ?, ?, ?)",
            (event.run_id, event.seq, event.ts.isoformat(), event.model_dump_json(exclude_none=True)),
        )
        self._queue_audit_event(connection, event)

    def _queue_audit_event(self, connection, event):
        """Snapshot source evidence in the event transaction; never call a service."""
        mapping_error = None
        try:
            from app.audit.events import map_event
            document = map_event(self, event)
            encoded = json.dumps(document, separators=(",", ":"), allow_nan=False)
        except Exception as error:
            # Audit projection failures cannot undo a financial event. The
            # durable marker prevents a partial projection being called synced.
            document = {"run_id": event.run_id, "seq": event.seq, "ts": event.ts.isoformat(),
                        "lane": event.lane, "type": event.type, "mode": event.mode,
                        "payload": json.loads(event.model_dump_json())["payload"]}
            mapping_error = type(error).__name__
            encoded = json.dumps(document, separators=(",", ":"), allow_nan=False)
        connection.execute(
            "INSERT OR IGNORE INTO audit_outbox (event_id, run_id, seq, document, mapping_error) VALUES (?, ?, ?, ?, ?)",
            (f"{event.run_id}:{event.seq}", event.run_id, event.seq,
             encoded, mapping_error),
        )
        connection.execute(
            "INSERT INTO audit_event_sequences (run_id, last_seq) VALUES (?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET last_seq=MAX(last_seq, excluded.last_seq)",
            (event.run_id, event.seq),
        )

    def backfill_audit_outbox(self):
        """Queue recoverable pre-feature events once; delivered IDs remain tombstones."""
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT events.data FROM events LEFT JOIN audit_outbox "
                "ON audit_outbox.run_id=events.run_id AND audit_outbox.seq=events.seq "
                "WHERE audit_outbox.event_id IS NULL ORDER BY events.run_id, events.seq"
            ).fetchall()
            for row in rows:
                self._queue_audit_event(connection, _EVENT_ADAPTER.validate_json(row["data"]))
        return len(rows)

    @staticmethod
    def _validated_audit_snapshot(snapshot):
        """Validate a finalized run's immutable, already-projected audit facts."""
        from app.audit.events import EVENT_MAPPINGS

        names = {"version", "run_id", "last_seq", "run", "rows", "snapshot_sha256"}
        if (not isinstance(snapshot, dict) or set(snapshot) != names
                or type(snapshot["version"]) is not int or snapshot["version"] != 1):
            raise ValueError("Unsupported audit snapshot")
        run = Run.model_validate(snapshot["run"])
        if (run.run_id != snapshot["run_id"] or not run.lanes
                or any(lane.status == "running" for lane in run.lanes)):
            raise ValueError("Audit export requires a finalized source run")
        rows = snapshot["rows"]
        if (not isinstance(rows, list) or not rows or type(snapshot["last_seq"]) is not int
                or snapshot["last_seq"] != len(rows)):
            raise ValueError("Audit snapshot must contain the complete source sequence")
        properties = EVENT_MAPPINGS["properties"]
        for seq, row in enumerate(rows, 1):
            if (not isinstance(row, dict) or set(row) != {"event_id", "run_id", "seq", "document"}
                    or row["run_id"] != run.run_id or type(row["seq"]) is not int or row["seq"] != seq
                    or row["event_id"] != f"{run.run_id}:{seq}"):
                raise ValueError("Audit snapshot event identity is inconsistent")
            document = row["document"]
            if not isinstance(document, dict) or set(document) != set(properties):
                raise ValueError("Audit snapshot projection does not match the explicit mapping")
            event = _EVENT_ADAPTER.validate_python(document["payload"])
            envelope = event.model_dump(mode="json", exclude_none=True)
            if (event.run_id != run.run_id or event.seq != seq or envelope != document["payload"]
                    or any(document[name] != envelope[name] for name in ("run_id", "seq", "ts", "lane", "type"))
                    or document["mode"] != (envelope.get("mode") or "unknown")):
                raise ValueError("Audit snapshot envelope and projection disagree")
            for name, specification in properties.items():
                value, kind = document[name], specification["type"]
                if value is None or name == "payload":
                    continue
                if name == "reason_codes":
                    valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
                elif kind in {"keyword", "date"}:
                    valid = isinstance(value, str)
                elif kind == "long":
                    valid = type(value) is int
                elif kind == "boolean":
                    valid = type(value) is bool
                else:
                    valid = False
                if not valid:
                    raise ValueError("Audit snapshot projection has an invalid field type")
        if rows[-1]["document"]["type"] != "run.completed":
            raise ValueError("Audit export requires the source run's final completion event")
        identity = {key: snapshot[key] for key in ("version", "run_id", "last_seq", "run", "rows")}
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if snapshot["snapshot_sha256"] != digest:
            raise ValueError("Audit snapshot checksum does not match its immutable contents")
        return run, digest

    def export_audit_snapshot(self, run_id):
        """Read a complete finalized case without recomputing its projections.

        Call only after the owning RunManager has closed. Keeping source rows
        untouched permits replay after a crash between the two SQLite stores.
        """
        with self.transaction() as connection:
            run = self.get_run(run_id)
            if run is None or not run.lanes or any(lane.status == "running" for lane in run.lanes):
                raise ValueError("Audit export requires a finalized source run")
            events = self.list_events(run_id)
            records = connection.execute(
                "SELECT event_id, run_id, seq, document, mapping_error FROM audit_outbox "
                "WHERE run_id=? ORDER BY seq", (run_id,),
            ).fetchall()
            if len(records) != len(events) or not records or any(row["mapping_error"] for row in records):
                raise ValueError("Source audit snapshots are incomplete or require projection repair")
            rows = [{"event_id": row["event_id"], "run_id": row["run_id"], "seq": row["seq"],
                     "document": json.loads(row["document"])} for row in records]
            for event, row in zip(events, rows):
                if event.model_dump(mode="json", exclude_none=True) != row["document"].get("payload"):
                    raise ValueError("Source audit snapshot differs from its original event")
            snapshot = {"version": 1, "run_id": run_id, "last_seq": rows[-1]["seq"],
                        "run": run.model_dump(mode="json"), "rows": rows}
            encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
            snapshot["snapshot_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
            self._validated_audit_snapshot(snapshot)
            return snapshot

    def import_audit_snapshot(self, snapshot):
        """Atomically stage unchanged external projections and reserve their sequence.

        No source entity or event is copied into local runtime tables. Existing
        delivery tombstones stay acknowledged. A finalized import fingerprint
        prevents a different history from later claiming the same run identity.
        """
        run, digest = self._validated_audit_snapshot(snapshot)
        rows = snapshot["rows"]
        manifest_key = "audit_import:" + run.run_id
        manifest = {"snapshot_sha256": digest, "last_seq": snapshot["last_seq"], "event_count": len(rows)}
        added = 0
        with self.transaction() as connection:
            local = self.get_run(run.run_id)
            if local is not None and any(lane.status == "running" for lane in local.lanes):
                raise ValueError("Cannot import audit history into an active local run")
            existing_manifest = connection.execute("SELECT value FROM metadata WHERE key=?", (manifest_key,)).fetchone()
            if existing_manifest is not None:
                if json.loads(existing_manifest["value"]) != manifest:
                    raise ValueError("Audit import conflicts with the run's immutable source snapshot")
            elif self.audit_run_known(run.run_id):
                raise ValueError("Audit import run identity already belongs to local history")
            for row in rows:
                encoded = json.dumps(row["document"], sort_keys=True, separators=(",", ":"), allow_nan=False)
                existing = connection.execute(
                    "SELECT run_id, seq, document, mapping_error FROM audit_outbox WHERE event_id=?",
                    (row["event_id"],),
                ).fetchone()
                if existing is not None:
                    if (existing["run_id"] != run.run_id or existing["seq"] != row["seq"]
                            or existing["mapping_error"] or json.loads(existing["document"]) != row["document"]):
                        raise ValueError("Audit event identity collision; existing history was not overwritten")
                    continue
                connection.execute(
                    "INSERT INTO audit_outbox (event_id, run_id, seq, document, mapping_error) VALUES (?, ?, ?, ?, NULL)",
                    (row["event_id"], run.run_id, row["seq"], encoded),
                )
                added += 1
            connection.execute(
                "INSERT INTO audit_event_sequences (run_id, last_seq) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET last_seq=MAX(last_seq, excluded.last_seq)",
                (run.run_id, snapshot["last_seq"]),
            )
            connection.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES (?, ?)",
                               (manifest_key, json.dumps(manifest, sort_keys=True, separators=(",", ":"))))
        return {"run_id": run.run_id, **manifest, "queued": added, "already_present": len(rows) - added,
                "destination_pending": self.audit_pending_count(run.run_id), "status": "staged",
                "elastic_delivery_verified": False}


    def pending_audit(self, limit=100, run_id=None):
        if type(limit) is not int or limit < 1:
            raise ValueError("Audit batch size must be positive")
        with self.transaction() as connection:
            where, parameters = "delivered=0", []
            if run_id is not None:
                where += " AND run_id=?"
                parameters.append(run_id)
            rows = connection.execute(
                f"SELECT * FROM audit_outbox WHERE {where} ORDER BY rowid LIMIT ?", (*parameters, limit)
            ).fetchall()
            result = []
            for row in rows:
                record = {"event_id": row["event_id"], "run_id": row["run_id"], "seq": row["seq"],
                          "document": json.loads(row["document"])}
                if row["mapping_error"]:
                    try:
                        from app.audit.events import map_event
                        saved = connection.execute("SELECT data FROM events WHERE run_id=? AND seq=?",
                                                   (row["run_id"], row["seq"])).fetchone()
                        if saved is None:
                            raise ValueError("Original event requires review")
                        document = map_event(self, _EVENT_ADAPTER.validate_json(saved["data"]))
                    except Exception as error:
                        record["mapping_error"] = type(error).__name__
                    else:
                        record["document"] = document
                        connection.execute("UPDATE audit_outbox SET document=?, mapping_error=NULL WHERE event_id=?",
                            (json.dumps(document, separators=(",", ":"), allow_nan=False), row["event_id"]))
                result.append(record)
            return result

    def acknowledge_audit(self, event_ids):
        with self.transaction() as connection:
            connection.executemany("UPDATE audit_outbox SET delivered=1 WHERE event_id=?", ((key,) for key in event_ids))

    def audit_pending_count(self, run_id=None):
        with self._lock:
            if run_id is None:
                return self._db().execute("SELECT COUNT(*) FROM audit_outbox WHERE delivered=0").fetchone()[0]
            return self._db().execute("SELECT COUNT(*) FROM audit_outbox WHERE delivered=0 AND run_id=?", (run_id,)).fetchone()[0]

    def audit_run_known(self, run_id):
        """Whether this database can safely allocate an event for this run.

        Remote-only audit context must never create a local sequence starting
        at one. Sequence tombstones and outbox records intentionally survive a
        demo reset, so failures for that history can still append safely.
        """
        with self._lock:
            return bool(self._db().execute(
                "SELECT EXISTS(SELECT 1 FROM runs WHERE run_id=? "
                "UNION ALL SELECT 1 FROM events WHERE run_id=? "
                "UNION ALL SELECT 1 FROM audit_event_sequences WHERE run_id=? "
                "UNION ALL SELECT 1 FROM audit_outbox WHERE run_id=?)",
                (run_id, run_id, run_id, run_id),
            ).fetchone()[0])

    def claim_audit_error(self, run_id):
        """Claim inside the transaction that also appends the failure event.

        EventBus.report_audit_failure supplies that outer transaction. Keeping
        these writes atomic prevents a cancelled notifier from consuming its
        durable once-per-run marker without emitting an event.
        """
        with self.transaction() as connection:
            return connection.execute("INSERT OR IGNORE INTO audit_failure_notifications(run_id) VALUES (?)", (run_id,)).rowcount == 1

    def list_events(self, run_id: str, last_seq: int = 0) -> list[RunEvent]:
        with self._lock:
            rows = self._db().execute(
                "SELECT data FROM events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, last_seq)
            ).fetchall()
            return [_EVENT_ADAPTER.validate_json(row["data"]) for row in rows]

    def persist_audit_event(self, event: RunEvent | Mapping[str, Any], *, require_offline=True) -> RunEvent:
        """Persist an already-sequenced event, preserving its source envelope.

        Offline callers require their explicit offline label by default. The
        live audit sink accepts either source mode without relabelling it. A
        conflicting sequence cannot change previously published history.
        """
        validated = _EVENT_ADAPTER.validate_python(_event_data(event))
        if require_offline and validated.mode != "offline":
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
                    queued = connection.execute("SELECT 1 FROM audit_outbox WHERE event_id=?",
                                                (f"{existing.run_id}:{existing.seq}",)).fetchone()
                    if queued is None:
                        self._queue_audit_event(connection, existing)
                    connection.commit()
                    return existing
                next_seq = self._next_event_sequence(connection, validated.run_id)
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
                              "session_payment_claims", "graphs", "planner_transcripts", "agent_transcripts", "runs",
                              "inbox_items", "inbox_document_keys", "autopilot_state"):
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
