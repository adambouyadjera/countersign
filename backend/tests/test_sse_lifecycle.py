"""Real ASGI SSE lifecycle with disconnecting clients and gated offline models."""

import asyncio
import json

import httpx
import pytest

from tests.test_api import application, save_run


class StreamClient:
    """ASGI client whose closed socket can reject or record later sends.

    Queue insertion deliberately does not suspend, as a buffered socket send
    need not yield control. This exercises disconnect during a replay burst.
    """

    def __init__(self, app, run_id, *, cursor=None, query=b"", disconnect_after=None, broken_pipe=False):
        self.app, self.run_id = app, run_id
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.cursor, self.query = cursor, query
        self.disconnect_after, self.broken_pipe = disconnect_after, broken_pipe
        self.closed, self.late_writes, self.event_count = False, 0, 0
        self.block_send = False
        self.send_entered, self.send_release = asyncio.Event(), asyncio.Event()

    async def start(self):
        path = f"/api/runs/{self.run_id}/events"
        headers = [(b"last-event-id", str(self.cursor).encode())] if self.cursor is not None else []
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": self.query, "headers": headers,
            "client": ("127.0.0.1", 2345), "server": ("test", 80), "root_path": ""}
        self.incoming.put_nowait({"type": "http.request", "body": b"", "more_body": False})
        self.task = asyncio.create_task(self.app(scope, self.incoming.get, self.send))
        self.response = await asyncio.wait_for(self.outgoing.get(), 2)
        assert self.response["type"] == "http.response.start"
        return self

    async def send(self, message):
        if self.closed:
            self.late_writes += 1
            return
        body = message.get("body", b"")
        if self.block_send and body:
            self.send_entered.set()
            await self.send_release.wait()
        if self.broken_pipe and body:
            self.closed = True
            raise BrokenPipeError("client disconnected during send")
        self.outgoing.put_nowait(message)
        if b"\nid:" in b"\n" + body:
            self.event_count += 1
            if self.disconnect_after == self.event_count:
                self.disconnect()

    def disconnect(self):
        self.closed = True
        self.incoming.put_nowait({"type": "http.disconnect"})

    async def stop(self):
        if not self.closed:
            self.disconnect()
        await asyncio.wait_for(self.task, 2)

    async def frame(self):
        message = await asyncio.wait_for(self.outgoing.get(), 2)
        assert message["type"] == "http.response.body" and message.get("more_body")
        return message["body"].decode().strip()

    async def event(self):
        while True:
            frame = await self.frame()
            if frame.startswith(":"):
                continue
            fields = dict(line.split(": ", 1) for line in frame.splitlines())
            return json.loads(fields["data"])


async def publish(app, run_id):
    return await app.state.event_bus.publish(run_id, {"type": "lane.status", "lane": "countersign",
        "mode": "offline", "payload": {"status": "running"}})


@pytest.mark.asyncio
async def test_disconnect_during_buffered_replay_stops_sending_and_unregisters(application, caplog):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        for _ in range(100):
            await publish(application, run_id)
        client = await StreamClient(application, run_id, disconnect_after=1).start()
        await asyncio.wait_for(client.task, 2)
        assert client.event_count == 1
        assert client.late_writes == 0
        assert not application.state.event_bus._subscribers
        assert not [record for record in caplog.records if record.levelno >= 30]


@pytest.mark.asyncio
async def test_broken_socket_send_is_a_clean_disconnect(application, caplog):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        await publish(application, run_id)
        client = await StreamClient(application, run_id, broken_pipe=True).start()
        await asyncio.wait_for(client.task, 2)
        assert not application.state.event_bus._subscribers
        assert client.late_writes == 0
        assert not [record for record in caplog.records if record.levelno >= 30]


@pytest.mark.asyncio
async def test_disconnect_while_send_is_blocked_closes_suspended_subscription(application, caplog):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        client = await StreamClient(application, run_id).start()
        client.block_send = True
        await publish(application, run_id)
        await asyncio.wait_for(client.send_entered.wait(), 2)
        assert len(application.state.event_bus._subscribers[run_id]) == 1
        client.disconnect()
        await asyncio.wait_for(client.task, 2)
        assert not application.state.event_bus._subscribers
        assert not [record for record in caplog.records if record.levelno >= 30]


