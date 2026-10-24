"""Independent bank register reads cannot be synthesized from local payments."""

import httpx
from pydantic import SecretStr
import pytest

from app.adapters.bank import BankError, LiveBankAdapter


def bank_with(resources):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/accounts/company":
            return httpx.Response(200, json={"_id": "company", "balance": 100})
        name = request.url.path.rsplit("/", 1)[-1]
        value = resources.get(name, [])
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    bank = LiveBankAdapter(key=SecretStr("synthetic-test-key"), base_url="https://bank.invalid",
        transport=httpx.MockTransport(handle), payment_method="paired_withdrawal_deposit")
    return bank, calls


@pytest.mark.asyncio
async def test_reads_remote_withdrawals_deposits_and_native_transfers_without_local_attempts():
    bank, calls = bank_with({
        "withdrawals": [{"_id": "untracked-withdrawal", "amount": 12.34, "status": "pending", "payer_id": "company"}],
        "deposits": [{"_id": "untracked-deposit", "amount": 5, "status": "completed", "payee_id": "company"}],
        "transfers": [{"_id": "native", "amount": 2, "status": "pending", "payer_id": "company", "payee_id": "vendor"}],
    })
    results = {item.transfer_id: item for item in await bank.account_activity("company")}
    assert set(calls) == {"/accounts/company/transfers", "/accounts/company/withdrawals", "/accounts/company/deposits"}
    assert {key: value.cash_change_cents for key, value in results.items()} == {
        "untracked-withdrawal": -1234, "untracked-deposit": 500, "native": -200,
    }
    assert results["untracked-withdrawal"].status == "pending"


@pytest.mark.asyncio
async def test_old_account_local_transfer_keeps_unknown_direction_instead_of_inventing_it():
    bank, _ = bank_with({"transfers": [{"id": "old-diagnostic", "amount": 1, "status": "pending"}]})
    results = await bank.account_activity("company")
    assert results[0].cash_change_cents is None
    assert results[0].amount_cents == 100


@pytest.mark.asyncio
async def test_same_company_transfer_has_zero_net_cash_change():
    bank, _ = bank_with({"transfers": [{"_id": "internal", "amount": 1, "status": "completed",
                                      "payer_id": "company", "payee_id": "company"}]})
    assert (await bank.account_activity("company"))[0].cash_change_cents == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("collection,owner", [("withdrawals", "payer_id"), ("deposits", "payee_id")])
async def test_foreign_company_activity_is_rejected(collection, owner):
    bank, _ = bank_with({collection: [{"_id": "foreign", "amount": 1, "status": "pending", owner: "other"}]})
    with pytest.raises(BankError, match="different company"):
        await bank.account_activity("company")


@pytest.mark.asyncio
async def test_unavailable_bank_collection_cannot_be_reported_as_a_zero_difference():
    bank, _ = bank_with({"withdrawals": httpx.Response(503, json={"message": "private provider response"})})
    with pytest.raises(BankError, match="HTTP 503") as error:
        await bank.account_activity("company")
    assert "private" not in str(error.value)


@pytest.mark.asyncio
async def test_empty_collection_response_still_checks_that_account_exists():
    bank, calls = bank_with({name: httpx.Response(404, json=f"No {name} found for this account")
                             for name in ("transfers", "withdrawals", "deposits")})
    assert await bank.account_activity("company") == []
    assert calls.count("/accounts/company") == 3
