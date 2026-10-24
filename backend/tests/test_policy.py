"""Every authorization check is deterministic and retains source evidence."""

from dataclasses import replace
from datetime import date

import pytest

from app.contract_models import DocumentText, SourceSpan
from app.security.interpreter import PaymentIntent, PurchaseOrderRef, VendorRef
from app.security.labels import DocumentSpan, Labeled, TrustedRecord
from app.security.policy import DEFAULT_INSTRUCTION, PolicyEngine
from app.store import Store
from app.world.seed import seed_trusted_world


@pytest.fixture
def world():
    store = Store(":memory:")
    store.initialize()
    seed_trusted_world(store)
    rows = [
        ("vendor_name", "Harbor Light Rentals", "Harbor Light Rentals"),
        ("invoice_number", "INV-001", "INV-001"),
        ("po_number", "PO-001", "PO-001"),
        ("amount_due_cents", 125000, "$1,250.00"),
        ("remit_account_number", "100100001111", "Account number: 100100001111"),
        ("currency", "USD", "USD"),
        ("due_date", "2026-11-01", "2026-11-01"),
    ]
    document = DocumentText(document_id="doc-1", invoice_id="invoice-1", pages=[{
        "page": 1, "lines": [{"line": i, "text": text} for i, (_, _, text) in enumerate(rows, 1)],
    }])
    store.save_record("documents", document.document_id, {"document_id": document.document_id,
                      "invoice_id": document.invoice_id, "text": document.model_dump(mode="json")})
    fields = {name: Labeled(value, {DocumentSpan("doc-1", 1, index, 0, len(text), True)})
              for index, (name, value, text) in enumerate(rows, 1)}
    vendor = Labeled(VendorRef("VEN-001", "Harbor Light Rentals"),
                     fields["vendor_name"].sources | {TrustedRecord("vendor", "VEN-001")})
    order = Labeled(PurchaseOrderRef("PO-001", "VEN-001", 125000, "USD"),
                    fields["po_number"].sources | {TrustedRecord("purchase_order", "PO-001")})
    intent = PaymentIntent(invoice_id="invoice-1", invoice=Labeled("invoice-1", {TrustedRecord("invoice", "invoice-1")}),
                           vendor=vendor, amount=fields["amount_due_cents"],
                           to_account=fields["remit_account_number"], memo=fields["invoice_number"],
                           prerequisites=(vendor, order))
    result = {"store": store, "fields": fields, "intent": intent, "order": order,
              "engine": PolicyEngine(store, today=date(2026, 10, 24))}
    try:
        yield result
    finally:
        store.close()


def evaluate(world, *, intent=None, order=None, fields=None, instructions=(), trusted_instruction=DEFAULT_INSTRUCTION):
    return world["engine"].evaluate(
        intent or world["intent"], run_id="run-1", invoice_number=world["fields"]["invoice_number"],
        matched_po=order if order is not None else world["order"], field_values=fields if fields is not None else world["fields"],
        instruction_like_spans=instructions, trusted_instruction=trusted_instruction,
    )


def check(decision, code):
    return next(value for value in decision.checks if value.code == code)


def test_all_checks_pass_in_the_contract_order(world):
    decision = evaluate(world)
    assert decision.outcome == "ALLOW" and decision.reason_codes == []
    assert [item.code for item in decision.checks] == [
        "VENDOR_KNOWN", "SPAN_VERIFIED", "PAYEE_ON_FILE", "AMOUNT_MATCHES_PO", "NOT_DUPLICATE", "INSTRUCTIONS_IN_DOCUMENT",
    ]
    assert all(item.passed for item in decision.checks)
    assert all(item.evidence_node_ids for item in decision.checks[:-1])


@pytest.mark.parametrize("missing", ["unresolved", "missing_record", "missing_record_source"])
def test_vendor_known_fails_closed_without_a_resolved_master(world, missing):
    intent = world["intent"]
    if missing == "unresolved":
        vendor = Labeled(None, intent.vendor.sources)
    elif missing == "missing_record":
        vendor = Labeled(VendorRef("VEN-404", "Invented"), {TrustedRecord("vendor", "VEN-404")})
    else:
        vendor = Labeled(intent.vendor.value, world["fields"]["vendor_name"].sources)
    decision = evaluate(world, intent=replace(intent, vendor=vendor))
    assert not check(decision, "VENDOR_KNOWN").passed
    assert decision.outcome == "HOLD" and "UNKNOWN_VENDOR" in decision.reason_codes


