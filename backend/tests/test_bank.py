"""Bank transport contracts, integer cents, and honest offline ledger behavior."""

from datetime import datetime, timedelta, timezone
import json

import httpx
from pydantic import SecretStr
import pytest
import respx

from app.adapters.bank import BankError, LiveBankAdapter, OfflineBankAdapter
from app.store import Store


@pytest.fixture
def store():
    instance = Store(":memory:")
    instance.initialize()
    instance.save_record("vendors", "vendor", {
        "vendor_id": "vendor", "account_on_file_display": "2000000001",
    })
    for role, identifier, number, amount in [
        ("company", "company-account", "1000000001", 10000),
        ("vendor", "vendor-account", "2000000001", 0),
        ("attacker", "attacker-account", "9999000013", 0),
    ]:
        instance.save_record("bank_directory", number, {
            "role": role, "label": role.title(), "account_id": identifier,
            "customer_id": identifier + "-customer", "display_number": number,
            "mode": "offline", "initial_balance_cents": amount,
        })
    yield instance
    instance.close()


@pytest.mark.asyncio
async def test_offline_ledger_moves_cents_once_with_labelled_real_ledger_ids(store):
    bank = OfflineBankAdapter(store=store)
    payment = await bank.transfer("company-account", "vendor-account", 101, "Invoice INV-001")
    assert payment.bank_mode == "offline"
    assert payment.transfer_id.startswith("offline-transfer-")
    assert payment.transfer_status == "completed"
    assert payment.payee_on_file and payment.to_display_number == "2000000001"
    assert (await bank.get_balance("company-account")).balance_cents == 9899
    target = await bank.get_balance("vendor-account")
    assert target.balance_cents == 101 and target.mode == "offline"
    records = await bank.list_transfers("vendor-account")
    assert len(records) == 1 and records[0].transfer_id == payment.transfer_id
    assert records[0].description == "Invoice INV-001" and records[0].mode == "offline"
    # Copies protect ledger state from callers mutating read results.
    records[0].amount_cents = 200
    assert (await bank.list_transfers("company-account"))[0].amount_cents == 101
    balances = await bank.get_balances(store.session_started_at)
    vendor = next(a for a in balances.accounts if a.role == "vendor")
    assert vendor.received_this_session_cents == 101
    assert vendor.transfers_this_session[0].transfer_id == payment.transfer_id
    store.reset_demo()
    balances = await bank.get_balances(store.session_started_at)
    vendor = next(a for a in balances.accounts if a.role == "vendor")
    assert vendor.balance_cents == 101
    assert vendor.received_this_session_cents == 0


@pytest.mark.asyncio
async def test_offline_attacker_not_on_file_and_invalid_transfers_do_not_move_money(store):
    bank = OfflineBankAdapter(store=store)
    for amount in [0, -1, 1.0, True]:
        with pytest.raises(ValueError):
            await bank.transfer("company-account", "vendor-account", amount)
    with pytest.raises(BankError, match="Insufficient"):
        await bank.transfer("company-account", "vendor-account", 10001)
    with pytest.raises(BankError, match="Unknown"):
        await bank.transfer("company-account", "missing", 1)
    payment = await bank.transfer(from_account_id="company-account", to_account_id="attacker-account", amount_cents=100)
    assert not payment.payee_on_file
    assert (await bank.get_balance("company-account")).balance_cents == 9900


@pytest.mark.asyncio
async def test_offline_restart_replays_stored_transfers_exactly_once(store):
    first = OfflineBankAdapter(store=store)
    payment = await first.transfer("company-account", "vendor-account", 101)
    second = OfflineBankAdapter(store=store)
    for _ in range(3):
        assert (await second.get_balance("company-account")).balance_cents == 9899
        assert (await second.get_balance("vendor-account")).balance_cents == 101
    records = await second.list_transfers("vendor-account")
    assert len(records) == 1 and records[0].transfer_id == payment.transfer_id
    receipt = next(a for a in (await second.get_balances(store.session_started_at)).accounts if a.role == "vendor")
    assert receipt.balance_cents == receipt.received_this_session_cents == 101
    await second.transfer("company-account", "vendor-account", 1)
    assert (await second.get_balance("vendor-account")).balance_cents == 102


