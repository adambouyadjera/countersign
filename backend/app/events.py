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

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, set[asyncio.Queue[RunEvent]]] = defaultdict(set)

    async def publish(self, run_id: str, event: RunEvent | Mapping[str, Any]) -> RunEvent:
        async with self._lock:
            validated = self.store.append_event(run_id, event)
            for queue in self._subscribers.get(run_id, ()):
                queue.put_nowait(validated.model_copy(deep=True))
            return validated

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
