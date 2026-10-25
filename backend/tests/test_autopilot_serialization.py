"""Independent service workers cannot race one long-lived planner transcript."""

import asyncio
import sys

import pytest
import pytest_asyncio

from app.adapters.bank import OfflineBankAdapter
from app.adapters.factory import build_adapters
from app.contract_models import AutopilotRequest, InboxRequest
from app.events import EventBus
from app.pipeline.autopilot import AutopilotManager
from app.pipeline.runner import RunManager
from app.store import Store
from app.world.batches import create_standard_batch
from app.world.seed import seed_world
from tests.test_autopilot import offline_settings


@pytest_asyncio.fixture
async def pair(tmp_path, monkeypatch):
    from app.config import Settings

    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    configuration = offline_settings()
    stores = [Store(tmp_path / "shared.db"), Store(tmp_path / "shared.db")]
    for store in stores:
        store.initialize()
    await seed_world(stores[0], OfflineBankAdapter(store=stores[0]))
    source = create_standard_batch(stores[0])
    runners = [RunManager(store, EventBus(store), build_adapters(configuration, store), configuration)
               for store in stores]
    services = [AutopilotManager(store, runner.bus, runner, poll_interval=3600)
                for store, runner in zip(stores, runners)]
    try:
        yield services, runners, stores, source
    finally:
        for service in services:
            await service.close()
        for runner in runners:
            await runner.close()
        for store in stores:
            store.close()


@pytest.mark.asyncio
async def test_two_managers_admit_independently_but_process_the_shared_run_serially(pair):
    services, runners, stores, source = pair
    first_started, release_first, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = 0
    maximum_active = 0
    observed = []

    async def execute_first(run_id, batch, lanes):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        first_started.set()
        await release_first.wait()
        observed.extend(invoice.document_id for invoice in batch.invoices)
        active -= 1

    async def execute_second(run_id, batch, lanes):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        second_started.set()
        observed.extend(invoice.document_id for invoice in batch.invoices)
        active -= 1

    runners[0].process_autopilot = execute_first
    runners[1].process_autopilot = execute_second
    await services[0].set(AutopilotRequest(enabled=True, lanes=["countersign"]))
    services[0].start()
    services[0].enqueue(InboxRequest(document_id=source.invoices[0].document_id, sender="first@example.com"))
    await services[0].tick()
    await asyncio.wait_for(first_started.wait(), timeout=2)

    services[1].start()
    later = services[1].enqueue(InboxRequest(document_id=source.invoices[1].document_id, sender="second@example.com"))
    await asyncio.wait_for(services[1].tick(), timeout=0.5)
    assert stores[1].get_record("inbox_items", later.item_id)["status"] == "processing"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(second_started.wait(), timeout=0.1)
    release_first.set()
    await asyncio.wait_for(asyncio.gather(*(service.wait_idle() for service in services)), timeout=2)

    assert maximum_active == 1
    assert observed == [source.invoices[0].document_id, source.invoices[1].document_id]
    assert {item["status"] for item in stores[0].list_records("inbox_items")} == {"processed"}
    assert services[0].get().run_id == services[1].get().run_id


@pytest.mark.asyncio
async def test_processing_lock_is_released_when_a_waiting_task_is_cancelled(pair):
    services, _, _, _ = pair
    entered = asyncio.Event()

    async def hold():
        async with services[0]._processing_slot():
            entered.set()
            await asyncio.Event().wait()

    owner = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), timeout=2)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    async def next_owner():
        async with services[1]._processing_slot():
            return True

    assert await asyncio.wait_for(next_owner(), timeout=1) is True


@pytest.mark.asyncio
async def test_os_lock_blocks_another_process_and_recovers_when_that_process_exits(pair):
    services, _, _, _ = pair
    lock_path = services[0]._lock_file
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    program = (
        "import fcntl,sys\n"
        "with open(sys.argv[1], 'a+b') as resource:\n"
        " fcntl.flock(resource, fcntl.LOCK_EX)\n"
        " print('locked', flush=True)\n"
        " sys.stdin.buffer.read(1)\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", program, str(lock_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    acquired = asyncio.Event()

    async def enter():
        async with services[0]._processing_slot():
            acquired.set()

    waiter = None
    try:
        assert await asyncio.wait_for(process.stdout.readline(), timeout=3) == b"locked\n"
        waiter = asyncio.create_task(enter())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(acquired.wait(), timeout=0.1)
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=3)
        await asyncio.wait_for(waiter, timeout=2)
        assert acquired.is_set()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if waiter is not None and not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
