"""Small SQLite persistence layer shared by the API and service adapters."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from pydantic import BaseModel, TypeAdapter

from app.contract_models import Run, RunEvent


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "countersign.db"
_EVENT_ADAPTER = TypeAdapter(RunEvent)
_RECORD_TABLES = {
    "companies": "company_id",
    "vendors": "vendor_id",
    "purchase_orders": "purchase_order_id",
    "bank_directory": "display_number",
    "bank_seeds": "seed_id",
    "bank_payment_attempts": "attempt_id",
    "invoice_scoring": "invoice_id",
    "documents": "document_id",
    "batches": "batch_id",
    "runs": "run_id",
    "decisions": "decision_id",
    "approvals": "approval_id",
    "transfers": "transfer_id",
    "redteam_jobs": "job_id",
}
_RUN_SCOPED_TABLES = {"decisions", "approvals", "transfers"}


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
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN IMMEDIATE")
            try:
                seq = connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
                data.update(run_id=run_id, seq=seq, ts=datetime.now(timezone.utc))
                validated = _EVENT_ADAPTER.validate_python(data)
                self._insert_event(connection, validated)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
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
                for table in ("events", "decisions", "approvals", "runs"):
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