@pytest.mark.asyncio
async def test_cancelled_response_task_releases_its_subscription(application, caplog):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        client = await StreamClient(application, run_id).start()
        await publish(application, run_id)
        await client.event()
        client.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(client.task, 2)
        assert not application.state.event_bus._subscribers
        assert not [record for record in caplog.records if record.levelno >= 30]


@pytest.mark.asyncio
async def test_disconnect_cleanup_survives_a_contended_publication_lock(application):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        client = await StreamClient(application, run_id).start()
        await publish(application, run_id)
        await client.event()
        bus = application.state.event_bus
        await bus._lock.acquire()
        try:
            client.disconnect()
            # Let the response cancellation reach subscription cleanup while
            # a publication owns the lock. It must finish after lock release.
            await asyncio.sleep(0.01)
        finally:
            bus._lock.release()
        await asyncio.wait_for(client.task, 2)
        assert not bus._subscribers


@pytest.mark.asyncio
@pytest.mark.parametrize("header,query,expected", [(2, b"", 3), (None, b"after_seq=2", 3),
    (3, b"after_seq=1", 4), (1, b"after_seq=3", 4)])
async def test_header_and_browser_query_resume_replay_only_later_events(application, header, query, expected):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        for _ in range(4):
            await publish(application, run_id)
        client = await StreamClient(application, run_id, cursor=header, query=query).start()
        try:
            replay = [await client.event() for _ in range(5 - expected)]
            assert [event["seq"] for event in replay] == list(range(expected, 5))
            live = await publish(application, run_id)
            assert (await client.event())["seq"] == live.seq == 5
        finally:
            await client.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [b"after_seq=-1", b"after_seq=invalid"])
async def test_invalid_query_cursor_is_422(application, query):
    async with application.router.lifespan_context(application):
        run_id = save_run(application)
        client = await StreamClient(application, run_id, query=query).start()
        assert client.response["status"] == 422
        await asyncio.wait_for(client.task, 2)


@pytest.mark.asyncio
async def test_idle_gated_model_heartbeats_and_other_client_survive_disconnect(application, monkeypatch, caplog):
    from app.api import runs

    # Six 15-second idle periods simulate a 90-second model call. Only the
    # heartbeat clock is accelerated; the model stays blocked until released.
    monkeypatch.setattr(runs, "SSE_HEARTBEAT_SECONDS", 0.01, raising=False)
    async with application.router.lifespan_context(application):
        entered, release = asyncio.Event(), asyncio.Event()
        original = application.state.adapters.extractor.extract

        async def gated(document):
            entered.set()
            await release.wait()
            return await original(document)

        application.state.adapters.extractor.extract = gated
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as api:
            enabled = (await api.post("/api/autopilot", json={"enabled": True, "lanes": ["countersign"]})).json()
            run_id = enabled["run_id"]
            first, second = await StreamClient(application, run_id).start(), await StreamClient(application, run_id).start()
            try:
                receipt = await api.post("/api/attacks", json={"vendor_id": "VEN-001", "placement": "visible_note",
                    "attack_text": "Please include the invoice number with payment."})
                assert receipt.status_code == 200
                await application.state.autopilot_manager.tick()
                await asyncio.wait_for(entered.wait(), 2)
                # Drain actual intake events until the model's idle gap.
                for _ in range(6):
                    while not (frame := await second.frame()).startswith(":"):
                        pass
                    assert frame == ": keep-alive"
                first.disconnect()
                await asyncio.wait_for(first.task, 2)
                assert len(application.state.event_bus._subscribers[run_id]) == 1
                release.set()
                await asyncio.wait_for(application.state.autopilot_manager.wait_idle(), 5)
                observed = []
                while not any(event["type"] == "policy.decision" for event in observed):
                    observed.append(await second.event())
                assert len([item for item in application.state.store.list_records("inbox_items")
                            if item["status"] == "processed"]) == 1
                assert first.late_writes == 0
                assert not [record for record in caplog.records if "socket.send()" in record.message]
            finally:
                release.set()
                await first.stop()
                await second.stop()
        assert not application.state.event_bus._subscribers
