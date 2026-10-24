"""A dry-run bank cannot spend money, query Nessie, or fabricate transfer IDs."""

from copy import deepcopy

import pytest

from app.adapters.dry_run_bank import DryRunBank, DryRunViolation


class PrivateStore:
    def __init__(self):
        self.records = {}

    def get_record(self, table, key):
        return deepcopy(self.records.get((table, key)))

    def save_record(self, table, key, record):
        self.records[(table, key)] = deepcopy(record)


def values(**extra):
    return {"run_id": "eval-run", "invoice_id": "invoice-1", "invoice_number": "EVAL-001",
            "vendor_id": "VEN-001", "amount_cents": 125000, "to_display_number": "100100001111", **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    "health_check", "get_balance", "get_balances", "list_transfers", "account_activity",
    "list_customers", "create_customer", "list_accounts", "create_account", "transfer",
])
async def test_every_bank_capability_is_denied_without_a_transport(operation):
    bank = DryRunBank()
    assert bank.mode == "dry_run"
    with pytest.raises(DryRunViolation, match="no bank request was made"):
        await getattr(bank, operation)("untrusted input", amount_cents=125000)
    assert bank.denied_calls == [operation]
    assert bank.intents == []
    assert not hasattr(bank, "_transport")
    assert not hasattr(bank, "_key")


def test_records_only_an_internal_would_pay_intent_with_no_financial_result():
    store = PrivateStore()
    bank = DryRunBank(store)
    record = bank.record_intent(**values())
    assert record["intent_id"].startswith("dry-run-intent-")
    assert record["execution"] == record["mode"] == "dry_run"
    assert record["submitted"] is False
    assert "transfer_id" not in record and "transfer_status" not in record
    assert record["amount_cents"] == 125000
    assert store.records == {("dry_run_intents", record["intent_id"]): record}
    assert bank.denied_calls == []


def test_repeated_intent_reuses_record_and_original_timestamp_including_after_restart():
    store = PrivateStore()
    first = DryRunBank(store).record_intent(**values())
    bank = DryRunBank(store)
    second = bank.record_intent(**values())
    assert second == first
    assert bank.record_intent(**values()) == first
    assert len(bank.intents) == len(store.records) == 1
    second["amount_cents"] = 1
    bank.intents[0]["amount_cents"] = 2
    assert bank.intents[0]["amount_cents"] == 125000


@pytest.mark.parametrize("extra", [
    {"run_id": ""}, {"invoice_id": ""}, {"amount_cents": 0}, {"amount_cents": True},
    {"amount_cents": 1.5}, {"lane": "system"}, {"metadata": float("nan")},
    {"metadata": object()}, {"transfer_id": "fake-success"}, {"submitted": True},
    {"execution": "live"}, {"intent_id": "claimed-bank-id"},
])
def test_invalid_or_fabricated_result_fields_are_rejected(extra):
    bank = DryRunBank()
    with pytest.raises(DryRunViolation):
        bank.record_intent(**values(**extra))
    assert bank.intents == []


def test_distinct_real_decision_inputs_remain_separate_audit_actions():
    bank = DryRunBank()
    first = bank.record_intent(**values())
    second = bank.record_intent(**values(amount_cents=7500, memo="Separate requested fee"))
    assert first["intent_id"] != second["intent_id"]
    assert len(bank.intents) == 2