@pytest.mark.asyncio
async def test_directory_role_does_not_make_untrusted_account_on_file(store):
    attacker = store.get_record("bank_directory", "9999000013")
    attacker["role"] = "vendor"
    store.save_record("bank_directory", attacker["display_number"], attacker)
    bank = OfflineBankAdapter(store=store)
    payment = await bank.transfer("company-account", "attacker-account", 1)
    assert not payment.payee_on_file


@pytest.mark.asyncio
@respx.mock
async def test_live_transfer_posts_dollars_retains_real_status_id_and_refreshes(store):
    for row in store.list_records("bank_directory"):
        row["mode"] = "live"
        store.save_record("bank_directory", row["display_number"], row)
    bank = LiveBankAdapter(key=SecretStr("synthetic-secret"), base_url="https://bank.invalid", store=store)
    remote = {"_id": "real-service-transfer", "payer_id": "company-account", "payee_id": "vendor-account",
              "amount": 1.01, "status": "pending", "transaction_date": "2026-10-23", "description": "Invoice"}
    route = respx.post("https://bank.invalid/accounts/company-account/transfers").mock(
        return_value=httpx.Response(201, json={"code": 201, "objectCreated": remote}))
    payment = await bank.transfer("company-account", "vendor-account", 101, "Invoice")
    request = route.calls[0].request
    assert request.url.params["key"] == "synthetic-secret"
    payload = json.loads(request.content)
    assert payload["amount"] == 1.01 and payload["medium"] == "balance"
    assert payload["payee_id"] == "vendor-account" and "status" not in payload
    assert payment.transfer_id == remote["_id"] and payment.transfer_status == "pending"
    saved_ts = store.get_record("transfers", payment.transfer_id)["ts"]
    remote["status"] = "completed"
    for identifier, balance in [("company-account", 98.99), ("vendor-account", 1.01), ("attacker-account", 0)]:
        respx.get(f"https://bank.invalid/accounts/{identifier}").mock(
            return_value=httpx.Response(200, json={"_id": identifier, "balance": balance}))
        respx.get(f"https://bank.invalid/accounts/{identifier}/transfers").mock(
            return_value=httpx.Response(200, json=[remote] if identifier != "attacker-account" else []))
    balances = await bank.get_balances(store.session_started_at)
    vendor = next(a for a in balances.accounts if a.role == "vendor")
    assert vendor.balance_cents == vendor.received_this_session_cents == 101
    assert store.get_record("transfers", payment.transfer_id)["ts"] == saved_ts


@pytest.mark.asyncio
@respx.mock
async def test_pending_cancelled_and_old_transfers_are_not_receipts(store):
    for row in store.list_records("bank_directory"):
        row["mode"] = "live"
        store.save_record("bank_directory", row["display_number"], row)
    bank = LiveBankAdapter(key=SecretStr("synthetic"), base_url="https://bank.invalid", store=store)
    transfers = []
    for index, (status, ts) in enumerate([
        ("pending", store.session_started_at + timedelta(seconds=1)),
        ("cancelled", store.session_started_at + timedelta(seconds=1)),
        ("completed", store.session_started_at - timedelta(seconds=1)),
    ]):
        identifier = f"transfer-{index}"
        store.save_record("transfers", identifier, {
            "transfer_id": identifier, "from_account_id": "company-account", "to_account_id": "vendor-account",
            "amount_cents": 100, "status": status, "description": "", "ts": ts.isoformat(), "mode": "live",
        })
        transfers.append({"_id": identifier, "payer_id": "company-account", "payee_id": "vendor-account",
                          "amount": 1, "status": status, "transaction_date": "2026-10-23"})
    for identifier in ["company-account", "vendor-account", "attacker-account"]:
        respx.get(f"https://bank.invalid/accounts/{identifier}/transfers").mock(
            return_value=httpx.Response(200, json=transfers if identifier != "attacker-account" else []))
        respx.get(f"https://bank.invalid/accounts/{identifier}").mock(
            return_value=httpx.Response(200, json={"_id": identifier, "balance": 100}))
    balances = await bank.get_balances(store.session_started_at)
    assert all(a.received_this_session_cents == 0 for a in balances.accounts)


