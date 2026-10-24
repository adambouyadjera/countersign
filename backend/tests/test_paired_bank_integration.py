"""Check the actual adapter result and session receipts for correlated pairs."""

import httpx
from pydantic import SecretStr
import pytest
import respx

from app.adapters.bank import LiveBankAdapter
from app.adapters.payment_models import BankPayment
from app.store import Store


@pytest.mark.asyncio
@respx.mock
async def test_adapter_returns_payment_detail_and_only_counts_settled_pairs():
    store = Store(":memory:")
    store.initialize()
    try:
        store.save_record("vendors", "vendor", {
            "vendor_id": "vendor", "account_on_file_display": "2000",
        })
        for role, identifier, display in [("company", "company", "1000"), ("vendor", "vendor", "2000")]:
            store.save_record("bank_directory", display, {
                "role": role, "label": role.title(), "account_id": identifier,
                "display_number": display, "mode": "live",
            })
            respx.get(f"https://bank.invalid/accounts/{identifier}").mock(
                return_value=httpx.Response(200, json={"_id": identifier, "balance": 100 if role == "company" else 0}))
        bank = LiveBankAdapter(
            key=SecretStr("synthetic"), base_url="https://bank.invalid", store=store,
            payment_method="paired_withdrawal_deposit",
        )
        withdrawal = {"_id": "service-withdrawal", "amount": 1, "status": "pending", "payer_id": "company"}
        deposit = {"_id": "service-deposit", "amount": 1, "status": "pending"}
        respx.post("https://bank.invalid/accounts/company/withdrawals").mock(
            return_value=httpx.Response(201, json={"objectCreated": withdrawal}))
        respx.post("https://bank.invalid/accounts/vendor/deposits").mock(
            return_value=httpx.Response(201, json={"objectCreated": deposit}))
        withdrawal_read = respx.get("https://bank.invalid/withdrawal/service-withdrawal").mock(
            return_value=httpx.Response(200, json=withdrawal))
        deposit_read = respx.get("https://bank.invalid/deposits/service-deposit").mock(
            return_value=httpx.Response(200, json=deposit))
        payment = await bank.transfer("company", "vendor", 100, "INV-001")
        assert isinstance(payment, BankPayment)
        assert payment.transfer_id == payment.detail.withdrawal_id == "service-withdrawal"
        assert payment.model_dump()["detail"]["deposit_id"] == "service-deposit"
        assert payment.detail.atomic is False and payment.payee_on_file
        initial = await bank.get_balances(store.session_started_at)
        vendor = next(account for account in initial.accounts if account.role == "vendor")
        assert vendor.received_this_session_cents == 0
        withdrawal_read.mock(return_value=httpx.Response(200, json={**withdrawal, "status": "completed"}))
        deposit_read.mock(return_value=httpx.Response(200, json={**deposit, "status": "completed"}))
        settled = await bank.get_balances(store.session_started_at)
        vendor = next(account for account in settled.accounts if account.role == "vendor")
        assert vendor.received_this_session_cents == 100
        assert vendor.transfers_this_session[0].transfer_id == "service-withdrawal"
        assert vendor.balance_cents == 0  # Remote balances remain authoritative.
        saved = store.get_record("transfers", "service-withdrawal")
        assert saved["detail"]["deposit_status"] == "completed"
    finally:
        store.close()
