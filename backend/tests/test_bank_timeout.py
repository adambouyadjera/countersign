"""Bound live-bank HTTP time and keep credential URLs out of timeout messages."""

import httpx
from pydantic import SecretStr
import pytest

from app.adapters.bank import BankError, LiveBankAdapter


@pytest.mark.asyncio
async def test_nessie_timeout_is_explicit_fifteen_seconds_and_sanitized():
    def timeout(request):
        assert set(request.extensions["timeout"].values()) == {15}
        raise httpx.ReadTimeout("https://bank.invalid?key=synthetic-secret", request=request)

    bank = LiveBankAdapter(
        key=SecretStr("synthetic-secret"), base_url="https://bank.invalid",
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(BankError, match=r"timed out after 15 s \(nessie\)") as caught:
        await bank.get_balance("company")
    assert "synthetic-secret" not in str(caught.value)
    assert bank.mode == "live"