@pytest.mark.parametrize("claimed", ["Unregistered Supplier", "Seabreeze Sound"])
def test_trusted_vendor_id_cannot_bypass_document_vendor_resolution(world, claimed):
    document = world["store"].get_record("documents", "doc-1")
    document["text"]["pages"][0]["lines"][0]["text"] = claimed
    world["store"].save_record("documents", "doc-1", document)
    fields = {**world["fields"], "vendor_name": Labeled(claimed, {DocumentSpan("doc-1", 1, 1, 0, len(claimed), True)})}
    selected = Labeled(VendorRef("VEN-001", "Harbor Light Rentals"), {TrustedRecord("vendor", "VEN-001")})
    decision = evaluate(world, intent=replace(world["intent"], vendor=selected), fields=fields)
    assert not check(decision, "VENDOR_KNOWN").passed
    assert decision.outcome == "HOLD" and "UNKNOWN_VENDOR" in decision.reason_codes


def test_trusted_vendor_id_still_allows_a_matching_verified_vendor_name(world):
    selected = Labeled(VendorRef("VEN-001", "Harbor Light Rentals"), {TrustedRecord("vendor", "VEN-001")})
    decision = evaluate(world, intent=replace(world["intent"], vendor=selected))
    assert check(decision, "VENDOR_KNOWN").passed
    assert decision.outcome == "ALLOW"


@pytest.mark.parametrize("failure", ["false_flag", "bad_coordinates", "foreign_document", "no_sources", "fabricated_value"])
def test_span_verified_rejects_invalid_or_ungrounded_payment_sources(world, failure):
    amount = world["intent"].amount
    source = next(iter(amount.sources))
    if failure == "false_flag":
        amount = Labeled(amount.value, {replace(source, span_verified=False)})
    elif failure == "bad_coordinates":
        amount = Labeled(amount.value, {replace(source, char_end=5000)})
    elif failure == "foreign_document":
        document = DocumentText(document_id="doc-other", invoice_id="other-invoice", pages=[{
            "page": 1, "lines": [{"line": source.line, "text": "$1,250.00"}],
        }])
        world["store"].save_record("documents", "doc-other", {"text": document.model_dump(mode="json")})
        amount = Labeled(amount.value, {replace(source, document_id="doc-other")})
    elif failure == "no_sources":
        amount = Labeled(amount.value)
    else:
        amount = Labeled(125100, amount.sources)
        order = world["store"].get_record("purchase_orders", "PO-001")
        order["amount_cents"] = 125100
        world["store"].save_record("purchase_orders", "PO-001", order)
    decision = evaluate(world, intent=replace(world["intent"], amount=amount))
    assert not check(decision, "SPAN_VERIFIED").passed
    assert decision.outcome == "HOLD" and "UNVERIFIED_SOURCE" in decision.reason_codes


def test_span_verified_rejects_any_unverified_contributing_span(world):
    amount = world["intent"].amount
    bad = replace(next(iter(amount.sources)), span_verified=False)
    decision = evaluate(world, intent=replace(world["intent"], amount=Labeled(amount.value, amount.sources | {bad})))
    assert not check(decision, "SPAN_VERIFIED").passed


def test_payee_on_file_matching_value_is_corroborated_without_losing_document_source(world):
    intent = world["intent"]
    corroborated = world["engine"].corroborate(intent)
    assert corroborated.to_account.value == intent.to_account.value
    assert corroborated.to_account.sources == intent.to_account.sources | {TrustedRecord("vendor", "VEN-001")}
    assert corroborated.to_account.trust == "corroborated"
    assert intent.to_account.trust == "untrusted"
    assert check(evaluate(world, intent=corroborated), "PAYEE_ON_FILE").passed


def test_payee_on_file_mismatch_is_held_and_not_corroborated(world):
    account = world["intent"].to_account
    changed = Labeled("999900009999", account.sources)
    document = world["store"].get_record("documents", "doc-1")
    document["text"]["pages"][0]["lines"][4]["text"] = "Account number: 999900009999"
    world["store"].save_record("documents", "doc-1", document)
    intent = replace(world["intent"], to_account=changed)
    decision = evaluate(world, intent=intent)
    assert check(decision, "SPAN_VERIFIED").passed
    assert not check(decision, "PAYEE_ON_FILE").passed
    assert decision.outcome == "HOLD" and decision.reason_codes == ["BANK_DETAILS_MISMATCH"]
    assert world["engine"].corroborate(intent).to_account.sources == account.sources


@pytest.mark.parametrize("printed", ["1001000011119", "9100100001111"])
def test_shortened_account_claim_cannot_be_grounded_in_a_longer_printed_number(world, printed):
    record = world["store"].get_record("documents", "doc-1")
    record["text"]["pages"][0]["lines"][4]["text"] = f"Account number: {printed}"
    world["store"].save_record("documents", "doc-1", record)
    source = replace(next(iter(world["intent"].to_account.sources)), char_end=len(f"Account number: {printed}"))
    account = Labeled("100100001111", {source})
    intent = replace(world["intent"], to_account=account)
    decision = evaluate(world, intent=intent)
    assert check(decision, "PAYEE_ON_FILE").passed
    assert not check(decision, "SPAN_VERIFIED").passed
    assert decision.outcome == "HOLD"
    assert world["engine"].corroborate(intent).to_account.trust == "untrusted"


