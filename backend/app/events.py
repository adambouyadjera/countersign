"""Validated, durable per-run events with race-free in-process subscriptions."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from typing import Any

from app.contract_models import RunEvent
from app.store import Store


class EventBus:
    """Persist events before delivering them to every local subscriber.

    Use one bus per application process. The shared lock makes replay selection
    and subscriber registration atomic with respect to publication, so an event
    arriving during replay is neither lost nor delivered twice. SQLite owns the
    sequence counter so it survives application restarts.
    """

    def __init__(self, store: Store, bookkeeper=None) -> None:
        self.store = store
        self.bookkeeper = bookkeeper
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[asyncio.Queue[RunEvent]]] = defaultdict(set)

    async def publish(self, run_id: str, event: RunEvent | Mapping[str, Any]) -> RunEvent:
        async with self._lock:
            with self.store.transaction():
                validated = self.store.append_event(run_id, event)
                delivered = [validated]
                if self.bookkeeper is not None:
                    entries = self.bookkeeper.payment_postings(validated)
                    delivered.extend(self._posted_events(run_id, entries, validated.mode))
            for item in delivered:
                for queue in self._subscribers.get(run_id, ()):
                    queue.put_nowait(item.model_copy(deep=True))
            return validated

    def _posted_events(self, run_id, entries, mode):
        return [self.store.append_event(run_id, {
            "type": "ledger.posted", "lane": "countersign", "mode": mode or "live",
            "payload": {"entry": entry.model_dump(mode="python")},
        }) for entry in entries]

    async def before_payment(self):
        if self.bookkeeper is not None:
            await self.bookkeeper.before_payment()

    async def accept_bill(self, *, mode, authorize=None, **values):
        async with self._lock:
            with self.store.transaction():
                if authorize is not None:
                    authorize()
                entries = self.bookkeeper.accept_bill(**values) if self.bookkeeper is not None else []
                delivered = self._posted_events(values["run_id"], entries, mode)
            for item in delivered:
                for queue in self._subscribers.get(item.run_id, ()):
                    queue.put_nowait(item.model_copy(deep=True))

    async def reverse_entry(self, entry_id, *, created_by, memo):
        """Append a correction and its event together in the original run."""
        if self.bookkeeper is None:
            raise ValueError("Journal corrections require the configured bookkeeper")
        ledger = self.bookkeeper.ledger
        async with self._lock:
            with self.store.transaction():
                context = ledger.entry_context(entry_id)
                if context is None or context["run_id"] is None:
                    raise ValueError("A runtime correction requires its original journal/run binding")
                run_id = context["run_id"]
                entry = ledger.reverse(entry_id, created_by=created_by, memo=memo)
                events = self.store.list_events(run_id)
                already_emitted = any(event.type == "ledger.posted" and event.payload.entry.entry_id == entry.entry_id
                                      for event in events)
                original_mode = next((event.mode for event in events if event.type == "ledger.posted"
                                      and event.payload.entry.entry_id == entry_id), None)
                mode = original_mode or context["bank_mode"] or self.bookkeeper.reconciler.bank.mode
                delivered = [] if already_emitted else self._posted_events(run_id, [entry], mode)
            for item in delivered:
                for queue in self._subscribers.get(run_id, ()):
                    queue.put_nowait(item.model_copy(deep=True))
            return entry

    async def subscribe(self, run_id: str, last_seq: int = 0) -> AsyncIterator[RunEvent]:
        """Yield stored events after the cursor, then await future events.

        Completion events do not close a run: approvals may produce new events
        later. Closing or cancelling the generator removes its subscription.
        """
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        registered = False
        try:
            async with self._lock:
                replay = self.store.list_events(run_id, last_seq)
                self._subscribers[run_id].add(queue)
                registered = True
            for event in replay:
                last_seq = event.seq
                yield event
            while True:
                event = await queue.get()
                if event.seq > last_seq:
                    last_seq = event.seq
                    yield event
        finally:
            if registered:
                async with self._lock:
                    subscribers = self._subscribers.get(run_id)
                    if subscribers is not None:
                        subscribers.discard(queue)
                        if not subscribers:
                            self._subscribers.pop(run_id, None)
