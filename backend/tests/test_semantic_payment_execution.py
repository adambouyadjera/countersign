"""Payment reservations enforce semantic duplicates before any bank submission."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.adapters.bank import OfflineBankAdapter
from app.events import EventBus
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.payments import DuplicatePaymentError, PaymentExecutor
from app.pipeline.spans import to_payload
from app.security.duplicate_identity import verified_invoice_references
from app.store import Store
from app.world.invoices import InvoiceSpec, LineItem, extract_document, generate_invoice
from app.world.seed import seed_world


@pytest_asyncio.fixture
async def world(tmp_path):
    store = Store(tmp_path / "semantic-payments.db")
    store.initialize()
    bank = OfflineBankAdapter(store=store)
    await seed_world(store, bank)
    bank.transfer = AsyncMock(wraps=bank.transfer)
    try:
        yield store, bank, PaymentExecutor(store, EventBus(store), bank)
    finally:
        store.close()


def save_invoice(store, suffix, *, invoice_date="2026-01-15", po_number="PO-001"):
    """Use real PDF text and the explicit offline extractor, never synthetic spans."""
    run_id, invoice_id, number = f"run-{suffix}", f"invoice-{suffix}", f"BILL-{suffix}"
    spec = InvoiceSpec(invoice_number=number, vendor_name="Harbor Light Rentals",
        vendor_email="billing@harborlight.example", remit_account_number="100100001111",
        po_number=po_number, issued_date=date.fromisoformat(invoice_date),
        due_date=date(2026, 11, 7), line_items=(LineItem("Stage rigging", 1, 125000),))
    document = extract_document(generate_invoice(spec, invariant=True), f"document-{suffix}", invoice_id)
    store.save_record("documents", document.document_id, {
        "document_id": document.document_id, "invoice_id": invoice_id,
        "text": document.model_dump(mode="json"),
    })
    extraction = to_payload(document, parse_offline(document), "offline-semantic-fixture")
    identity = f"{run_id}:{invoice_id}"
    store.save_record("extractions", identity, {
        "extraction_id": identity, "run_id": run_id, **extraction.model_dump(mode="json"),
    })
    return dict(run_id=run_id, invoice_id=invoice_id, invoice_number=number,
                vendor_id="VEN-001", amount_cents=125000,
                to_display_number="100100001111", via="policy")


@pytest.mark.asyncio
async def test_new_number_cannot_pay_same_vendor_amount_and_invoice_date_twice(world):
    store, bank, executor = world
    first = save_invoice(store, "original", po_number="PO-001")
    second = save_invoice(store, "new-number", po_number="PO-OTHER")
    payment = await executor.execute(**first)
    with pytest.raises(DuplicatePaymentError):
        await executor.execute(**second)
    assert bank.transfer.await_count == 1
    assert len(store.list_records("session_payment_claims")) == 1
    assert not store.list_events(second["run_id"])
    assert len(store.list_records("transfers")) == 1
    assert store.get_record("transfers", payment.transfer_id)["invoice_number"] == first["invoice_number"]


@pytest.mark.asyncio
async def test_claim_and_transfer_store_printed_references_instead_of_payment_timestamp(world):
    store, _, executor = world
    arguments = save_invoice(store, "references", invoice_date="2026-01-15", po_number="PO-001")
    payment = await executor.execute(**arguments)
    claim = store.list_records("session_payment_claims")[0]
    transfer = store.get_record("transfers", payment.transfer_id)
    for record in (claim, transfer):
        assert record["invoice_date"] == "2026-01-15"
        assert record["po_number"].casefold() == "po-001"
        assert record["vendor_id"] == "VEN-001"
        assert record["amount_cents"] == 125000
        assert record["run_id"] == arguments["run_id"]
        assert record["invoice_id"] == arguments["invoice_id"]
    assert transfer["ts"][:10] != transfer["invoice_date"]
    assert claim["state"] == "submitted"


@pytest.mark.asyncio
async def test_different_number_is_blocked_while_same_semantic_payment_is_in_flight(world):
    store, bank, executor = world
    original = save_invoice(store, "in-flight", po_number="PO-001")
    duplicate = save_invoice(store, "pending-copy", po_number="PO-OTHER")
    entered, release = asyncio.Event(), asyncio.Event()
    bank_transfer = bank.transfer

    async def pause_at_bank(**values):
        entered.set()
        await release.wait()
        return await bank_transfer(**values)

    bank.transfer = AsyncMock(side_effect=pause_at_bank)
    task = asyncio.create_task(executor.execute(**original))
    await asyncio.wait_for(entered.wait(), timeout=5)
    other_store = Store(store.path)
    other_store.initialize()
    try:
        claim = other_store.list_records("session_payment_claims")[0]
        assert claim["state"] == "reserved"
        assert claim["invoice_date"] == "2026-01-15"
        other_executor = PaymentExecutor(other_store, EventBus(other_store), bank)
        with pytest.raises(DuplicatePaymentError):
            await other_executor.execute(**duplicate)
        assert bank.transfer.await_count == 1
        assert len(other_store.list_records("session_payment_claims")) == 1
    finally:
        release.set()
        await task
        other_store.close()


@pytest.mark.asyncio
async def test_legacy_paid_transfer_recovers_references_from_its_bound_extraction(world):
    store, bank, executor = world
    first = save_invoice(store, "legacy", po_number="PO-001")
    duplicate = save_invoice(store, "legacy-copy", po_number="PO-OTHER")
    payment = await executor.execute(**first)
    transfer = store.get_record("transfers", payment.transfer_id)
    transfer.pop("invoice_date")
    transfer.pop("po_number")
    store.save_record("transfers", payment.transfer_id, transfer)
    with store.transaction() as connection:
        connection.execute("DELETE FROM session_payment_claims")
    with pytest.raises(DuplicatePaymentError):
        await executor.execute(**duplicate)
    assert bank.transfer.await_count == 1
    assert store.list_records("session_payment_claims") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unverified", "foreign_document", "foreign_extraction", "missing_fields", "mismatched_value"])
async def test_reference_helper_rejects_unverified_or_foreign_extraction_claims(world, failure):
    store, _, _ = world
    arguments = save_invoice(store, "source-check")
    run_id, invoice_id = arguments["run_id"], arguments["invoice_id"]
    assert verified_invoice_references(store, run_id, invoice_id) == {
        "invoice_date": "2026-01-15", "po_number": "PO-001"}
    key = f"{run_id}:{invoice_id}"
    extraction = store.get_record("extractions", key)
    if failure == "foreign_extraction":
        extraction["run_id"] = "different-run"
    elif failure == "missing_fields":
        extraction["fields"] = [field for field in extraction["fields"]
                                if field["name"] not in {"invoice_date", "po_number"}]
    else:
        if failure == "foreign_document":
            foreign = store.get_record("documents", "document-source-check")
            foreign.update(document_id="foreign-document", invoice_id="different-invoice")
            foreign["text"].update(document_id="foreign-document", invoice_id="different-invoice")
            store.save_record("documents", "foreign-document", foreign)
        for field in extraction["fields"]:
            if field["name"] in {"invoice_date", "po_number"}:
                if failure == "unverified":
                    field["span"]["span_verified"] = False
                elif failure == "mismatched_value":
                    field["value"] = "2026-01-16" if field["name"] == "invoice_date" else "PO-OTHER"
                else:
                    field["span"]["document_id"] = "foreign-document"
    store.save_record("extractions", key, extraction)
    assert verified_invoice_references(store, run_id, invoice_id) == {}