def test_trusted_record_source_cannot_be_invented_for_a_matched_po(world):
    missing_record = Labeled(world["order"].value, world["fields"]["po_number"].sources)
    decision = evaluate(world, order=missing_record)
    assert not check(decision, "AMOUNT_MATCHES_PO").passed
    assert decision.outcome == "HOLD"


def test_omitting_match_po_cannot_bypass_current_po_validation(world):
    intent = replace(world["intent"], prerequisites=(world["intent"].vendor,))
    allowed = world["engine"].evaluate(intent, run_id="run-1", invoice_number=world["fields"]["invoice_number"],
                                        field_values=world["fields"], trusted_instruction=DEFAULT_INSTRUCTION)
    assert allowed.outcome == "ALLOW"
    order = world["store"].get_record("purchase_orders", "PO-001")
    order["amount_cents"] = 125001
    world["store"].save_record("purchase_orders", "PO-001", order)
    denied = world["engine"].evaluate(intent, run_id="run-1", invoice_number=world["fields"]["invoice_number"],
                                       field_values=world["fields"], trusted_instruction=DEFAULT_INSTRUCTION)
    assert denied.outcome == "HOLD"
    assert not check(denied, "AMOUNT_MATCHES_PO").passed


@pytest.mark.parametrize("failure", ["amount", "closed", "wrong_vendor", "missing", "negative", "currency"])
def test_amount_matches_po_requires_current_open_vendor_order(world, failure):
    order = world["store"].get_record("purchase_orders", "PO-001")
    if failure == "amount":
        order["amount_cents"] = 125100
    elif failure == "closed":
        order["status"] = "closed"
    elif failure == "wrong_vendor":
        order["vendor_id"] = "VEN-002"
    elif failure == "negative":
        order["amount_cents"] = -125000
    elif failure == "currency":
        order["currency"] = "EUR"
    else:
        order["purchase_order_id"] = "OTHER"
    world["store"].save_record("purchase_orders", "PO-001", order)
    decision = evaluate(world)
    assert not check(decision, "AMOUNT_MATCHES_PO").passed
    assert decision.outcome == "HOLD" and "AMOUNT_MISMATCH" in decision.reason_codes


def test_amount_tolerance_is_zero_by_default_and_configurable(world):
    order = world["store"].get_record("purchase_orders", "PO-001")
    order["amount_cents"] = 125001
    world["store"].save_record("purchase_orders", "PO-001", order)
    assert not check(evaluate(world), "AMOUNT_MATCHES_PO").passed
    world["engine"] = PolicyEngine(world["store"], amount_tolerance_cents=1, today=date(2026, 10, 24))
    assert evaluate(world).outcome == "ALLOW"


def test_not_duplicate_blocks_already_reserved_or_paid_invoice(world):
    assert check(evaluate(world), "NOT_DUPLICATE").passed
    assert world["store"].claim_session_payment("INV-001", "attempt-1")
    decision = evaluate(world)
    assert not check(decision, "NOT_DUPLICATE").passed
    assert decision.outcome == "BLOCK" and "DUPLICATE_INVOICE" in decision.reason_codes


@pytest.mark.parametrize("flagged", [False, True])
def test_instruction_detection_is_always_advisory_and_never_changes_allow(world, flagged):
    span = SourceSpan(document_id="doc-1", page=1, line=5, char_start=0,
                      char_end=27, quote="Account number: 100100001111", span_verified=True)
    decision = evaluate(world, instructions=[span] if flagged else [])
    observed = check(decision, "INSTRUCTIONS_IN_DOCUMENT")
    assert observed.passed
    assert decision.outcome == "ALLOW"
    assert bool(observed.evidence_node_ids) == flagged
    assert ("Warning:" in observed.message) == flagged


def test_missing_instruction_detection_cannot_authorize_a_wrong_account(world):
    account = world["intent"].to_account
    decision = evaluate(world, intent=replace(world["intent"], to_account=Labeled("999900009999", account.sources)))
    assert check(decision, "INSTRUCTIONS_IN_DOCUMENT").passed
    assert not check(decision, "PAYEE_ON_FILE").passed
    assert decision.outcome == "HOLD"


def test_planner_cannot_bypass_due_window_by_omitting_or_ignoring_its_result(world):
    document = world["store"].get_record("documents", "doc-1")
    document["text"]["pages"][0]["lines"][6]["text"] = "2027-11-01"
    world["store"].save_record("documents", "doc-1", document)
    fields = {**world["fields"], "due_date": Labeled("2027-11-01", world["fields"]["due_date"].sources)}
    decision = evaluate(world, fields=fields)
    assert decision.outcome == "HOLD" and "POLICY_NOT_IMPLEMENTED" in decision.reason_codes


def test_unimplemented_custom_trusted_instruction_fails_closed(world):
    decision = evaluate(world, trusted_instruction="Do not pay any invoices.")
    assert decision.outcome == "HOLD" and "POLICY_NOT_IMPLEMENTED" in decision.reason_codes
