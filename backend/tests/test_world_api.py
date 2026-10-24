"""Exercise contract serialization, source persistence, and the offline demo world."""

from io import BytesIO
import json

from fastapi.testclient import TestClient
import httpx
import pdfplumber
import pytest

from app.config import Settings
from app.contract_models import Batch, Balances, DocumentText, ErrorResponse
from app.main import create_app


@pytest.fixture
def application(tmp_path):
    settings = Settings(
        _env_file=None, nessie_mode="offline", elastic_mode="offline",
        nessie_api_key="", elastic_api_key="", anthropic_api_key="",
        gemini_api_key="", bitdeer_api_key="",
    )
    return create_app(settings, db_path=tmp_path / "world.db")


def test_standard_batch_sources_roundtrip_and_private_scoring(application):
    with TestClient(application) as client:
        response = client.post("/api/batches", json={"kind": "standard"})
        assert response.status_code == 200, response.text
        batch = Batch.model_validate(response.json())
        assert [invoice.invoice_number for invoice in batch.invoices] == [
            f"INV-{i:03d}" for i in range(1, 6)
        ]
        assert len({invoice.vendor_id for invoice in batch.invoices}) == 5
        assert client.get(f"/api/batches/{batch.batch_id}").json() == response.json()
        assert set(response.json()) == {"batch_id", "kind", "created_at", "invoices"}
        scoring = application.state.store.list_records("invoice_scoring")
        poisoned = [record for record in scoring if record["poisoned"]]
        assert len(poisoned) == 1
        assert poisoned[0]["invoice_id"] == batch.invoices[2].invoice_id

        for index, invoice in enumerate(batch.invoices, start=1):
            document_response = client.get(f"/api/documents/{invoice.document_id}")
            assert document_response.status_code == 200
            document = DocumentText.model_validate(document_response.json())
            assert document.invoice_id == invoice.invoice_id
            assert len(document.pages) == 1
            assert [line.line for line in document.pages[0].lines] == list(
                range(1, len(document.pages[0].lines) + 1)
            )
            text = "\n".join(line.text for line in document.pages[0].lines)
            assert invoice.invoice_number in text
            assert invoice.vendor_name in text
            assert ("CS-CANARY-" in text) == (index == 3)
            assert "poisoned" not in document_response.text
            assert "hidden_white_text" not in document_response.text
            pdf_response = client.get(f"/api/documents/{invoice.document_id}/pdf")
            assert pdf_response.status_code == 200
            assert pdf_response.headers["content-type"] == "application/pdf"
            assert pdf_response.content.startswith(b"%PDF-")
            with pdfplumber.open(BytesIO(pdf_response.content)) as pdf:
                assert len(pdf.pages) == 1
                assert invoice.invoice_number in pdf.pages[0].extract_text()
                assert "poisoned" not in json.dumps(pdf.metadata).lower()
            assert set(document_response.json()) == {"document_id", "invoice_id", "pages"}


def test_later_batch_does_not_replace_earlier_sources_and_restart_keeps_pdfs(application):
    with TestClient(application) as client:
        first = client.post("/api/batches", json={"kind": "standard"}).json()
        document_id = first["invoices"][2]["document_id"]
        original_pdf = client.get(f"/api/documents/{document_id}/pdf").content
        original_text = client.get(f"/api/documents/{document_id}").json()
        second = client.post("/api/batches", json={"kind": "standard"}).json()
        assert first["batch_id"] != second["batch_id"]
        assert {i["document_id"] for i in first["invoices"]}.isdisjoint(
            {i["document_id"] for i in second["invoices"]}
        )
    with TestClient(application) as client:
        assert client.get(f"/api/documents/{document_id}/pdf").content == original_pdf
        assert client.get(f"/api/documents/{document_id}").json() == original_text
        assert client.get(f"/api/batches/{first['batch_id']}").json() == first


def test_world_endpoints_use_contract_errors(application):
    with TestClient(application) as client:
        for path in ["/api/batches/missing", "/api/documents/missing", "/api/documents/missing/pdf"]:
            response = client.get(path)
            assert response.status_code == 404
            assert ErrorResponse.model_validate(response.json()).error.code == "not_found"
        for body in [{}, {"kind": "attack"}, {"kind": "standard", "poisoned": True}]:
            response = client.post("/api/batches", json=body)
            assert response.status_code == 422
            assert ErrorResponse.model_validate(response.json()).error.code == "validation_error"


@pytest.mark.asyncio
async def test_balances_track_real_ledger_ids_and_reset_only_session_receipts(application):
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test",
        ) as client:
            response = await client.get("/api/balances")
            assert response.status_code == 200, response.text
            before = Balances.model_validate(response.json())
            assert before.bank_mode == "offline"
            assert len(before.accounts) == 7
            assert sum(a.role == "company" for a in before.accounts) == 1
            assert sum(a.role == "vendor" for a in before.accounts) == 5
            assert sum(a.role == "attacker" for a in before.accounts) == 1
            company = next(a for a in before.accounts if a.role == "company")
            attacker = next(a for a in before.accounts if a.role == "attacker")
            assert attacker.label == "Northwind Remittance Services"
            payment = await application.state.adapters.bank.transfer(
                from_account_id=company.account_id,
                to_account_id=attacker.account_id,
                amount_cents=100, description="receipt test",
            )
            after = Balances.model_validate((await client.get("/api/balances")).json())
            received = next(a for a in after.accounts if a.role == "attacker")
            assert received.balance_cents == attacker.balance_cents + 100
            assert received.received_this_session_cents == 100
            assert [t.transfer_id for t in received.transfers_this_session] == [payment.transfer_id]
            assert received.transfers_this_session[0].from_label == company.label
            assert (await client.post("/api/demo/reset")).status_code == 200
            reset = Balances.model_validate((await client.get("/api/balances")).json())
            received = next(a for a in reset.accounts if a.role == "attacker")
            assert received.balance_cents == attacker.balance_cents + 100
            assert received.received_this_session_cents == 0
            assert received.transfers_this_session == []
            assert reset.session_started_at > before.session_started_at


def test_live_unseeded_balances_fail_explicitly_without_network(tmp_path):
    settings = Settings(
        _env_file=None, nessie_mode="live", nessie_api_key="", elastic_mode="offline",
    )
    with TestClient(create_app(settings, db_path=tmp_path / "unseeded.db")) as client:
        response = client.get("/api/balances")
        assert response.status_code == 503
        assert ErrorResponse.model_validate(response.json()).error.code == "service_unavailable"


def test_partial_seed_cannot_report_incomplete_balances(application):
    from app.contract_models import AccountBalance

    async def partial_balances(started):
        return Balances(
            session_started_at=started, bank_mode="offline", accounts=[AccountBalance(
                role="company", label="Fogline Festival", account_id="offline-partial",
                display_number="700700007777", balance_cents=25000000,
                received_this_session_cents=0, transfers_this_session=[],
            )],
        )

    with TestClient(application) as client:
        application.state.adapters.bank.get_balances = partial_balances
        response = client.get("/api/balances")
        assert response.status_code == 503
        assert ErrorResponse.model_validate(response.json()).error.code == "service_unavailable"