@pytest.mark.asyncio
@respx.mock
async def test_live_errors_are_sanitized_never_switch_to_offline():
    bank = LiveBankAdapter(key=SecretStr("synthetic-secret"), base_url="https://bank.invalid")
    respx.get("https://bank.invalid/accounts/account").mock(
        side_effect=httpx.ConnectError("https://bank.invalid?key=synthetic-secret"))
    with pytest.raises(BankError) as caught:
        await bank.get_balance("account")
    assert "synthetic-secret" not in str(caught.value)
    assert bank.mode == "live"


@pytest.mark.asyncio
@respx.mock
async def test_missing_transfer_id_never_fabricates_success():
    bank = LiveBankAdapter(key=SecretStr("synthetic"), base_url="https://bank.invalid")
    respx.post("https://bank.invalid/accounts/from/transfers").mock(
        return_value=httpx.Response(201, json={"code": 201, "message": "Accepted"}))
    with pytest.raises(BankError, match="resource id"):
        await bank.transfer("from", "to", 100)


@pytest.mark.asyncio
@respx.mock
async def test_incompatible_live_schema_never_drops_routing_or_retries(store):
    bank = LiveBankAdapter(key=SecretStr("synthetic-secret"), base_url="https://bank.invalid", store=store)
    route = respx.post("https://bank.invalid/accounts/from/transfers").mock(
        return_value=httpx.Response(400, json=(
            "3 validation errors for TransferCreate\n"
            "status\n  field required (type=value_error.missing)\n"
            "medium\n  extra fields not permitted (type=value_error.extra)\n"
            "payee_id\n  extra fields not permitted (type=value_error.extra)\n"
            "synthetic-secret"
        )))
    with pytest.raises(BankError, match="routing field") as caught:
        await bank.transfer("from", "to", 100)
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content)["payee_id"] == "to"
    assert "synthetic-secret" not in str(caught.value)
    assert bank.mode == "live" and store.list_records("transfers") == []


@pytest.mark.asyncio
@respx.mock
async def test_observed_empty_transfer_404_is_empty_only_for_existing_account():
    bank = LiveBankAdapter(key=SecretStr("synthetic-secret"), base_url="https://bank.invalid")
    respx.get("https://bank.invalid/accounts/existing/transfers").mock(
        return_value=httpx.Response(404, json="No transfers found for this account"))
    existence = respx.get("https://bank.invalid/accounts/existing").mock(
        return_value=httpx.Response(200, json={"_id": "existing", "balance": 100}))
    assert await bank.list_transfers("existing") == []
    assert existence.call_count == 1
    respx.get("https://bank.invalid/accounts/unknown/transfers").mock(
        return_value=httpx.Response(404, json="No transfers found for this account"))
    respx.get("https://bank.invalid/accounts/unknown").mock(
        return_value=httpx.Response(404, json="Account not found"))
    with pytest.raises(BankError, match="HTTP 404"):
        await bank.list_transfers("unknown")


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("body", ["Account not found", "No transfers found", {"message": "No transfers found for this account"}])
async def test_other_transfer_404s_remain_failures(body):
    bank = LiveBankAdapter(key=SecretStr("synthetic-secret"), base_url="https://bank.invalid")
    respx.get("https://bank.invalid/accounts/account/transfers").mock(
        return_value=httpx.Response(404, json=body))
    with pytest.raises(BankError, match="HTTP 404"):
        await bank.list_transfers("account")
