"""Real-response correlation and failure safety for non-atomic bank payments."""

import asyncio
import json

import httpx
from pydantic import SecretStr
import pytest
import respx

from app.adapters.bank import BankError, LiveBankAdapter
from app.adapters.paired_payments import (
    PairedPaymentError, execute_paired_payment, refresh_paired_payments,
)
from app.store import Store


@pytest.fixture
def bank():
    store = Store(":memory:")
    store.initialize()
    instance = LiveBankAdapter(
        key=SecretStr("synthetic-secret"), base_url="https://bank.invalid", store=store,
    )
    yield instance
    store.close()


def account_routes(balance=100):
    respx.get("https://bank.invalid/accounts/company").mock(
        return_value=httpx.Response(200, json={"_id": "company", "balance": balance}))
    respx.get("https://bank.invalid/accounts/vendor").mock(
        return_value=httpx.Response(200, json={"_id": "vendor", "balance": 0}))


def leg(identifier, status="pending", **changes):
    return {"_id": identifier, "amount": 1, "status": status, **changes}


def creation_routes():
    withdrawal = respx.post("https://bank.invalid/accounts/company/withdrawals").mock(
        return_value=httpx.Response(201, json={"objectCreated": leg("withdrawal-real", payer_id="company")}))
    deposit = respx.post("https://bank.invalid/accounts/vendor/deposits").mock(
        return_value=httpx.Response(201, json={"objectCreated": leg("deposit-real")}))
    return withdrawal, deposit


def readback_routes(status="pending", **withdrawal_changes):
    respx.get("https://bank.invalid/withdrawal/withdrawal-real").mock(
        return_value=httpx.Response(200, json=leg("withdrawal-real", status, payer_id="company", **withdrawal_changes)))
    respx.get("https://bank.invalid/deposits/deposit-real").mock(
        return_value=httpx.Response(200, json=leg("deposit-real", status)))


@pytest.mark.asyncio
@respx.mock
async def test_pair_returns_both_actual_ids_status_and_persists_debit_before_credit(bank):
    account_routes()
    withdrawal, _ = creation_routes()

    def deposit_response(request):
        attempts = bank._store.list_records("bank_payment_attempts")
        assert len(attempts) == 1
        assert attempts[0]["withdrawal_id"] == "withdrawal-real"
        assert attempts[0]["state"] == "deposit_submitting"
        return httpx.Response(201, json={"objectCreated": leg("deposit-real")})

    respx.post("https://bank.invalid/accounts/vendor/deposits").mock(side_effect=deposit_response)
    result = await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    assert result["transfer_id"] == "withdrawal-real"
    assert result["status"] == "pending" and result["mode"] == "live"
    assert result["detail"] == {
        "method": "paired_withdrawal_deposit", "atomic": False,
        "withdrawal_id": "withdrawal-real", "deposit_id": "deposit-real",
        "withdrawal_status": "pending", "deposit_status": "pending",
    }
    payload = json.loads(withdrawal.calls[0].request.content)
    assert payload["amount"] == 1.0 and payload["status"] == "pending"
    assert payload["medium"] == "balance" and payload["description"] == "INV-001"
    assert set(payload) == {"medium", "amount", "status", "transaction_date", "description"}
    assert bank._store.list_records("bank_payment_attempts")[0]["state"] == "both_created"


@pytest.mark.asyncio
@respx.mock
async def test_repeated_payment_reads_back_same_ids_without_another_debit(bank):
    account_routes()
    withdrawal, deposit = creation_routes()
    first = await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    readback_routes("completed")
    second = await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    assert withdrawal.call_count == deposit.call_count == 1
    assert second["transfer_id"] == first["transfer_id"]
    assert second["status"] == "completed" and second["ts"] == first["ts"]
    assert len(await refresh_paired_payments(bank, "vendor")) == 1
    assert await refresh_paired_payments(bank, "unrelated") == []


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("withdrawal_status,deposit_status,expected", [
    ("pending", "completed", "pending"),
    ("pending", "rejected", "rejected"),
])
async def test_one_completed_leg_never_overstates_pair_status(bank, withdrawal_status, deposit_status, expected):
    account_routes()
    respx.post("https://bank.invalid/accounts/company/withdrawals").mock(
        return_value=httpx.Response(201, json={"objectCreated": leg("withdrawal-real", withdrawal_status)}))
    respx.post("https://bank.invalid/accounts/vendor/deposits").mock(
        return_value=httpx.Response(201, json={"objectCreated": leg("deposit-real", deposit_status)}))
    result = await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    assert result["status"] == expected
    assert result["detail"]["withdrawal_status"] == withdrawal_status
    assert result["detail"]["deposit_status"] == deposit_status


