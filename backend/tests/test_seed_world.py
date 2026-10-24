"""Trusted-world invariants and resumable seeding against mocked Nessie HTTP."""

from __future__ import annotations

from collections import Counter
import json

import httpx
import pytest
import respx

from app.adapters.factory import build_adapters
from app.adapters.bank import BankError
from app.config import Settings
from app.store import Store
from app.world.seed import (
    ATTACKER_DISPLAY_NUMBER, ATTACKER_NAME, COMPANY_FUNDING_CENTS,
    seed_trusted_world, seed_world,
)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "world.db")
    value.initialize()
    yield value
    value.close()


def bank_settings(**overrides):
    return Settings(**{
        "_env_file": None,
        "nessie_base_url": "https://nessie.invalid",
        "nessie_api_key": "synthetic-seed-key",
        "nessie_mode": "live",
        "elastic_api_key": "", "anthropic_api_key": "",
        "gemini_api_key": "", "bitdeer_api_key": "",
        **overrides,
    })


class MockNessie:
    def __init__(self):
        self.customers = []
        self.accounts = []
        self.calls = Counter()
        self.lose_next_customer_response = False
        self.lose_next_account_response = False

    def __call__(self, request):
        assert request.url.params["key"] == "synthetic-seed-key"
        path = request.url.path
        method = request.method
        self.calls[method, path] += 1
        payload = json.loads(request.content) if request.content else None
        if path == "/customers":
            if method == "GET":
                return httpx.Response(200, json=self.customers)
            if method == "POST":
                created = {"_id": f"customer-{len(self.customers) + 1}", **payload}
                self.customers.append(created)
                if self.lose_next_customer_response:
                    self.lose_next_customer_response = False
                    raise httpx.ReadTimeout("Synthetic lost customer response", request=request)
                return httpx.Response(201, json={"code": 201, "objectCreated": created})
        if path.startswith("/customers/") and path.endswith("/accounts"):
            customer_id = path.split("/")[2]
            if method == "GET":
                return httpx.Response(200, json=[x for x in self.accounts if x["customer_id"] == customer_id])
            if method == "POST":
                created = {"_id": f"account-{len(self.accounts) + 1}", "customer_id": customer_id, **payload}
                self.accounts.append(created)
                if self.lose_next_account_response:
                    self.lose_next_account_response = False
                    raise httpx.ReadTimeout("Synthetic lost account response", request=request)
                return httpx.Response(201, json={"code": 201, "objectCreated": created})
        pytest.fail(f"Unexpected mock operation {method} {path}")


def test_trusted_world_has_company_five_vendors_and_open_pos_and_is_preserved(store):
    world = seed_trusted_world(store)
    assert world["company"]["name"] == "Fogline Festival"
    assert world["company"]["duration_days"] == 3
    assert world["company"]["city"] == "San Francisco"
    assert len(world["vendors"]) == len(world["purchase_orders"]) == 5
    services = {v["service_description"] for v in world["vendors"]}
    assert services == {"Stage rigging", "Security staffing", "Sanitation", "Sound production", "Artist agency"}
    for vendor, po in zip(world["vendors"], world["purchase_orders"]):
        assert vendor["trust"] == po["trust"] == "trusted"
        assert vendor["email"].endswith("@" + vendor["email_domain"])
        assert vendor["remit_account_number"] != ATTACKER_DISPLAY_NUMBER
        assert vendor["purchase_order_ids"] == [po["purchase_order_id"]]
        assert po["vendor_id"] == vendor["vendor_id"]
        assert po["status"] == "open"
        assert po["amount_cents"] == sum(x["quantity"] * x["unit_price_cents"] for x in po["line_items"])
    first = world["vendors"][0]
    first["remit_account_number"] = "verified-new-account"
    store.save_record("vendors", first["vendor_id"], first)
    assert seed_trusted_world(store)["vendors"][0]["remit_account_number"] == "verified-new-account"


