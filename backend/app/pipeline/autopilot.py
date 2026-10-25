"""Durable inbox admission and a sequential, long-lived AP run.

The dispatcher never waits for model or bank work: a slow invoice cannot hide
new arrivals from the Console. SQLite claims each document before work is
scheduled. Claimed documents are never automatically requeued after an
interruption, since an earlier payment may require reconciliation.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.contract_models import Autopilot, AutopilotRequest, Batch, DocumentText, InboxItem, InboxRequest


_LOGGER = logging.getLogger(__name__)
_MAX_WORK_ITEMS = 25


@dataclass(frozen=True)
class _Work:
    run_id: str
    lanes: tuple[str, ...]
    batch: Batch
    items: tuple[InboxItem, ...]


class AutopilotManager:
    def __init__(self, store, bus, run_manager, *, poll_interval=5):
        if poll_interval <= 0:
            raise ValueError("Autopilot poll interval must be positive")
        self.store, self.bus, self.run_manager = store, bus, run_manager
        self.poll_interval = poll_interval
        self._state_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._queue = asyncio.Queue()
        self._dispatcher = None
        self._processor = None
        self._closed = False
        self._lock_file = (None if str(store.path) == ":memory:" else
            Path(__file__).resolve().parents[2] / "data/generated/autopilot-locks" /
            (hashlib.sha256(str(Path(store.path).resolve()).encode()).hexdigest() + ".lock"))
        # Separate in-memory SQLite databases cannot share a run. Managers
        # using the same Store object still share one processing mutex.
        if not hasattr(store, "_autopilot_processing_lock"):
            store._autopilot_processing_lock = asyncio.Lock()
        self._memory_lock = store._autopilot_processing_lock

    def start(self):
        """Start actual background workers, without probing any provider."""
        if self._closed:
            raise RuntimeError("Autopilot manager is closed")
        if self._dispatcher is None:
            self._reconcile_queued()
            self._processor = asyncio.create_task(self._process(), name="autopilot-processor")
            self._dispatcher = asyncio.create_task(self._dispatch(), name="autopilot-dispatcher")

    def get(self) -> Autopilot:
        return self.store.get_autopilot_state()

    def list(self) -> list[InboxItem]:
        return sorted((InboxItem.model_validate(row) for row in self.store.list_records("inbox_items")),
                      key=lambda item: (item.received_at, item.item_id))

    def _reconcile_queued(self):
        # Older versions queued Attack Lab invoices but did not bind manual
        # runs to those rows. Intake's history lookup migrates those attempts
        # without scheduling another model or bank call.
        for item in self.list():
            if item.status == "queued":
                self.store.enqueue_inbox(item.document_id, item.sender, item.received_at)

    def _ensure_run(self, state):
        if state.run_id is not None and self.store.get_run(state.run_id) is not None:
            return state
        run = self.run_manager.create_autopilot(state.lanes)
        return state.model_copy(update={"run_id": run.run_id})

    async def set(self, body: AutopilotRequest) -> Autopilot:
        if self._closed:
            raise HTTPException(503, "Autopilot is shutting down")
        async with self._state_lock:
            with self.store.transaction():
                current = self.get()
                lanes = list(body.lanes) if body.lanes is not None else current.lanes
                if not lanes or len(set(lanes)) != len(lanes):
                    raise HTTPException(422, "Autopilot lanes must be nonempty and distinct")
                state = current.model_copy(update={"enabled": body.enabled, "lanes": lanes})
                if state.enabled:
                    state = self._ensure_run(state)
                self.store.save_autopilot_state(state)
        self._wake.set()
        return state

    def enqueue(self, body: InboxRequest) -> InboxItem:
        if self._closed:
            raise HTTPException(503, "Autopilot is shutting down")
        if not body.document_id.strip() or not body.sender.strip():
            raise HTTPException(422, "document_id and sender must be nonempty")
        self._source_batch(body.document_id)
        return self.store.enqueue_inbox(body.document_id, body.sender.strip())

    def _source_batch(self, document_id):
        raw = self.store.get_record("documents", document_id)
        if raw is None:
            raise HTTPException(404, "Document not found")
        try:
            document = DocumentText.model_validate(raw["text"])
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "Document is not a valid generated invoice") from None
        if (document.document_id != document_id or raw.get("document_id") != document_id
                or raw.get("invoice_id") != document.invoice_id
                or document.document_type not in (None, "invoice")):
            raise HTTPException(422, "Only generated invoice documents can enter the AP inbox")
        # This lookup reads only the server-owned source-batch association.
        # Attack classifications and answer-key/scoring fields never enter work.
        association = self.store.get_record("invoice_scoring", document.invoice_id) or {}
        preferred = self.store.get_record("batches", association["batch_id"]) if association.get("batch_id") else None
        candidates = [preferred] if preferred is not None else self.store.list_records("batches")
        autopilot = self.get()
        current = self.store.get_run(autopilot.run_id) if autopilot.run_id else None
        matches = []
        for record in candidates:
            if current is not None and record.get("batch_id") == current.batch_id:
                continue
            try:
                batch = Batch.model_validate(record)
            except (ValueError, TypeError):
                continue
            invoices = [invoice for invoice in batch.invoices
                        if invoice.document_id == document_id and invoice.invoice_id == document.invoice_id]
            if len(invoices) == 1:
                matches.append((batch, invoices[0]))
        if len(matches) != 1:
            raise HTTPException(422, "Document must belong to exactly one generated source invoice")
        return matches[0]

    def _batch(self, items):
        invoices, supporting, kinds = [], {}, set()
        for item in items:
            source, invoice = self._source_batch(item.document_id)
            invoices.append(invoice)
            kinds.add(source.kind)
            for document in source.supporting_documents or []:
                if document.invoice_id == invoice.invoice_id:
                    supporting[document.document_id] = document
        return Batch(batch_id=f"autopilot-work-{uuid4().hex}",
                     kind="attack" if "attack" in kinds else "bank_change" if supporting else "standard",
                     created_at=datetime.now(timezone.utc), invoices=invoices,
                     **({"supporting_documents": list(supporting.values())} if supporting else {}))

    async def tick(self):
        """Admit new documents and publish receipts; never await processing."""
        if self._closed:
            return
        async with self._state_lock:
            with self.store.transaction():
                state = self.get()
                if not state.enabled:
                    return
                self._reconcile_queued()
                state = self._ensure_run(state).model_copy(update={"last_checked_at": datetime.now(timezone.utc)})
                self.store.save_autopilot_state(state)
                items = self.store.claim_inbox(state.run_id)
            for offset in range(0, len(items), _MAX_WORK_ITEMS):
                claimed = tuple(items[offset:offset + _MAX_WORK_ITEMS])
                try:
                    batch = self._batch(claimed)
                    self.run_manager.attach_autopilot(state.run_id, batch, state.lanes)
                    # No run.started, document.loaded or model call precedes
                    # these receipts for the admitted invoices.
                    for item in claimed:
                        await self.run_manager._event(state.run_id, "inbox.received",
                            {"item": item.model_dump(mode="python")}, lane="system")
                    self._queue.put_nowait(_Work(state.run_id, tuple(state.lanes), batch, claimed))
                except (asyncio.CancelledError, Exception) as error:
                    for item in claimed:
                        await self._failed_item(state.run_id, item, type(error).__name__)
                    if isinstance(error, asyncio.CancelledError):
                        raise

    async def _failed_item(self, run_id, item, classification):
        try:
            await self.run_manager._event(run_id, "error", {
                "message": f"Autopilot invoice attempt requires review ({classification}); it will not be retried automatically.",
                "recoverable": False,
            }, lane="system")
        finally:
            self.store.finish_inbox(item.item_id)

    async def _dispatch(self):
        while not self._closed:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # The exception body could contain provider or document text.
                _LOGGER.error("Autopilot dispatcher failed (%s)", type(error).__name__)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def _processing_slot(self):
        """Serialize model/audit work across processes without locking SQLite.

        A file lock is released by the OS if a worker dies. A persistent SQL
        slot would strand future work after a crash; an expiring lease could
        let two live planners overwrite the same transcript. Intake remains
        independent while another worker holds this processing-only lock.
        """
        if self._lock_file is None:
            async with self._memory_lock:
                yield
            return
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            while not acquired:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    await asyncio.sleep(0.05)
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    async def _process(self):
        while not self._closed:
            work = await self._queue.get()
            try:
                async with self._processing_slot():
                    await self.run_manager.process_autopilot(work.run_id, work.batch, list(work.lanes))
            except asyncio.CancelledError:
                for item in work.items:
                    await self._failed_item(work.run_id, item, "Interrupted")
                raise
            except Exception as error:
                for item in work.items:
                    await self._failed_item(work.run_id, item, type(error).__name__)
            finally:
                for item in work.items:
                    self.store.finish_inbox(item.item_id)
                self._queue.task_done()

    async def wait_idle(self):
        await self._queue.join()

    async def close(self):
        self._closed = True
        for task in (self._dispatcher, self._processor):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (self._dispatcher, self._processor) if task is not None),
                             return_exceptions=True)
        # Claimed, accepted work is not silently returned to the queue on a
        # shutdown. Its durable terminal record and error require human review.
        while not self._queue.empty():
            work = self._queue.get_nowait()
            try:
                for item in work.items:
                    await self._failed_item(work.run_id, item, "InterruptedBeforeProcessing")
            finally:
                self._queue.task_done()