@pytest.mark.asyncio
@respx.mock
async def test_deposit_failure_retains_real_debit_and_never_reposts_on_retry(bank):
    account_routes()
    withdrawal, _ = creation_routes()
    deposit = respx.post("https://bank.invalid/accounts/vendor/deposits").mock(
        return_value=httpx.Response(500, json="synthetic-secret must not leak"))
    for _ in range(2):
        with pytest.raises(PairedPaymentError) as caught:
            await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
        assert caught.value.withdrawal_id == "withdrawal-real"
        assert caught.value.deposit_id is None
        assert "synthetic-secret" not in str(caught.value)
    assert withdrawal.call_count == deposit.call_count == 1
    attempt = bank._store.list_records("bank_payment_attempts")[0]
    assert attempt["state"] == "deposit_unconfirmed"
    assert attempt["withdrawal_id"] == "withdrawal-real"


@pytest.mark.asyncio
@respx.mock
async def test_failed_withdrawal_status_does_not_create_a_credit(bank):
    account_routes()
    respx.post("https://bank.invalid/accounts/company/withdrawals").mock(
        return_value=httpx.Response(201, json={"objectCreated": leg("withdrawal-real", "rejected")}))
    deposit = respx.post("https://bank.invalid/accounts/vendor/deposits").mock(return_value=httpx.Response(500))
    with pytest.raises(PairedPaymentError, match="rejected the withdrawal"):
        await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    assert deposit.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_missing_returned_status_retains_id_and_does_not_assume_requested_pending(bank):
    account_routes()
    respx.post("https://bank.invalid/accounts/company/withdrawals").mock(
        return_value=httpx.Response(201, json={"objectCreated": {"_id": "withdrawal-real", "amount": 1}}))
    with pytest.raises(PairedPaymentError) as caught:
        await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    assert caught.value.withdrawal_id == "withdrawal-real"
    attempt = bank._store.list_records("bank_payment_attempts")[0]
    assert "withdrawal_status" not in attempt


@pytest.mark.asyncio
@respx.mock
async def test_insufficient_funds_never_submits_a_leg(bank):
    account_routes(balance=0)
    with pytest.raises(BankError, match="Insufficient"):
        await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    assert bank._store.list_records("bank_payment_attempts") == []
    assert all(call.request.method == "GET" for call in respx.calls)


@pytest.mark.asyncio
@respx.mock
async def test_fractional_dollars_are_rejected_before_any_http_or_attempt(bank):
    with pytest.raises(ValueError, match="whole-dollar"):
        await execute_paired_payment(bank, "company", "vendor", 101, "INV-001")
    assert len(respx.calls) == 0
    assert bank._store.list_records("bank_payment_attempts") == []


@pytest.mark.asyncio
@respx.mock
async def test_readback_mismatch_never_updates_local_status_to_completed(bank):
    account_routes()
    creation_routes()
    await execute_paired_payment(bank, "company", "vendor", 100, "INV-001")
    readback_routes("completed", amount=2)
    with pytest.raises(BankError, match="amount differing"):
        await refresh_paired_payments(bank, "company")
    assert bank._store.list_records("bank_payment_attempts")[0]["withdrawal_status"] == "pending"


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_identical_calls_submit_only_one_pair(bank):
    account_routes()
    withdrawal, deposit = creation_routes()
    readback_routes()
    first, second = await asyncio.gather(*(
        execute_paired_payment(bank, "company", "vendor", 100, "INV-001") for _ in range(2)
    ))
    assert first["transfer_id"] == second["transfer_id"]
    assert withdrawal.call_count == deposit.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_two_adapters_atomically_claim_one_payment_before_posting(bank):
    second_bank = LiveBankAdapter(
        key=SecretStr("synthetic-secret"), base_url="https://bank.invalid", store=bank._store,
    )
    both_checked = asyncio.Event()
    other_worker_finished = asyncio.Event()
    checked = 0

    async def source_response(request):
        nonlocal checked
        checked += 1
        if checked == 2:
            both_checked.set()
        await both_checked.wait()
        return httpx.Response(200, json={"_id": "company", "balance": 100})

    async def withdrawal_response(request):
        # The other adapter has already observed no attempt, so it must lose
        # the atomic claim rather than relying on the initial existence check.
        await other_worker_finished.wait()
        return httpx.Response(201, json={"objectCreated": leg("withdrawal-real")})

    respx.get("https://bank.invalid/accounts/company").mock(side_effect=source_response)
    respx.get("https://bank.invalid/accounts/vendor").mock(
        return_value=httpx.Response(200, json={"_id": "vendor", "balance": 0}))
    withdrawal = respx.post("https://bank.invalid/accounts/company/withdrawals").mock(
        side_effect=withdrawal_response)
    deposit = respx.post("https://bank.invalid/accounts/vendor/deposits").mock(
        return_value=httpx.Response(201, json={"objectCreated": leg("deposit-real")}))

    async def worker(adapter):
        try:
            return await execute_paired_payment(adapter, "company", "vendor", 100, "INV-001")
        except PairedPaymentError as error:
            other_worker_finished.set()
            return error

    results = await asyncio.wait_for(asyncio.gather(worker(bank), worker(second_bank)), timeout=2)
    assert sum(isinstance(result, PairedPaymentError) for result in results) == 1
    assert withdrawal.call_count == deposit.call_count == 1