@pytest.mark.asyncio
async def test_nessie_seed_reuses_ids_on_rerun_and_after_database_reopen(store):
    nessie = MockNessie()
    bank = build_adapters(bank_settings(), store).bank
    with respx.mock(assert_all_called=False) as router:
        router.route(host="nessie.invalid").mock(side_effect=nessie)
        first = await seed_world(store, bank)
        first_calls = nessie.calls.copy()
        assert len(first["accounts"]) == 7
        assert len(nessie.customers) == len(nessie.accounts) == 7
        assert sum(x["balance"] for x in nessie.accounts) == COMPANY_FUNDING_CENTS / 100
        assert {x["type"] for x in nessie.accounts} == {"Checking"}
        assert all(x["rewards"] == 0 for x in nessie.accounts)
        assert all(x["mode"] == "live" for x in first["accounts"])
        attacker = next(x for x in first["accounts"] if x["role"] == "attacker")
        assert attacker["label"] == ATTACKER_NAME
        assert attacker["display_number"] == ATTACKER_DISPLAY_NUMBER
        assert store.get_record("bank_directory", ATTACKER_DISPLAY_NUMBER)["account_id"] == attacker["account_id"]
        assert len(store.list_records("bank_seeds")) == 7

        # Persistent IDs avoid even discovery reads on an ordinary rerun.
        second = await seed_world(store, bank)
        assert second == first
        assert nessie.calls == first_calls
        store.close()
        store.initialize()
        assert await seed_world(store, build_adapters(bank_settings(), store).bank) == first
        assert nessie.calls == first_calls


@pytest.mark.asyncio
async def test_nessie_seed_recovers_an_account_created_before_lost_response(store):
    nessie = MockNessie()
    nessie.lose_next_account_response = True
    bank = build_adapters(bank_settings(), store).bank
    with respx.mock(assert_all_called=False) as router:
        router.route(host="nessie.invalid").mock(side_effect=nessie)
        with pytest.raises(BankError, match="timed out"):
            await seed_world(store, bank)
        assert len(nessie.customers) == len(nessie.accounts) == 1
        partial = store.get_record("bank_seeds", "live:fogline-festival")
        assert partial["customer_id"] == "customer-1"
        assert "account_id" not in partial

        result = await seed_world(store, bank)
        assert len(result["accounts"]) == 7
        assert len(nessie.customers) == len(nessie.accounts) == 7
        assert nessie.calls["POST", "/customers/customer-1/accounts"] == 1


@pytest.mark.asyncio
async def test_nessie_seed_recovers_a_customer_created_before_lost_response(store):
    nessie = MockNessie()
    nessie.lose_next_customer_response = True
    bank = build_adapters(bank_settings(), store).bank
    with respx.mock(assert_all_called=False) as router:
        router.route(host="nessie.invalid").mock(side_effect=nessie)
        with pytest.raises(BankError, match="timed out"):
            await seed_world(store, bank)
        assert len(nessie.customers) == 1
        assert len(nessie.accounts) == 0
        assert store.get_record("bank_seeds", "live:fogline-festival") is None
        result = await seed_world(store, bank)
        assert len(result["accounts"]) == 7
        assert len(nessie.customers) == len(nessie.accounts) == 7
        assert nessie.calls["POST", "/customers"] == 7


@pytest.mark.asyncio
async def test_nessie_seed_recovers_existing_named_world_without_local_ids(store):
    nessie = MockNessie()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="nessie.invalid").mock(side_effect=nessie)
        first = await seed_world(store, build_adapters(bank_settings(), store).bank)
        fresh_store = Store(":memory:")
        fresh_store.initialize()
        try:
            second = await seed_world(fresh_store, build_adapters(bank_settings(), fresh_store).bank)
            assert second == first
            assert len(nessie.customers) == len(nessie.accounts) == 7
        finally:
            fresh_store.close()


@pytest.mark.asyncio
async def test_seed_keeps_offline_and_live_ids_separate(store):
    offline_bank = build_adapters(bank_settings(nessie_mode="offline"), store).bank
    offline = await seed_world(store, offline_bank)
    assert offline["mode"] == "offline"
    assert all(x["mode"] == "offline" for x in offline["accounts"])
    nessie = MockNessie()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="nessie.invalid").mock(side_effect=nessie)
        live = await seed_world(store, build_adapters(bank_settings(), store).bank)
        assert len(nessie.customers) == len(nessie.accounts) == 7
        assert len(store.list_records("bank_seeds")) == 14
        assert {x["account_id"] for x in offline["accounts"]}.isdisjoint(x["account_id"] for x in live["accounts"])
        assert {x["mode"] for x in store.list_records("bank_directory")} == {"live"}
        assert await seed_world(store, offline_bank) == offline
        assert {x["mode"] for x in store.list_records("bank_directory")} == {"offline"}
