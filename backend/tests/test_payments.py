"""Payment execution validates the bank result and reserves invoices durably."""

import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.adapters.bank import BankError, OfflineBankAdapter
from app.events import EventBus
from app.pipeline.payments import DuplicatePaymentError, PaymentExecutor
from app.store import Store
from app.world.seed import seed_world


@pytest_asyncio.fixture
async def payment_world(tmp_path):
    store = Store(tmp_path / "payments.sqlite")
    store.initialize()
    bank = OfflineBankAdapter(store=store)
    await seed_world(store, bank)
    executor = PaymentExecutor(store, EventBus(store), bank)
    try:
        yield store, bank, executor
    finally:
        store.close()


def args(**changes):
    return dict(run_id="run-1", invoice_id="invoice-1", invoice_number="INV-001",
                vendor_id="VEN-001", amount_cents=125000,
                to_display_number="100100001111", via="policy", **changes)


@pytest.mark.asyncio
async def test_payment_reserves_invoice_persists_binding_and_emits_actual_bank_result(payment_world):
    store, bank, executor = payment_world
    bank.transfer = AsyncMock(wraps=bank.transfer)
    payment = await executor.execute(**args())
    assert payment.transfer_id.startswith("offline-transfer-")
    assert payment.transfer_status == "completed" and payment.payee_on_file
    assert bank.transfer.await_count == 1
    assert store.has_session_payment("inv-001")
    record = store.get_record("transfers", payment.transfer_id)
    assert (record["run_id"], record["invoice_id"], record["vendor_id"]) == ("run-1", "invoice-1", "VEN-001")
    assert record["invoice_number"] == "INV-001"
    event = store.list_events("run-1")[0]
    assert event.type == "payment.executed" and event.lane == "countersign" and event.mode == "offline"
    assert event.payload.payment == payment
    with pytest.raises(DuplicatePaymentError):
        await executor.execute(**{**args(), "run_id": "run-2"})
    assert bank.transfer.await_count == 1


@pytest.mark.asyncio
async def test_payment_preflight_never_reserves_or_pays_mismatched_or_unknown_accounts(payment_world):
    store, bank, executor = payment_world
    bank.transfer = AsyncMock(wraps=bank.transfer)
    with pytest.raises(BankError, match="trusted vendor"):
        await executor.execute(**{**args(), "to_display_number": "999900009999"})
    with pytest.raises(BankError, match="not seeded"):
        await executor.execute(**{**args(), "via": "approval", "to_display_number": "444433332222"})
    assert not store.has_session_payment("INV-001")
    bank.transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_failure_cannot_be_resubmitted_after_restart(payment_world):
    store, bank, executor = payment_world
    bank.transfer = AsyncMock(side_effect=BankError("Synthetic timeout"))
    with pytest.raises(BankError):
        await executor.execute(**args())
    claims = store.list_records("session_payment_claims")
    assert len(claims) == 1 and claims[0]["state"] == "uncertain"
    other_store = Store(store.path)
    other_store.initialize()
    try:
        restarted = PaymentExecutor(other_store, EventBus(other_store), bank)
        with pytest.raises(DuplicatePaymentError):
            await restarted.execute(**args())
        assert other_store.has_session_payment(" INV-001 ")
        assert bank.transfer.await_count == 1
    finally:
        other_store.close()


@pytest.mark.asyncio
async def test_concurrent_executors_use_one_cross_connection_invoice_claim(payment_world):
    store, bank, executor = payment_world
    entered, release = asyncio.Event(), asyncio.Event()
    original = bank.transfer

    async def in_flight(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    bank.transfer = AsyncMock(side_effect=in_flight)
    first = asyncio.create_task(executor.execute(**args()))
    await entered.wait()
    other_store = Store(store.path)
    other_store.initialize()
    try:
        other = PaymentExecutor(other_store, EventBus(other_store), bank)
        with pytest.raises(DuplicatePaymentError):
            await other.execute(**{**args(), "run_id": "run-2"})
        release.set()
        await first
        assert bank.transfer.await_count == 1
    finally:
        release.set()
        other_store.close()


@pytest.mark.asyncio
async def test_malformed_bank_payment_result_never_emits_success_and_keeps_reservation(payment_world):
    store, bank, executor = payment_world
    original = bank.transfer

    async def wrong_destination(**kwargs):
        payment = await original(**kwargs)
        return payment.model_copy(update={"to_account_id": "unrequested-account"})

    bank.transfer = AsyncMock(side_effect=wrong_destination)
    with pytest.raises(BankError, match="reconciliation"):
        await executor.execute(**args())
    assert not store.list_events("run-1")
    assert store.has_session_payment("INV-001")
    assert store.list_records("session_payment_claims")[0]["state"] == "uncertain"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_binding", [False, True])
async def test_current_session_historical_payment_is_duplicate_without_a_b6_claim(payment_world, legacy_binding):
    from datetime import timedelta
    from app.contract_models import Run

    store, bank, executor = payment_world
    record = {"transfer_id": "historical-submission", "ts": (store.session_started_at + timedelta(seconds=1)).isoformat(),
              "status": "pending", "run_id": "legacy-run", "invoice_id": "legacy-invoice"}
    if legacy_binding:
        store.save_run(Run(run_id="legacy-run", batch_id="legacy-batch", instruction="trusted",
                           created_at=store.session_started_at, lanes=[]))
        store.save_record("batches", "legacy-batch", {
            "batch_id": "legacy-batch", "invoices": [{"invoice_id": "legacy-invoice", "invoice_number": "INV-001"}],
        })
    else:
        record.update(invoice_number="INV-001", session_started_at=store.session_started_at.isoformat())
    store.save_record("transfers", record["transfer_id"], record)
    bank.transfer = AsyncMock(wraps=bank.transfer)
    assert store.has_session_payment(" inv-001 ")
    assert store.list_records("session_payment_claims") == []
    with pytest.raises(DuplicatePaymentError):
        await executor.execute(**args())
    bank.transfer.assert_not_awaited()
    assert store.list_records("session_payment_claims") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_session", [False, True])
async def test_earlier_session_historical_transfer_does_not_block_current_payment(payment_world, explicit_session):
    from datetime import timedelta

    store, bank, executor = payment_world
    earlier = store.session_started_at - timedelta(seconds=1)
    record = {"transfer_id": "old-transfer", "invoice_number": "INV-001", "status": "completed",
              "ts": earlier.isoformat()}
    if explicit_session:
        # An explicit old session wins even if a later refresh changed ts.
        record.update(session_started_at=earlier.isoformat(), ts=(store.session_started_at + timedelta(seconds=1)).isoformat())
    store.save_record("transfers", record["transfer_id"], record)
    assert not store.has_session_payment("INV-001")
    payment = await executor.execute(**args())
    assert payment.transfer_id != "old-transfer"


@pytest.mark.asyncio
async def test_unbound_current_session_bank_smoke_transfer_is_not_an_invoice_duplicate(payment_world):
    from datetime import timedelta

    store, bank, executor = payment_world
    store.save_record("transfers", "smoke-transfer", {
        "transfer_id": "smoke-transfer", "status": "completed",
        "ts": (store.session_started_at + timedelta(seconds=1)).isoformat(),
        "description": "Countersign B3 live $1.00 paired smoke test",
    })
    assert not store.has_session_payment("INV-001")
    assert not store.has_session_payment("Countersign B3 live $1.00 paired smoke test")
