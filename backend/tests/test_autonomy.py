"""Trusted role limits apply independently of valid invoice/PO/account checks."""

from dataclasses import replace
from datetime import date

import pytest

from app.security.labels import Labeled
from app.security.policy import PolicyEngine
from app.store import Store
from app.world.people import seed_people
from tests.test_policy import world, evaluate


def set_amount(world, cents):
    text = f"${cents // 100:,}.{cents % 100:02d}"
    record = world["store"].get_record("documents", "doc-1")
    record["text"]["pages"][0]["lines"][3]["text"] = text
    world["store"].save_record("documents", "doc-1", record)
    source = replace(next(iter(world["intent"].amount.sources)), char_end=len(text))
    amount = Labeled(cents, {source})
    world["fields"]["amount_due_cents"] = amount
    world["intent"] = replace(world["intent"], amount=amount)
    po = world["store"].get_record("purchase_orders", "PO-001")
    world["store"].save_record("purchase_orders", "PO-001", {**po, "amount_cents": cents})


@pytest.mark.parametrize("cents,outcome", [(2_500_000, "ALLOW"), (2_500_001, "HOLD")])
def test_default_agent_limit_is_25000_dollars_and_boundary_is_inclusive(world, cents, outcome):
    set_amount(world, cents)
    result = evaluate(world)
    assert result.outcome == outcome
    assert all(check.passed for check in result.checks)
    assert ("OVER_AUTONOMY_LIMIT" in result.reason_codes) == (outcome == "HOLD")
    if outcome == "HOLD":
        assert "autonomy limit" in next(check.message for check in result.checks if check.code == "AMOUNT_MATCHES_PO")


def test_configured_agent_limit_holds_an_otherwise_valid_invoice(world):
    world["engine"] = PolicyEngine(world["store"], autonomy_limit_cents=100000, today=date(2026, 10, 24))
    result = evaluate(world)
    assert result.outcome == "HOLD" and result.reason_codes == ["OVER_AUTONOMY_LIMIT"]
    assert all(check.passed for check in result.checks)


@pytest.mark.parametrize("limit", [-1, True, 1.5, None])
def test_agent_limit_rejects_non_integer_or_negative_config(world, limit):
    with pytest.raises(ValueError, match="Autonomy limit"):
        PolicyEngine(world["store"], autonomy_limit_cents=limit)


def test_people_defaults_and_explicit_agent_configuration_use_required_limits():
    store = Store(":memory:")
    store.initialize()
    try:
        seed_people(store)
        assert store.get_record("people", "person-ap-agent")["approval_limit_cents"] == 2500000
        assert store.get_record("people", "person-ap-manager")["approval_limit_cents"] == 5000000
        assert store.get_record("people", "person-controller")["approval_limit_cents"] is None
        assert store.get_record("people", "person-vendor-admin")["approval_limit_cents"] == 0
        seed_people(store, autonomy_limit_cents=12345)
        assert store.get_record("people", "person-ap-agent")["approval_limit_cents"] == 12345
    finally:
        store.close()


def test_role_seed_migrates_legacy_defaults_and_keeps_custom_manager_limits():
    store = Store(":memory:")
    store.initialize()
    try:
        seed_people(store)
        for person_id, limit in (("person-ap-agent", 150000), ("person-ap-manager", 200000)):
            person = store.get_record("people", person_id)
            store.save_record("people", person_id, {**person, "approval_limit_cents": limit})
        seed_people(store)
        assert store.get_record("people", "person-ap-agent")["approval_limit_cents"] == 2500000
        assert store.get_record("people", "person-ap-manager")["approval_limit_cents"] == 5000000
        manager = store.get_record("people", "person-ap-manager")
        store.save_record("people", manager["person_id"], {**manager, "approval_limit_cents": 4000000})
        seed_people(store)
        assert store.get_record("people", manager["person_id"])["approval_limit_cents"] == 4000000
    finally:
        store.close()
