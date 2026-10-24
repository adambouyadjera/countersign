"""Deterministic authorization over labeled inputs and current trusted AP records.

This module has no bank or LLM dependency. A successful comparison corroborates
document data; it never removes its untrusted source or treats detection of an
instruction as a payment rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from uuid import uuid4

from app.contract_models import DocumentText, PolicyCheck, PolicyDecision, SourceSpan
from app.security.graph import source_node_id
from app.security.interpreter import Interpreter, PaymentIntent, PurchaseOrderRef, VendorRef
from app.security.labels import DocumentSpan, Labeled, TrustedRecord, sources_of
from app.world.vendors import history_check


DEFAULT_INSTRUCTION = (
    "Pay every invoice in this batch that is due within 30 days and matches an "
    "open purchase order. Pay only to accounts on file; anything else needs "
    "human approval."
)
_MONEY = re.compile(r"(?<![\w.])(?:\$|USD\s*)?([0-9][0-9,]*\.[0-9]{2})(?![\d.])")


def normalize_account(value: Any) -> str:
    """Accept printed separators, never fuzzy-match an account number."""
    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"[\s-]", "", value)
    return normalized if re.fullmatch(r"[0-9]{4,34}", normalized) else ""


def _normalized(value: Any) -> str:
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _evidence(*values: Labeled) -> list[str]:
    return sorted({source_node_id(source) for source in sources_of(*values)})


class PolicyEngine:
    """The only automatic authorization decision, with no external side effects."""

    def __init__(self, store, *, amount_tolerance_cents: int = 0,
                 autonomy_limit_cents: int = 2_500_000, today: date | None = None):
        if type(amount_tolerance_cents) is not int or amount_tolerance_cents < 0:
            raise ValueError("Amount tolerance must be a nonnegative integer number of cents")
        self.store = store
        self.amount_tolerance_cents = amount_tolerance_cents
        if type(autonomy_limit_cents) is not int or autonomy_limit_cents < 0:
            raise ValueError("Autonomy limit must be a nonnegative integer number of cents")
        self.autonomy_limit_cents = autonomy_limit_cents
        self.today = today or datetime.now(timezone.utc).date()

    def _vendor(self, intent: PaymentIntent, fields: Mapping[str, Labeled] | None = None) -> dict[str, Any] | None:
        ref = intent.vendor.value
        if not isinstance(ref, VendorRef):
            return None
        if TrustedRecord("vendor", ref.vendor_id) not in intent.vendor.sources:
            return None
        vendor = self.store.get_record("vendors", ref.vendor_id)
        if vendor is None or vendor.get("vendor_id") != ref.vendor_id:
            return None
        if fields is not None:
            name = fields.get("vendor_name")
            if name is None or not self._verified(name, intent.invoice_id, kind="string"):
                return None
            # Trusted-ID arguments are permitted by the DSL, but choosing one
            # must not bypass deterministic identity resolution from this invoice.
            resolved = Interpreter(None, self.store.list_records("vendors"), []).resolve_vendor(name)
            if resolved.value is None or resolved.value.vendor_id != ref.vendor_id:
                return None
        return vendor

    def _slice(self, source: DocumentSpan, invoice_id: str, *, kind: str,
               supporting_documents: Sequence[DocumentText] = ()) -> str | None:
        if not source.span_verified:
            return None
        record = self.store.get_record("documents", source.document_id)
        if record is None:
            return None
        try:
            document = DocumentText.model_validate(record["text"])
        except (ValueError, KeyError, TypeError):
            return None
        if document.invoice_id != invoice_id or document.document_id != source.document_id:
            return None
        if getattr(document, "document_type", None) not in (None, "invoice"):
            # A letter is evidence of a requested account, never evidence of an
            # invoice amount or vendor identity. Its server-owned association
            # must be supplied explicitly by the run, not inferred from a
            # self-asserted span or arbitrary document ID.
            authorized = [item for item in supporting_documents
                          if item.document_id == document.document_id]
            if (kind != "account" or getattr(document, "document_type", None) != "vendor_bank_change_letter"
                    or len(authorized) != 1 or authorized[0] != document):
                return None
        lines = [line.text for page in document.pages if page.page == source.page
                 for line in page.lines if line.line == source.line]
        if (len(lines) != 1 or not 0 <= source.char_start < source.char_end <= len(lines[0])):
            return None
        return lines[0][source.char_start:source.char_end]

    def _verified(self, value: Labeled, invoice_id: str, *, kind: str,
                  supporting_documents: Sequence[DocumentText] = ()) -> bool:
        spans = [source for source in value.sources if isinstance(source, DocumentSpan)]
        # No source must not vacuously authorize a planner-invented literal.
        if not spans:
            return False
        if kind == "money" and (type(value.value) is not int or value.value <= 0):
            return False
        if kind == "account" and not normalize_account(value.value):
            return False
        if kind == "string" and (not isinstance(value.value, str) or not value.value.strip()):
            return False
        for source in spans:
            quote = self._slice(source, invoice_id, kind=kind,
                                supporting_documents=supporting_documents)
            if quote is None:
                return False
            if kind == "money":
                matches = []
                for match in _MONEY.finditer(quote):
                    try:
                        matches.append(Decimal(match[1].replace(",", "")) * 100 == value.value)
                    except InvalidOperation:
                        continue
                if not any(matches):
                    return False
            elif kind == "account":
                account = re.escape(normalize_account(value.value))
                if not re.search(rf"(?<![0-9]){account}(?![0-9])", re.sub(r"[\s-]", "", quote)):
                    return False
            elif not re.search(rf"(?<!\w){re.escape(_normalized(value.value))}(?!\w)", _normalized(quote)):
                return False
        return True

    def corroborate(self, intent: PaymentIntent, *,
                    supporting_documents: Sequence[DocumentText] = ()) -> PaymentIntent:
        """Return an enriched value, preserving every original source."""
        vendor = self._vendor(intent)
        if vendor is None:
            return intent
        expected = normalize_account(vendor.get("account_on_file_display", vendor.get("remit_account_number")))
        if (not expected or normalize_account(intent.to_account.value) != expected
                or not self._verified(intent.to_account, intent.invoice_id, kind="account",
                                      supporting_documents=supporting_documents)):
            return intent
        payee = Labeled(intent.to_account.value,
                        intent.to_account.sources | {TrustedRecord("vendor", vendor["vendor_id"])})
        return replace(intent, to_account=payee)

    def _matched_order(self, intent, vendor, matched_po, fields):
        if vendor is None:
            return None, None
        if matched_po is None:
            candidates = [value for value in intent.prerequisites if isinstance(value.value, PurchaseOrderRef)]
            matched_po = candidates[0] if len(candidates) == 1 else None
        po_number = fields.get("po_number") if fields is not None else None
        if po_number is not None and not self._verified(po_number, intent.invoice_id, kind="string"):
            return None, matched_po
        if matched_po is not None:
            ref = matched_po.value
            if not isinstance(ref, PurchaseOrderRef):
                return None, matched_po
            if TrustedRecord("purchase_order", ref.purchase_order_id) not in matched_po.sources:
                return None, matched_po
            order = self.store.get_record("purchase_orders", ref.purchase_order_id)
        else:
            # A planner cannot bypass matching simply by omitting match_po.
            if po_number is None:
                return None, None
            candidates = [order for order in self.store.list_records("purchase_orders")
                          if order.get("vendor_id") == vendor["vendor_id"]
                          and _normalized(order.get("po_number")) == _normalized(po_number.value)]
            order = candidates[0] if len(candidates) == 1 else None
        if (order is None or order.get("status") != "open"
                or order.get("vendor_id") != vendor["vendor_id"]):
            return None, matched_po
        if ("purchase_order_ids" in vendor
                and order["purchase_order_id"] not in vendor["purchase_order_ids"]):
            return None, matched_po
        if po_number is not None and _normalized(order.get("po_number")) != _normalized(po_number.value):
            return None, matched_po
        return order, matched_po

    def evaluate(
        self, intent: PaymentIntent, *, run_id: str, invoice_number: Labeled[str],
        matched_po: Labeled[PurchaseOrderRef | None] | None = None,
        instruction_like_spans: Sequence[SourceSpan] = (),
        field_values: Mapping[str, Labeled] | None = None,
        trusted_instruction: str | None = None,
        supporting_documents: Sequence[DocumentText] = (),
    ) -> PolicyDecision:
        vendor = self._vendor(intent, field_values)
        vendor_sources = Labeled(None, {TrustedRecord("vendor", vendor["vendor_id"])}) if vendor else Labeled(None)
        amount_verified = self._verified(intent.amount, intent.invoice_id, kind="money")
        account_verified = self._verified(intent.to_account, intent.invoice_id, kind="account",
                                          supporting_documents=supporting_documents)
        number_verified = self._verified(invoice_number, intent.invoice_id, kind="string")
        span_verified = amount_verified and account_verified and number_verified
        on_file = normalize_account(vendor.get("account_on_file_display", vendor.get("remit_account_number"))) if vendor else ""
        payee_matches = bool(on_file and normalize_account(intent.to_account.value) == on_file)
        history_passed, history_message = history_check(self.store, vendor, intent.to_account.value)
        over_autonomy_limit = (type(intent.amount.value) is int
                               and intent.amount.value > self.autonomy_limit_cents)
        letter_ids = {document.document_id for document in supporting_documents
                      if document.invoice_id == intent.invoice_id
                      and getattr(document, "document_type", None) == "vendor_bank_change_letter"}
        letter_requested = any(isinstance(source, DocumentSpan) and source.document_id in letter_ids
                               for source in intent.to_account.sources)
        order, matched_po = self._matched_order(intent, vendor, matched_po, field_values)
        open_amount = order.get("open_amount_cents", order.get("amount_cents")) if order else None
        amount_matches = bool(
            order and type(intent.amount.value) is int and intent.amount.value > 0
            and type(open_amount) is int and open_amount > 0
            and order.get("currency") == "USD"
            and abs(intent.amount.value - open_amount) <= self.amount_tolerance_cents
        )
        currency = field_values.get("currency") if field_values is not None else None
        if field_values is not None:
            amount_matches = amount_matches and bool(currency is not None and currency.value == "USD"
                                                     and self._verified(currency, intent.invoice_id, kind="string"))
        duplicate = bool(isinstance(invoice_number.value, str) and invoice_number.value.strip()
                         and self.store.has_session_payment(invoice_number.value))
        reasons = []
        if vendor is None:
            reasons.append("UNKNOWN_VENDOR")
        if not span_verified:
            reasons.append("UNVERIFIED_SOURCE")
        if vendor is not None and not payee_matches:
            reasons.append("BANK_DETAILS_MISMATCH")
        if vendor is not None and not history_passed:
            reasons.append("BANK_DETAILS_MISMATCH")
        if vendor is not None and not amount_matches:
            reasons.append("AMOUNT_MISMATCH")
        if duplicate:
            reasons.append("DUPLICATE_INVOICE")
        if over_autonomy_limit:
            reasons.append("OVER_AUTONOMY_LIMIT")

        instruction_supported = trusted_instruction in (None, DEFAULT_INSTRUCTION)
        due_supported = True
        if field_values is not None:
            due = field_values.get("due_date")
            due_supported = due is not None and self._verified(due, intent.invoice_id, kind="string")
            if due_supported:
                try:
                    due_supported = date.fromisoformat(due.value) <= self.today + timedelta(days=30)
                except (TypeError, ValueError):
                    due_supported = False
        if any(value.value is False for value in intent.prerequisites):
            due_supported = False
        if not instruction_supported or not due_supported:
            reasons.append("POLICY_NOT_IMPLEMENTED")

        po_sources = Labeled(None, {TrustedRecord("purchase_order", order["purchase_order_id"])}) if order else Labeled(None)
        po_evidence = _evidence(intent.amount, po_sources, *([matched_po] if matched_po is not None else []),
                                *([field_values["po_number"]] if field_values and "po_number" in field_values else []))
        instruction_evidence = sorted({source_node_id(DocumentSpan(
            span.document_id, span.page, span.line, span.char_start, span.char_end, span.span_verified,
        )) for span in instruction_like_spans})
        name_evidence = [field_values["vendor_name"]] if field_values and "vendor_name" in field_values else []
        payee_message = (
            "Document remittance matches the trusted account and is corroborated." if payee_matches else
            "Untrusted bank-change letter requests a different account; human countersign is required and the vendor master is unchanged."
            if letter_requested else "Requested account differs from, or has no, trusted vendor account."
        )
        if not payee_matches and vendor is not None:
            payee_message += " " + history_message
        autonomy_message = (f" Automatic payment exceeds the agent autonomy limit of {self.autonomy_limit_cents} cents; "
                            "an independent authorized approver is required.") if over_autonomy_limit else ""
        checks = [
            PolicyCheck(code="VENDOR_KNOWN", passed=vendor is not None,
                        message="Vendor resolves to a current trusted vendor master record." if vendor else
                        "Vendor did not resolve to a trusted vendor master record.",
                        evidence_node_ids=_evidence(intent.vendor, vendor_sources, *name_evidence)),
            PolicyCheck(code="SPAN_VERIFIED", passed=span_verified,
                        message="Amount, account and invoice identity are grounded in verified source spans." if span_verified else
                        "Missing, foreign, unverified or ungrounded document payment source.",
                        evidence_node_ids=_evidence(intent.amount, intent.to_account, invoice_number)),
            PolicyCheck(code="PAYEE_ON_FILE", passed=payee_matches,
                        message=payee_message,
                        evidence_node_ids=_evidence(intent.to_account, vendor_sources)),
            PolicyCheck(code="AMOUNT_MATCHES_PO", passed=amount_matches,
                        message=(f"Positive USD amount matches an open vendor PO within {self.amount_tolerance_cents} cents." if amount_matches else
                                 "Amount does not match the current open vendor purchase order.") + autonomy_message,
                        evidence_node_ids=po_evidence),
            PolicyCheck(code="NOT_DUPLICATE", passed=not duplicate,
                        message="Invoice number has not been paid or reserved in this session." if not duplicate else
                        "Invoice number is already paid or reserved in this session.",
                        evidence_node_ids=_evidence(invoice_number)),
            PolicyCheck(code="INSTRUCTIONS_IN_DOCUMENT", passed=True,
                        message="Warning: instruction-like document text is attached as untrusted evidence; detection does not authorize or deny payment."
                        if instruction_like_spans else
                        "No instruction-like spans were flagged; detection is advisory only.",
                        evidence_node_ids=instruction_evidence),
            PolicyCheck(code="VENDOR_HISTORY", passed=history_passed,
                        message=history_message,
                        evidence_node_ids=_evidence(intent.to_account, vendor_sources)),
        ]
        outcome = "BLOCK" if duplicate else "HOLD" if reasons else "ALLOW"
        return PolicyDecision(decision_id=f"decision-{uuid4().hex}", run_id=run_id,
                              invoice_id=intent.invoice_id, outcome=outcome,
                              checks=checks, reason_codes=list(dict.fromkeys(reasons)))
