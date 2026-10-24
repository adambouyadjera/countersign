"""Explicit live smoke test: creates/reuses demo world and submits one dollar.

No response, status or balance movement is fabricated. Pending is reported as
pending; the test verifies readback rather than promising immediate settlement.
"""

import pytest

from app.adapters import build_adapters
from app.config import Settings
from app.store import Store


@pytest.mark.live
@pytest.mark.asyncio
async def test_seed_and_one_dollar_transfer_readback():
    from app.world.seed import seed_world

    settings = Settings()
    if not settings.nessie_api_key.get_secret_value():
        pytest.skip("NESSIE_API_KEY is not configured")
    settings = settings.model_copy(update={"nessie_mode": "live"})
    store = Store()
    store.initialize()
    try:
        bank = build_adapters(settings, store).bank
        result = await seed_world(store, bank)
        company = next(a for a in result["accounts"] if a["role"] == "company")
        vendor = next(a for a in result["accounts"] if a["role"] == "vendor")
        before_source = await bank.get_balance(company["account_id"])
        before_target = await bank.get_balance(vendor["account_id"])
        payment = await bank.transfer(company["account_id"], vendor["account_id"], 100, "Countersign B2 live $1.00 smoke test")
        transfers = await bank.list_transfers(company["account_id"])
        record = next(t for t in transfers if t.transfer_id == payment.transfer_id)
        assert record.amount_cents == 100 and record.to_account_id == vendor["account_id"]
        assert record.mode == payment.bank_mode == "live"
        assert record.status and payment.transfer_status
        after_source = await bank.get_balance(company["account_id"])
        after_target = await bank.get_balance(vendor["account_id"])
        print({"transfer_id": payment.transfer_id, "submitted_status": payment.transfer_status,
               "readback_status": record.status, "source_before_cents": before_source.balance_cents,
               "source_after_cents": after_source.balance_cents, "target_before_cents": before_target.balance_cents,
               "target_after_cents": after_target.balance_cents})
    finally:
        store.close()
