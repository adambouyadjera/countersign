"""Supporting letters remain requested payment evidence, never trusted records."""

from dataclasses import replace
from datetime import date

import pytest

from app.contract_models import DocumentText, ExtractedField, FieldsExtractedPayload
from app.security.graph import build_graph, check_node_id, supporting_field_node_id
from app.security.handles import HandleTable
from app.security.interpreter import PaymentIntent, PurchaseOrderRef, VendorRef
from app.security.labels import DocumentSpan, Labeled, TrustedRecord
from app.security.policy import DEFAULT_INSTRUCTION, PolicyEngine
from app.store import Store
from app.world.seed import seed_trusted_world


@pytest.fixture
def letter_world():
    store = Store(":memory:")
    store.initialize()
    records = seed_trusted_world(store)
    invoice_id = "invoice-letter-test"
    rows = [
        ("vendor_name", "Golden Gate Sanitation", "string", "Golden Gate Sanitation"),
        ("invoice_number", "INV-003", "string", "INV-003"),
        ("po_number", "PO-003", "string", "PO-003"),
        ("amount_due_cents", 64000, "money", "$640.00"),
        ("remit_account_number", "300300003333", "account_number", "300300003333"),
        ("currency", "USD", "string", "USD"),
        ("due_date", "2026-11-01", "date", "2026-11-01"),
    ]
    document = DocumentText(document_id="invoice-document", invoice_id=invoice_id, pages=[{
        "page": 1, "lines": [{"line": index, "text": row[3]} for index, row in enumerate(rows, 1)],
    }])
    letter = DocumentText(document_id="letter-document", invoice_id=invoice_id,
        document_type="vendor_bank_change_letter", pages=[{"page": 1, "lines": [
            {"line": 1, "text": "Golden Gate Sanitation"},
            {"line": 2, "text": "New remittance account: 999900009999"},
            # Deliberately present money to ensure a letter cannot replace an invoice amount.
            {"line": 3, "text": "$640.00"},
        ]}])
    for source_document in (document, letter):
        store.save_record("documents", source_document.document_id,
                          {"text": source_document.model_dump(mode="json")})
    fields = [ExtractedField(name=name, value=value, type=kind, trust="untrusted", span={
        "document_id": document.document_id, "page": 1, "line": index,
        "char_start": 0, "char_end": len(quote), "quote": quote, "span_verified": True,
    }) for index, (name, value, kind, quote) in enumerate(rows, 1)]
    extraction = FieldsExtractedPayload(invoice_id=invoice_id, extractor_model="test",
        fields=fields, handles=[], instruction_like_spans=[])
    table = HandleTable()
    extraction.handles = table.add_invoice(document, extraction, 3)
    values = {name: table.values[f"$D3.{name}"] for name, *_ in rows}
    vendor = Labeled(VendorRef("VEN-003", "Golden Gate Sanitation"),
                     values["vendor_name"].sources | {TrustedRecord("vendor", "VEN-003")})
    order = Labeled(PurchaseOrderRef("PO-003", "VEN-003", 64000, "USD"),
                    values["po_number"].sources | {TrustedRecord("purchase_order", "PO-003")})
    account_span = DocumentSpan(letter.document_id, 1, 2, 24, 36, True)
    # Calculate the exact substring rather than trusting a hardcoded coordinate.
    account_text = letter.pages[0].lines[1].text
    start = account_text.index("999900009999")
    account_span = replace(account_span, char_start=start, char_end=start + 12)
    requested = Labeled("999900009999", {account_span})
    intent = PaymentIntent(invoice_id=invoice_id,
        invoice=Labeled(invoice_id, {TrustedRecord("invoice", invoice_id)}), vendor=vendor,
        amount=values["amount_due_cents"], to_account=requested,
        memo=values["invoice_number"], prerequisites=(vendor, order))
    support_field = ExtractedField(name="bank_change_remit_account_number", value=requested.value,
        type="account_number", trust="untrusted", span={
            "document_id": letter.document_id, "page": 1, "line": 2,
            "char_start": start, "char_end": start + 12,
            "quote": requested.value, "span_verified": True,
        })
    yield {"store": store, "records": records, "document": document, "letter": letter,
           "fields": values, "extraction": extraction, "intent": intent, "order": order,
           "support_field": support_field, "engine": PolicyEngine(store, today=date(2026, 10, 24))}
    store.close()


def evaluate(world, *, intent=None, supporting_documents=None):
    return world["engine"].evaluate(intent or world["intent"], run_id="letter-run",
        invoice_number=world["fields"]["invoice_number"], matched_po=world["order"],
        field_values=world["fields"], trusted_instruction=DEFAULT_INSTRUCTION,
        supporting_documents=[world["letter"]] if supporting_documents is None else supporting_documents)


