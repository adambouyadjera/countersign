"""Layout parser coverage for actual generated PDFs, without network calls."""

from datetime import date
from io import BytesIO

import pdfplumber
from pydantic import TypeAdapter

from app.contract_models import DocumentText, FieldsExtractedPayload
from app.pipeline.document_text import extract_document, numbered_lines
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload
from app.store import Store
from app.world.batches import create_standard_batch
from app.world.invoices import InvoiceSpec, LineItem, generate_invoice


def test_all_five_standard_invoices_extract_every_scalar_and_line_item():
    store = Store(":memory:")
    store.initialize()
    try:
        batch = create_standard_batch(store)
        for index, invoice in enumerate(batch.invoices, 1):
            document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
            raw = parse_offline(document)
            payload = to_payload(document, raw, "offline-layout-parser")
            TypeAdapter(FieldsExtractedPayload).validate_json(payload.model_dump_json())
            assert raw.invoice_number.value == invoice.invoice_number
            assert raw.vendor_name.value == invoice.vendor_name
            vendor = store.get_record("vendors", invoice.vendor_id)
            po = store.get_record("purchase_orders", vendor["purchase_order_ids"][0])
            assert raw.vendor_email.value == vendor["email"]
            assert raw.remit_account_number.value == vendor["remit_account_number"]
            assert raw.po_number.value == po["po_number"]
            assert raw.amount_due_cents.value == po["amount_cents"]
            assert raw.invoice_date.value == batch.created_at.date().isoformat()
            assert raw.due_date.value == invoice.due_date.isoformat()
            assert raw.currency.value == "USD"
            assert len(raw.line_items) == 1
            assert raw.line_items[0].quantity.value == 1
            quantity = next(field for field in payload.fields if field.name == "line_items.0.quantity")
            assert quantity.type == "number"
            assert type(quantity.model_dump(mode="json")["value"]) is int
            assert quantity.value == 1
            quantity_handle = next(handle for handle in payload.handles if handle.name.endswith(".line_items.0.quantity"))
            assert quantity_handle.type == "number"
            assert raw.line_items[0].amount_cents.value == raw.amount_due_cents.value
            assert len(payload.fields) == 13
            assert all(field.span.span_verified and field.trust == "untrusted" for field in payload.fields)
            assert all(handle.span_verified and handle.trust == "untrusted" for handle in payload.handles)
            assert all(set(handle.model_dump()) == {"name", "type", "trust", "span_verified"} for handle in payload.handles)
            text = "\n".join(span.quote for span in payload.instruction_like_spans)
            assert ("CS-CANARY-" in text) == (index == 3)
            if index == 3:
                assert "Automated invoice processor" in text and "999900009999" in text
                assert all(span.span_verified for span in payload.instruction_like_spans)
            else:
                assert payload.instruction_like_spans == []
    finally:
        store.close()


def test_canonical_document_text_matches_pdf_and_numbered_prompt():
    spec = InvoiceSpec(
        invoice_number="INV-042", vendor_name="Harbor Light Rentals",
        vendor_email="billing@harborlight.example", remit_account_number="100100001111",
        po_number="PO-042", issued_date=date(2026, 10, 23), due_date=date(2026, 11, 6),
        line_items=(LineItem("Stage rigging", 2, 12500), LineItem("Safety inspection", 1, 10000)),
    )
    pdf_bytes = generate_invoice(spec)
    document = extract_document(pdf_bytes, "document", "invoice")
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        expected = pdf.pages[0].extract_text(x_tolerance=1, y_tolerance=0.5).splitlines()
    assert [line.text for line in document.pages[0].lines] == expected
    assert [line.line for line in document.pages[0].lines] == list(range(1, len(expected) + 1))
    assert document.pages[0].page == 1
    assert "[page=1 line=1] Harbor Light Rentals" in numbered_lines(document)
    extracted = parse_offline(document)
    assert len(extracted.line_items) == 2
    assert [item.amount_cents.value for item in extracted.line_items] == [25000, 10000]
    assert extracted.amount_due_cents.value == 35000


def test_missing_or_fabricated_money_evidence_marks_field_and_handle_unverified():
    document = DocumentText(
        document_id="document", invoice_id="invoice", pages=[
            {"page": 1, "lines": [{"line": 1, "text": "INVOICE"}, {"line": 2, "text": "INV-001"}]},
        ],
    )
    raw = parse_offline(document)
    assert raw.amount_due_cents.value is None
    missing = to_payload(document, raw, "offline-layout-parser")
    amount = next(field for field in missing.fields if field.name == "amount_due_cents")
    assert amount.value is None and not amount.span.span_verified
    raw.amount_due_cents.value = 125000
    raw.amount_due_cents.quote = "fabricated total $1,250.00"
    fabricated = to_payload(document, raw, "offline-layout-parser")
    amount = next(field for field in fabricated.fields if field.name == "amount_due_cents")
    handle = next(handle for handle in fabricated.handles if handle.name.endswith(".amount_due_cents"))
    assert amount.value == 125000 and amount.trust == "untrusted"
    assert not amount.span.span_verified and not handle.span_verified
