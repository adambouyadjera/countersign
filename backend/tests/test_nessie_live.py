"""Explicit live smoke test: creates/reuses demo world and submits one dollar.

No response, status or balance movement is fabricated. Pending is reported as
pending; the test verifies readback rather than promising immediate settlement.
"""

import pytest

from app.adapters import build_adapters
from app.adapters.nessie_diagnostics import NessieTranscriptTransport
from app.config import BACKEND_DIR, Settings
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
    transport = NessieTranscriptTransport(
        settings.nessie_api_key.get_secret_value(),
        BACKEND_DIR / "docs" / "nessie_live_transcript.jsonl",
    )
    try:
        bank = build_adapters(settings, store, transport=transport).bank
        result = await seed_world(store, bank)
        company = next(a for a in result["accounts"] if a["role"] == "company")
        vendor = next(a for a in result["accounts"] if a["role"] == "vendor")
        before_source = await bank.get_balance(company["account_id"])
        before_target = await bank.get_balance(vendor["account_id"])
        payment = await bank.transfer(company["account_id"], vendor["account_id"], 100, "Countersign B3 live $1.00 paired smoke test")
        transfers = await bank.list_transfers(company["account_id"])
        record = next(t for t in transfers if t.transfer_id == payment.transfer_id)
        assert record.amount_cents == 100 and record.to_account_id == vendor["account_id"]
        assert record.mode == payment.bank_mode == "live"
        assert record.status and payment.transfer_status
        if settings.nessie_payment_method == "paired_withdrawal_deposit":
            assert payment.detail.method == "paired_withdrawal_deposit"
            assert payment.detail.atomic is False
            assert payment.transfer_id == payment.detail.withdrawal_id
            assert payment.detail.withdrawal_id != payment.detail.deposit_id
            withdrawal = await bank._request("GET", f"/withdrawal/{payment.detail.withdrawal_id}")
            deposit = await bank._request("GET", f"/deposits/{payment.detail.deposit_id}")
            assert withdrawal["_id"] == payment.detail.withdrawal_id
            assert deposit["_id"] == payment.detail.deposit_id
            assert withdrawal["amount"] == deposit["amount"] == 1
            assert withdrawal["status"] == payment.detail.withdrawal_status
            assert deposit["status"] == payment.detail.deposit_status
            company_withdrawals = await bank._request("GET", f"/accounts/{company['account_id']}/withdrawals")
            vendor_deposits = await bank._request("GET", f"/accounts/{vendor['account_id']}/deposits")
            assert any(item.get("_id", item.get("id")) == payment.detail.withdrawal_id for item in company_withdrawals)
            assert any(item.get("_id", item.get("id")) == payment.detail.deposit_id for item in vendor_deposits)
        after_source = await bank.get_balance(company["account_id"])
        after_target = await bank.get_balance(vendor["account_id"])
        print({"payment": payment.model_dump(mode="json"), "transfer_id": payment.transfer_id, "submitted_status": payment.transfer_status,
               "readback_status": record.status, "source_before_cents": before_source.balance_cents,
               "source_after_cents": after_source.balance_cents, "target_before_cents": before_target.balance_cents,
               "target_after_cents": after_target.balance_cents})
    finally:
        await transport.aclose()
        store.close()
