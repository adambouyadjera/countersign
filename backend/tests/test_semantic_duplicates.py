"""A changed invoice number cannot bypass ordinary AP duplicate controls."""

import pytest

from app.security.labels import DocumentSpan, Labeled
from tests.test_policy import check, evaluate, world


def invoice_date(world, value="2026-10-24", *, verified=True):
    """Supply an actual invoice-date span, rather than an issue-time guess."""
    document = world["store"].get_record("documents", "doc-1")
    lines = document["text"]["pages"][0]["lines"]
    number = max(line["line"] for line in lines) + 1
    prefix = "Invoice date: "
    text = prefix + value
    lines.append({"line": number, "text": text})
    world["store"].save_record("documents", "doc-1", document)
    world["fields"]["invoice_date"] = Labeled(value, {
        DocumentSpan("doc-1", 1, number, len(prefix), len(text), verified),
    })


def previously_paid(world, *, vendor_id="VEN-001", amount_cents=125000,
                    date="2026-10-24", po_number=None, state="submitted"):
    assert world["store"].claim_session_payment(
        "ARCHIVE-DIFFERENT-NUMBER", "prior-payment-attempt",
        vendor_id=vendor_id, amount_cents=amount_cents,
        invoice_date=date, po_number=po_number,
    )
    if state != "reserved":
        world["store"].finish_session_payment("ARCHIVE-DIFFERENT-NUMBER", state=state)
    # The current invoice number is new; semantic matching must do the work.
    assert not world["store"].has_session_payment("INV-001")


@pytest.mark.parametrize("state", ["submitted", "reserved", "uncertain"])
def test_new_number_same_vendor_amount_and_date_blocks_paid_or_pending(world, state):
    invoice_date(world)
    previously_paid(world, state=state)

    decision = evaluate(world)

    assert decision.outcome == "BLOCK"
    assert "DUPLICATE_INVOICE" in decision.reason_codes
    assert check(decision, "NOT_DUPLICATE").passed is False


@pytest.mark.parametrize("prior_po", [None, "PO-A-DIFFERENT-SERVICE"])
def test_same_vendor_and_amount_on_different_date_without_same_po_passes(world, prior_po):
    invoice_date(world)
    previously_paid(world, date="2026-10-23", po_number=prior_po)

    decision = evaluate(world)

    assert check(decision, "NOT_DUPLICATE").passed is True
    assert decision.outcome == "ALLOW"
    assert decision.reason_codes == []


def test_different_vendor_with_same_amount_date_and_po_does_not_false_block(world):
    invoice_date(world)
    previously_paid(world, vendor_id="VEN-002", po_number="PO-001")

    decision = evaluate(world)

    assert check(decision, "NOT_DUPLICATE").passed is True
    assert decision.outcome == "ALLOW"


def test_same_vendor_amount_and_po_blocks_even_when_invoice_date_changed(world):
    invoice_date(world)
    previously_paid(world, date="2026-10-23", po_number="PO-001")

    decision = evaluate(world)

    assert decision.outcome == "BLOCK"
    assert decision.reason_codes == ["DUPLICATE_INVOICE"]
    assert check(decision, "NOT_DUPLICATE").passed is False


@pytest.mark.parametrize("value,verified", [("2026-10-24", False), ("not-an-iso-date", True)])
def test_unverified_or_invalid_date_is_not_accepted_as_semantic_identity(world, value, verified):
    invoice_date(world, value, verified=verified)
    previously_paid(world)

    decision = evaluate(world)

    assert check(decision, "NOT_DUPLICATE").passed is True
    assert "DUPLICATE_INVOICE" not in decision.reason_codes


def test_same_vendor_date_and_po_with_different_amount_does_not_false_block(world):
    invoice_date(world)
    previously_paid(world, amount_cents=124999, po_number="PO-001")

    decision = evaluate(world)

    assert check(decision, "NOT_DUPLICATE").passed is True
    assert decision.outcome == "ALLOW"


def test_missing_invoice_date_does_not_borrow_due_date_or_the_current_date(world):
    previously_paid(world)

    decision = evaluate(world)

    assert check(decision, "NOT_DUPLICATE").passed is True
    assert decision.outcome == "ALLOW"
