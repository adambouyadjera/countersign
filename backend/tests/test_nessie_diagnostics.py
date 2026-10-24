"""HTTP transcripts preserve request evidence without exposing credentials."""

import json

import httpx
from pydantic import SecretStr
import pytest

from app.adapters.bank import BankError, LiveBankAdapter
from app.adapters.nessie_diagnostics import NessieTranscriptTransport


@pytest.mark.asyncio
async def test_transcript_masks_request_and_echoed_error_credentials(tmp_path, capsys):
    secret = "synthetic-transcript-secret"
    report = tmp_path / "diagnostics" / "requests.jsonl"

    def respond(request):
        assert request.url.params["key"] == secret
        return httpx.Response(400, json={
            "error": f"Rejected request {request.url}",
            "echo": json.loads(request.content),
        })

    transport = NessieTranscriptTransport(
        secret, report, transport=httpx.MockTransport(respond),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(
            f"https://nessie.invalid/accounts/payer-id/transfers?key={secret}",
            headers={
                "Authorization": "Bearer separate-auth-secret",
                "Cookie": "session=separate-cookie-secret",
                "X-Api-Key": "separate-header-secret",
                "X-Diagnostic": "keep-this-header",
            },
            json={"amount": 1, "description": f"echo {secret}", "status": "pending"},
        )
    assert response.status_code == 400
    output = capsys.readouterr().out
    saved = report.read_text()
    assert output == saved
    for credential in (secret, "separate-auth-secret", "separate-cookie-secret", "separate-header-secret"):
        assert credential not in output
        assert credential not in saved
    request, returned = map(json.loads, saved.splitlines())
    recorded = request["request"]
    url = httpx.URL(recorded["url"])
    assert recorded["method"] == "POST"
    assert url.host == "nessie.invalid"
    assert url.path == "/accounts/payer-id/transfers"
    assert url.params["key"] == "[REDACTED]"
    assert recorded["headers"]["content-type"] == "application/json"
    for name in ("authorization", "cookie", "x-api-key"):
        assert recorded["headers"][name] == "[REDACTED]"
    assert recorded["headers"]["x-diagnostic"] == "keep-this-header"
    assert recorded["json"] == {
        "amount": 1, "description": "echo [REDACTED]", "status": "pending",
    }
    assert returned["response"]["status"] == 400
    assert returned["response"]["body"]["echo"] == recorded["json"]
    assert "[REDACTED]" in returned["response"]["body"]["error"]


@pytest.mark.asyncio
async def test_transport_failure_records_safe_error_and_bank_hides_url(tmp_path, capsys):
    secret = "synthetic-failure-secret"
    report = tmp_path / "failure.jsonl"

    def fail(request):
        raise httpx.ConnectError(f"Could not connect to {request.url}", request=request)

    transport = NessieTranscriptTransport(
        secret, report, transport=httpx.MockTransport(fail),
    )
    bank = LiveBankAdapter(
        key=SecretStr(secret), base_url="https://nessie.invalid", transport=transport,
    )
    with pytest.raises(BankError, match="Nessie request failed") as caught:
        await bank.transfer("company", "vendor", 100, "Diagnostic failure")
    output = capsys.readouterr().out
    assert secret not in output + report.read_text() + str(caught.value)
    records = [json.loads(line) for line in report.read_text().splitlines()]
    assert records[1] == {"response": {"error": "HTTP transport failed"}}
    assert "nessie.invalid" not in json.dumps(records[1])
    assert "https://" not in str(caught.value)