def test_letter_account_is_verified_but_never_changes_the_vendor_master(letter_world):
    world = letter_world
    before = world["store"].get_record("vendors", "VEN-003")
    decision = evaluate(world)
    assert decision.outcome == "HOLD" and decision.reason_codes == ["BANK_DETAILS_MISMATCH"]
    assert next(check for check in decision.checks if check.code == "SPAN_VERIFIED").passed
    payee = next(check for check in decision.checks if check.code == "PAYEE_ON_FILE")
    assert not payee.passed and "vendor master is unchanged" in payee.message
    assert world["store"].get_record("vendors", "VEN-003") == before
    assert world["engine"].corroborate(world["intent"], supporting_documents=[world["letter"]]).to_account.trust == "untrusted"


@pytest.mark.parametrize("failure", ["not_supplied", "foreign_invoice", "forged_copy", "false_flag", "fabricated_value"])
def test_letter_account_requires_explicit_genuine_linked_document(letter_world, failure):
    world = letter_world
    intent = world["intent"]
    documents = [world["letter"]]
    if failure == "not_supplied":
        documents = []
    elif failure == "foreign_invoice":
        documents = [world["letter"].model_copy(update={"invoice_id": "other-invoice"})]
    elif failure == "forged_copy":
        documents = [world["letter"].model_copy(deep=True)]
        documents[0].pages[0].lines[1].text = "New remittance account: 111122223333"
    elif failure == "false_flag":
        source = next(iter(intent.to_account.sources))
        intent = replace(intent, to_account=Labeled(intent.to_account.value, {replace(source, span_verified=False)}))
    else:
        intent = replace(intent, to_account=Labeled("111122223333", intent.to_account.sources))
    decision = evaluate(world, intent=intent, supporting_documents=documents)
    assert decision.outcome == "HOLD" and "UNVERIFIED_SOURCE" in decision.reason_codes
    assert not next(check for check in decision.checks if check.code == "SPAN_VERIFIED").passed


def test_letter_cannot_supply_an_invoice_amount_even_when_the_number_matches(letter_world):
    world = letter_world
    source = DocumentSpan(world["letter"].document_id, 1, 3, 0, len("$640.00"), True)
    intent = replace(world["intent"], amount=Labeled(64000, {source}))
    decision = evaluate(world, intent=intent)
    assert "UNVERIFIED_SOURCE" in decision.reason_codes
    assert not next(check for check in decision.checks if check.code == "SPAN_VERIFIED").passed


def test_matching_letter_retains_untrusted_span_when_corroborated(letter_world):
    world = letter_world
    letter = world["letter"]
    letter.pages[0].lines[1].text = letter.pages[0].lines[1].text.replace("999900009999", "300300003333")
    world["store"].save_record("documents", letter.document_id, {"text": letter.model_dump(mode="json")})
    account = Labeled("300300003333", world["intent"].to_account.sources)
    intent = replace(world["intent"], to_account=account)
    decision = evaluate(world, intent=intent)
    assert decision.outcome == "ALLOW"
    corroborated = world["engine"].corroborate(intent, supporting_documents=[letter])
    assert corroborated.to_account.trust == "corroborated"
    assert corroborated.to_account.sources == account.sources | {TrustedRecord("vendor", "VEN-003")}
    assert world["store"].get_record("vendors", "VEN-003")["account_on_file_display"] == "300300003333"


def test_held_graph_shows_letter_request_and_original_invoice_separately(letter_world):
    world = letter_world
    field = world["support_field"]
    # The graph retrieves actual document text instead of trusting a quoted claim.
    field.span.quote = "invented quote"
    graph = build_graph(run_id="letter-run", invoice_id=world["document"].invoice_id,
        document=world["document"], extraction=world["extraction"], steps=[],
        decision=evaluate(world), intent=world["intent"],
        vendors=world["records"]["vendors"], purchase_orders=world["records"]["purchase_orders"],
        supporting_documents=[world["letter"]], supporting_fields=[field])
    span = next(node for node in graph.nodes if node.kind == "span"
                and node.span.document_id == world["letter"].document_id)
    assert span.label == "999900009999" and span.trust == "untrusted"
    field_id = supporting_field_node_id(graph.invoice_id, world["letter"].document_id, field.name)
    requested_field = next(node for node in graph.nodes if node.id == field_id)
    assert requested_field.detail["role"] == "requested_remittance"
    assert requested_field.detail["vendor_master_updated"] is False
    target = check_node_id(graph.invoice_id, "PAYEE_ON_FILE")
    assert any(edge.source == span.id and edge.target == field_id and edge.kind == "derived_from" for edge in graph.edges)
    assert any(edge.source == field_id and edge.target == target and edge.kind == "checked_by" for edge in graph.edges)
    assert any(node.kind == "field" and node.label == "remit_account_number"
               and node.detail["value"] == "300300003333" for node in graph.nodes)
    assert not any(node.kind == "plan_step" for node in graph.nodes)


def test_graph_rejects_foreign_supporting_document(letter_world):
    world = letter_world
    with pytest.raises(ValueError, match="Supporting graph documents"):
        build_graph(run_id="letter-run", invoice_id=world["document"].invoice_id,
            document=world["document"], extraction=world["extraction"], steps=[],
            decision=evaluate(world), intent=world["intent"],
            supporting_documents=[world["letter"].model_copy(update={"invoice_id": "foreign"})])
