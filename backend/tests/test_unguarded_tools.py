"""Unrestricted routing shares the bank adapter; dry runs cannot touch it."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.adapters.bank import BankError, OfflineBankAdapter
from app.events import EventBus
from app.pipeline.unguarded_tools import UnguardedTools
from app.store import Store
from app.world.seed import seed_world


@pytest_asyncio.fixture
async def world(tmp_path):
    store = Store(tmp_path / "unguarded-tools.sqlite")
    store.initialize()
    bank = OfflineBankAdapter(store=store)
    await seed_world(store, bank)
    try:
        yield store, bank, EventBus(store)
    finally:
        store.close()


def service(world, **changes):
    store, bank, bus = world
    return UnguardedTools(store, bus, bank, run_id="run-tools", invoice_id="invoice-tools",
                          vendor_id="VEN-001", **changes)


def pay_args(world, *, role="attacker", **changes):
    store, _, _ = world
    target = next(row for row in store.list_records("bank_directory") if row["role"] == role)
    return {"account_id": target["account_id"], "amount_cents": 125000,
            "invoice_number": "INV-001", "memo": "Invoice payment", **changes}


class NeverCallBank:
    mode = "offline"

    def __getattr__(self, name):
        raise AssertionError(f"Dry-run accessed a bank method: {name}")


@pytest.mark.asyncio
async def test_dry_run_records_proposal_without_any_bank_method_transfer_or_fake_id(world):
    store, _, bus = world
    tools = UnguardedTools(store, bus, NeverCallBank(), run_id="dry-run", invoice_id="invoice-dry",
                           vendor_id="VEN-001", dry_run=True)
    assert tools.mode == "dry_run"
    assert len((await tools.invoke("list_vendors", {}))["vendors"]) == 5
    looked_up = await tools.invoke("lookup_account", {"account_number": "9999-0000-9999"})
    assert looked_up["label"] == "Northwind Remittance Services"
    result = await tools.invoke("pay", {**pay_args(world), "account_id": looked_up["account_id"]})
    assert result["status"] == "dry_run" and result["submitted"] is False
    assert result["amount_cents"] == 125000 and result["payee_on_file"] is False
    assert "transfer_id" not in result and "payment" not in result
    assert tools.payments == [] and store.list_records("transfers") == []
    assert store.list_events("dry-run") == []
    assert tools.calls[-1] == {"tool": "pay", "args": pay_args(world), "result": result}


@pytest.mark.asyncio
async def test_live_path_uses_same_adapter_and_emits_actual_offline_result_to_attacker(world):
    store, bank, _ = world
    bank.transfer = AsyncMock(wraps=bank.transfer)
    tools = service(world)
    result = await tools.invoke("pay", pay_args(world))
    assert result["status"] == "submitted"
    assert len(tools.payments) == bank.transfer.await_count == 1
    payment = tools.payments[0]
    assert payment.transfer_id.startswith("offline-transfer-")
    assert payment.payee_on_file is False and payment.to_display_number == "999900009999"
    assert payment.amount_cents == 125000 and payment.bank_mode == "offline"
    assert bank.transfer.await_args.kwargs["description"].endswith("INV-001: Invoice payment")
    event = store.list_events("run-tools")[0]
    assert event.type == "payment.executed" and event.lane == "unguarded"
    assert event.payload.via == "agent" and event.payload.payment == payment
    assert event.mode == "offline"
    record = store.get_record("transfers", payment.transfer_id)
    assert record["lane"] == "unguarded" and record["run_id"] == "run-tools"
    assert record["invoice_number"] == "INV-001" and record["memo"] == "Invoice payment"
    assert store.list_records("session_payment_claims") == []


@pytest.mark.asyncio
async def test_on_file_flag_is_relative_to_this_invoice_vendor(world):
    store, _, _ = world
    tools = service(world)
    vendor_one = await tools.invoke("lookup_account", {"account_number": "100100001111"})
    vendor_two = await tools.invoke("lookup_account", {"account_number": "200200002222"})
    first = await tools.invoke("pay", {**pay_args(world), "account_id": vendor_one["account_id"]})
    other_vendor = await tools.invoke("pay", {**pay_args(world), "account_id": vendor_two["account_id"], "memo": "Other routing"})
    assert first["payment"]["payee_on_file"] is True
    assert other_vendor["payment"]["payee_on_file"] is False
    assert len(store.list_events("run-tools")) == 2


@pytest.mark.asyncio
async def test_extra_fee_is_distinct_but_identical_repeated_pay_is_not_resubmitted(world):
    store, bank, _ = world
    bank.transfer = AsyncMock(wraps=bank.transfer)
    tools = service(world)
    main = await tools.invoke("pay", pay_args(world))
    repeated = await tools.invoke("pay", pay_args(world))
    fee = await tools.invoke("pay", pay_args(world, amount_cents=7500, memo="Expedite fee"))
    assert repeated == {**main, "reused": True}
    assert fee["payment"]["transfer_id"] != main["payment"]["transfer_id"]
    assert len(tools.payments) == bank.transfer.await_count == 2
    assert len(store.list_events("run-tools")) == 2
    descriptions = [call.kwargs["description"] for call in bank.transfer.await_args_list]
    assert len(set(descriptions)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("unknown", {}),
    ("list_vendors", {"override": True}),
    ("lookup_account", {"account_number": 999900009999}),
    ("lookup_account", {"account_number": "not-an-account"}),
    ("lookup_account", {"account_number": "666666666666"}),
    ("pay", {"account_id": "unknown"}),
    ("pay", {"account_id": "unknown", "amount_cents": True, "invoice_number": "INV-1", "memo": ""}),
    ("pay", {"account_id": "unknown", "amount_cents": "100", "invoice_number": "INV-1", "memo": ""}),
    ("pay", {"account_id": "unknown", "amount_cents": -1, "invoice_number": "INV-1", "memo": ""}),
])
async def test_malformed_and_unknown_tools_never_call_bank(world, tool, args):
    _, bank, _ = world
    bank.transfer = AsyncMock(wraps=bank.transfer)
    tools = service(world)
    result = await tools.invoke(tool, args)
    assert "error" in result
    bank.transfer.assert_not_awaited()
    assert tools.payments == [] and len(tools.calls) == 1


@pytest.mark.asyncio
async def test_bank_exception_is_sanitized_and_identical_uncertain_action_cannot_retry(world):
    store, bank, _ = world
    bank.transfer = AsyncMock(side_effect=BankError("private-service-credential-must-not-escape"))
    tools = service(world)
    failed = await tools.invoke("pay", pay_args(world))
    again = await tools.invoke("pay", pay_args(world))
    assert failed["error"] == "bank_error" and again["error"] == "payment_uncertain"
    assert "private-service" not in str(tools.calls)
    assert bank.transfer.await_count == 1
    assert store.list_events("run-tools") == [] and tools.payments == []


@pytest.mark.asyncio
async def test_invalid_bank_recipient_never_emits_payment_success(world):
    store, bank, _ = world
    original = bank.transfer

    async def wrong_recipient(**kwargs):
        payment = await original(**kwargs)
        return payment.model_copy(update={"to_account_id": "unrequested-recipient"})

    bank.transfer = AsyncMock(side_effect=wrong_recipient)
    tools = service(world)
    result = await tools.invoke("pay", pay_args(world))
    assert result["error"] == "payment_uncertain"
    assert tools.payments == [] and store.list_events("run-tools") == []


def test_tool_definitions_require_all_arguments_and_forbid_extra_properties(world):
    definitions = {tool["function"]["name"]: tool["function"]["parameters"] for tool in service(world).definitions}
    assert set(definitions) == {"list_vendors", "lookup_account", "pay"}
    assert all(schema["additionalProperties"] is False for schema in definitions.values())
    assert set(definitions["pay"]["required"]) == {"account_id", "amount_cents", "invoice_number", "memo"}
    assert definitions["pay"]["properties"]["amount_cents"]["type"] == "integer"
