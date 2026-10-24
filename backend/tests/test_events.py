"""Offline publication, durable replay, continuation, and subscription checks."""

import asyncio

import pytest
from pydantic import ValidationError

from app.events import EventBus
from app.store import Store


def lane_event(lane="countersign", **overrides):
    return {
        "type": "lane.status",
        "lane": lane,
        "mode": "offline",
        "payload": {"status": "running"},
        **overrides,
    }


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "events.db")
    store.initialize()
    yield store
    store.close()


def test_publish_validates_before_commit_and_shares_sequence_across_lanes(store):
    async def scenario():
        bus = EventBus(store)
        with pytest.raises(ValidationError):
            await bus.publish("run-test", lane_event(payload={"status": "not-valid"}))
        published = await asyncio.gather(*(
            bus.publish("run-test", lane_event("unguarded" if index % 2 else "countersign"))
            for index in range(20)
        ))
        assert [event.seq for event in published] == list(range(1, 21))
        assert all(event.run_id == "run-test" and event.mode == "offline" for event in published)
        assert store.list_events("run-test") == published
        assert (await bus.publish("other-run", lane_event())).seq == 1

    asyncio.run(scenario())


def test_subscription_replays_in_order_and_resumes_after_cursor(store):
    async def scenario():
        bus = EventBus(store)
        for _ in range(4):
            await bus.publish("run-test", lane_event())
        stream = bus.subscribe("run-test", last_seq=2)
        try:
            assert (await anext(stream)).seq == 3
            assert (await anext(stream)).seq == 4
            live = await bus.publish("run-test", lane_event())
            assert await asyncio.wait_for(anext(stream), 1) == live
        finally:
            await stream.aclose()
        assert not bus._subscribers

    asyncio.run(scenario())


def test_publication_during_replay_is_delivered_once_without_gap(store):
    async def scenario():
        bus = EventBus(store)
        await bus.publish("run-test", lane_event())
        await bus.publish("run-test", lane_event())
        stream = bus.subscribe("run-test")
        try:
            assert (await anext(stream)).seq == 1
            await bus.publish("run-test", lane_event())
            assert (await anext(stream)).seq == 2
            assert (await asyncio.wait_for(anext(stream), 1)).seq == 3
        finally:
            await stream.aclose()

    asyncio.run(scenario())


def test_run_completed_keeps_subscription_open_for_approval_events(store):
    async def scenario():
        bus = EventBus(store)
        await bus.publish("run-test", {"type": "run.completed", "lane": "system", "payload": {"tallies": {}}})
        stream = bus.subscribe("run-test")
        try:
            assert (await anext(stream)).type == "run.completed"
            approval = await bus.publish("run-test", {
                "type": "approval.resolved",
                "lane": "countersign",
                "mode": "offline",
                "payload": {"approval_id": "approval-test", "invoice_id": "invoice-test", "decision": "reject"},
            })
            assert (await asyncio.wait_for(anext(stream), 1)) == approval
        finally:
            await stream.aclose()

    asyncio.run(scenario())


def test_multiple_subscribers_receive_independent_events(store):
    async def scenario():
        bus = EventBus(store)
        await bus.publish("run-test", lane_event())
        first = bus.subscribe("run-test")
        second = bus.subscribe("run-test")
        try:
            await anext(first)
            await anext(second)
            published = await bus.publish("run-test", lane_event())
            published.payload.status = "failed"
            received_first = await anext(first)
            received_first.payload.status = "completed"
            received_second = await anext(second)
            assert received_second.payload.status == "running"
            assert store.list_events("run-test")[-1].payload.status == "running"
        finally:
            await first.aclose()
            await second.aclose()
        assert not bus._subscribers

    asyncio.run(scenario())


def test_cancelled_wait_removes_subscriber(store):
    async def scenario():
        bus = EventBus(store)
        stream = bus.subscribe("run-test")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(stream), 0.01)
        assert not bus._subscribers

    asyncio.run(scenario())


def test_resume_cursor_applies_to_future_events_too(store):
    async def scenario():
        bus = EventBus(store)
        store.append_event("run-test", lane_event())
        stream = bus.subscribe("run-test", last_seq=3)
        waiting = asyncio.create_task(anext(stream))
        try:
            # The first queued event may race with registration; replay and live
            # delivery must both respect the cursor either way.
            await bus.publish("run-test", lane_event())
            await bus.publish("run-test", lane_event())
            last = await bus.publish("run-test", lane_event())
            assert await asyncio.wait_for(waiting, 1) == last
        finally:
            if not waiting.done():
                waiting.cancel()
            await stream.aclose()
        assert not bus._subscribers

    asyncio.run(scenario())
