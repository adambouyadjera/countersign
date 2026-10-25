"""Disconnect-aware delivery around the SSE library's ping/replay tasks."""

from __future__ import annotations

import anyio
from fastapi import Request
from sse_starlette import EventSourceResponse
from starlette.requests import ClientDisconnect


class RunEventSourceResponse(EventSourceResponse):
    """Treat closed transports as connection completion, not run failures.

    The library watches ASGI disconnects concurrently. Buffered replay sends
    may never suspend, starving that watcher. Checkpoint and check the request
    before every send (including pings/final frames), and close the iterator
    explicitly so its per-client subscription is always removed.
    """

    def __init__(self, content, *, request: Request, **kwargs):
        self.request = request
        self.disconnected = False
        super().__init__(content, client_close_handler_callable=self._on_disconnect, **kwargs)

    async def _on_disconnect(self, message):
        self.disconnected = True

    async def __call__(self, scope, receive, send):
        async def send_if_connected(message):
            # A successful socket/queue write need not yield to the disconnect
            # watcher. Fairness here prevents a large replay flooding a closed
            # transport before the watcher has run.
            await anyio.lowlevel.checkpoint()
            if self.disconnected or await self.request.is_disconnected():
                self.disconnected = True
                raise ClientDisconnect()
            try:
                await send(message)
            except OSError:
                self.disconnected = True
                raise ClientDisconnect() from None

        try:
            try:
                await super().__call__(scope, receive, send_if_connected)
            except* ClientDisconnect:
                # A send can discover closure before ASGI receive does. The
                # task group cancels this connection's ping/listener tasks.
                pass
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
