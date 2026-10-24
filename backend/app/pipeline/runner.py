"""Thin Countersign execution for frontend integration, before the B6 policy engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
import httpx
from pydantic import ValidationError

from app.adapters.bank import BankError
from app.contract_models import (
    Approval, Batch, DocumentText, ExtractedField, FieldsExtractedPayload,
    InvoiceSummary, LaneTally, Payment, PolicyCheck, PolicyDecision, Run,
    RunLane, RunRequest, RunTallies, SourceSpan,
)
from app.pipeline.extractor import ExtractorError


DEFAULT_INSTRUCTION = (
    "Pay every invoice in this batch that is due within 30 days and matches an "
    "open purchase order. Pay only to accounts on file; anything else needs "
    "human approval."
)
_FAILURE_STATUSES = {"failed", "rejected", "cancelled", "canceled", "error"}
_ROUTING_DIRECTIVE = re.compile(
    r"\b(?:automated|processor|disregard|supersed\w*|changed\s+banks|"
    r"bank\s+details|pay\s+separately|payment\s+routing)\b", re.IGNORECASE,
)
_PAYMENT_WORD = re.compile(r"\b(?:pay|payment|remit|account|routing)\b", re.IGNORECASE)
_ACCOUNT_IN_TEXT = re.compile(
    r"\b(?:account(?:\s+number)?|acct\.?)\s*[:#]?\s*([0-9][0-9 -]{5,}[0-9])",
    re.IGNORECASE,
)
_MONEY = re.compile(r"(?<![\w.])(?:\$|USD\s*)?([0-9][0-9,]*\.[0-9]{2})(?![\d.])")
_PAYMENT_FIELDS = (
    "invoice_number", "vendor_name", "vendor_email", "due_date", "po_number",
    "amount_due_cents", "currency", "remit_account_number",
)
_STAGES = {"pdf_text", "gemini", "schema", "span_verification", "policy", "nessie", "cancelled"}
_LOGGER = logging.getLogger(__name__)


class _InvoiceProcessingError(RuntimeError):
    """Stage-aware safe failure; original service exception text is never retained."""

    def __init__(self, stage: str, summary: str, cause_type: str = "InvoiceProcessingError"):
        self.stage = stage
        self.safe_summary = summary
        self.cause_type = cause_type
        super().__init__(summary)


def _text(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _account(value: Any) -> str:
    return re.sub(r"[\s-]", "", str(value))


def _span_verified(span: SourceSpan | None, document: DocumentText) -> bool:
    """Never accept an adapter's true flag without checking the original slice."""
    if span is None or not span.span_verified or span.document_id != document.document_id:
        return False
    for page in document.pages:
        if page.page != span.page:
            continue
        for line in page.lines:
            if line.line == span.line:
                return (
                    0 <= span.char_start < span.char_end <= len(line.text)
                    and line.text[span.char_start:span.char_end] == span.quote
                )
    return False


def _field_verified(field: ExtractedField | None, document: DocumentText) -> bool:
    if field is None or not _span_verified(field.span, document):
        return False
    if field.name == "amount_due_cents":
        if type(field.value) is not int:
            return False
        for match in _MONEY.finditer(field.span.quote):
            try:
                if Decimal(match[1].replace(",", "")) * 100 == field.value:
                    return True
            except InvalidOperation:
                continue
        return False
    if not isinstance(field.value, str) or not field.value:
        return False
    if field.name == "remit_account_number":
        return bool(_account(field.value)) and _account(field.value) in _account(field.span.quote)
    return _text(field.value) in _text(field.span.quote)


def _payment_inputs_verified(document, extraction, invoice) -> bool:
    fields = {field.name: field for field in extraction.fields}
    return (
        all(_field_verified(fields.get(name), document) for name in _PAYMENT_FIELDS)
        and all(_span_verified(span, document) for span in extraction.instruction_like_spans)
        and _text(fields["invoice_number"].value) == _text(invoice.invoice_number)
    )


@dataclass
class _V0Result:
    vendor: dict[str, Any] | None
    amount_cents: int | None
    requested_account: str
    checks: list[PolicyCheck]
    reasons: list[str]


def temporary_v0_check(store, document: DocumentText, extraction: FieldsExtractedPayload,
                       invoice: InvoiceSummary, instruction: str) -> _V0Result:
    """TEMPORARY v0: replace this entire gate with the B6 deterministic engine.

    This gate is deliberately narrow. It cannot interpret arbitrary trusted
    instructions, so those require a hold until the planner/policy features land.
    It never consults scoring metadata and never follows document instructions.
    """
    fields = {field.name: field for field in extraction.fields}
    verified = _payment_inputs_verified(document, extraction, invoice)

    name = fields.get("vendor_name")
    email = fields.get("vendor_email")
    vendor = None
    if name and email and _field_verified(name, document) and _field_verified(email, document):
        domain = str(email.value).rsplit("@", 1)[-1].casefold()
        candidates = [row for row in store.list_records("vendors") if (
            _text(row["name"]) == _text(name.value)
            and row["email_domain"].casefold() == domain
            and row["vendor_id"] == invoice.vendor_id
        )]
        vendor = candidates[0] if len(candidates) == 1 else None
    account_field = fields.get("remit_account_number")
    requested = str(account_field.value) if account_field and account_field.value is not None else ""
    on_file = str(vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))) if vendor else ""
    account_matches = bool(vendor and _account(requested) == _account(on_file))

    po_field = fields.get("po_number")
    orders = []
    if vendor and po_field and _field_verified(po_field, document):
        orders = [row for row in store.list_records("purchase_orders") if (
            row["vendor_id"] == vendor["vendor_id"] and row["status"] == "open"
            and row["purchase_order_id"] in vendor["purchase_order_ids"]
            and _text(row["po_number"]) == _text(po_field.value)
        )]
    amount_field = fields.get("amount_due_cents")
    amount = amount_field.value if amount_field and type(amount_field.value) is int else None
    amount_matches = bool(len(orders) == 1 and amount is not None and amount > 0 and amount == orders[0]["amount_cents"])
    amount_matches = amount_matches and bool(fields.get("currency") and fields["currency"].value == "USD")

    routing_conflict = False
    for span in extraction.instruction_like_spans:
        if not _span_verified(span, document):
            continue
        numbers = [_account(match[1]) for match in _ACCOUNT_IN_TEXT.finditer(span.quote)]
        off_master = [number for number in numbers if number != _account(on_file)]
        if off_master:
            routing_conflict = True
            # Review metadata records the requested redirection from its verified
            # instruction span. This is never used as the payment destination.
            requested = off_master[0]
        if _ROUTING_DIRECTIVE.search(span.quote) and _PAYMENT_WORD.search(span.quote):
            routing_conflict = True

    reasons = []
    if not verified:
        reasons.append("UNVERIFIED_SOURCE")
    if vendor is None:
        reasons.append("UNKNOWN_VENDOR")
    if not account_matches or routing_conflict:
        reasons.append("BANK_DETAILS_MISMATCH")
    if not amount_matches:
        reasons.append("AMOUNT_MISMATCH")
    if instruction != DEFAULT_INSTRUCTION:
        reasons.append("POLICY_NOT_IMPLEMENTED")
    due = fields.get("due_date")
    try:
        due_date = date.fromisoformat(str(due.value)) if due else None
    except ValueError:
        due_date = None
    if due_date is None or due_date > datetime.now(timezone.utc).date() + timedelta(days=30):
        reasons.append("POLICY_NOT_IMPLEMENTED")
    checks = [
        PolicyCheck(code="VENDOR_KNOWN", passed=vendor is not None,
                    message="Vendor identity is bound to the trusted master.", evidence_node_ids=[]),
        PolicyCheck(code="SPAN_VERIFIED", passed=verified,
                    message="Payment inputs must match verified original source slices and values.", evidence_node_ids=[]),
        PolicyCheck(code="PAYEE_ON_FILE", passed=account_matches and not routing_conflict,
                    message="Remittance must match the master without conflicting routing instructions.", evidence_node_ids=[]),
        PolicyCheck(code="AMOUNT_MATCHES_PO", passed=amount_matches,
                    message="USD total must equal an open purchase order belonging to this vendor.", evidence_node_ids=[]),
        PolicyCheck(code="INSTRUCTIONS_IN_DOCUMENT", passed=not routing_conflict,
                    message="Automated payment redirection requires human review.", evidence_node_ids=[]),
    ]
    return _V0Result(vendor, amount, requested, checks, list(dict.fromkeys(reasons)))


class RunManager:
    """Keep background runs alive independently of the initiating HTTP request."""

    def __init__(self, store, bus, adapters, settings):
        self.store = store
        self.bus = bus
        self.adapters = adapters
        self.settings = settings
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False

    @property
    def mode(self):
        return "offline" if "offline" in {self.adapters.extractor.mode, self.adapters.bank.mode} else "live"

    def start(self, request: RunRequest) -> Run:
        if self._closed:
            raise HTTPException(503, "Run manager is shutting down")
        if not request.lanes or len(set(request.lanes)) != len(request.lanes):
            raise HTTPException(422, "At least one distinct lane is required")
        record = self.store.get_record("batches", request.batch_id)
        if record is None:
            raise HTTPException(404, "Batch not found")
        batch = Batch.model_validate(record)
        lanes = ["countersign", *(["unguarded"] if "unguarded" in request.lanes else [])]
        run = Run(
            run_id=f"run-{uuid4().hex}", batch_id=batch.batch_id,
            instruction=request.instruction if request.instruction is not None else DEFAULT_INSTRUCTION,
            created_at=datetime.now(timezone.utc),
            lanes=[RunLane(lane=lane, status="running" if lane == "countersign" else "not_implemented") for lane in lanes],
        )
        self.store.save_run(run)
        self._tasks[run.run_id] = asyncio.create_task(self._execute(run, batch), name=run.run_id)
        return run

    async def wait(self, run_id: str) -> Run:
        if run_id in self._tasks:
            await self._tasks[run_id]
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._tasks.items())
        for _, task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for run_id, _ in tasks:
            run = self.store.get_run(run_id)
            if run is None or not any(lane.lane == "countersign" and lane.status == "running" for lane in run.lanes):
                continue
            # A task cancelled before its coroutine first executes cannot enter
            # that coroutine's finally block. Finalize that accepted run here.
            if not self.store.list_events(run_id):
                await self._started(run)
            await self._event(run_id, "error", {
                "message": "Run interrupted before processing began (run).", "recoverable": False,
            })
            await self._finish(run, LaneTally(
                paid_count=0, paid_cents=0, held_count=0, blocked_count=0,
                not_on_file_count=0, not_on_file_cents=0,
            ), failed=True)
        self._tasks.clear()

    async def _event(self, run_id: str, event_type: str, payload: dict, *, lane="countersign", mode=None):
        return await self.bus.publish(run_id, {
            "type": event_type, "lane": lane, "mode": mode or self.mode, "payload": payload,
        })

    async def _started(self, run: Run) -> None:
        await self._event(run.run_id, "run.started", {
            "batch_id": run.batch_id, "lanes": [lane.lane for lane in run.lanes],
            "instruction": run.instruction,
            "models": {"extractor": getattr(self.adapters.extractor, "model", self.settings.extractor_model),
                       "planner": "not_implemented", "unguarded": "not_implemented"},
        }, lane="system")

    async def _finish(self, run: Run, tally: LaneTally, *, failed: bool) -> None:
        final_status = "failed" if failed else "completed"
        run.lanes = [RunLane(lane=lane.lane, status=final_status if lane.lane == "countersign" else "not_implemented") for lane in run.lanes]
        run.tallies = RunTallies(countersign=tally)
        self.store.save_run(run)
        await self._event(run.run_id, "lane.status", {"status": final_status})
        await self._event(run.run_id, "run.completed", {"tallies": run.tallies.model_dump(mode="python")}, lane="system")

    async def _execute(self, run: Run, batch: Batch) -> None:
        tally = LaneTally(paid_count=0, paid_cents=0, held_count=0, blocked_count=0,
                          not_on_file_count=0, not_on_file_cents=0)
        failed = False
        try:
            await self._started(run)
            for lane in run.lanes:
                await self._event(run.run_id, "lane.status", {
                    "status": lane.status,
                    **({"detail": "The unguarded lane is not implemented in B3."} if lane.lane == "unguarded" else {}),
                }, lane=lane.lane)
            for invoice in batch.invoices:
                try:
                    await self._invoice(run, invoice, tally)
                except asyncio.CancelledError:
                    await self._processing_failed(run, invoice, tally, _InvoiceProcessingError(
                        "cancelled", "invoice processing interrupted; reconcile any persisted bank attempts (cancelled)",
                    ))
                    raise
                except Exception as error:
                    failed = True
                    await self._processing_failed(run, invoice, tally, error)
        except asyncio.CancelledError:
            failed = True
            await self._event(run.run_id, "error", {
                "message": "Run interrupted during shutdown (run); inspect persisted bank attempts before retrying.",
                "recoverable": False,
            })
        except Exception as error:
            failed = True
            _LOGGER.error("Run processing failed run_id=%s stage=run cause=%s", run.run_id, type(error).__name__)
            await self._event(run.run_id, "error", {
                "message": f"Run processing failed (run: {type(error).__name__}).", "recoverable": False,
            })
        finally:
            await self._finish(run, tally, failed=failed)

    async def _invoice(self, run: Run, invoice: InvoiceSummary, tally: LaneTally) -> None:
        progress = {"stage": "pdf_text"}
        try:
            await self._process_invoice(run, invoice, tally, progress)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise self._classify_failure(error, progress["stage"]) from None

    def _classify_failure(self, error: Exception, stage: str) -> _InvoiceProcessingError:
        if isinstance(error, _InvoiceProcessingError):
            return error
        summary = None
        if isinstance(error, ExtractorError):
            candidate_stage = getattr(error, "stage", stage)
            stage = candidate_stage if candidate_stage in _STAGES else stage
            summary = getattr(error, "safe_summary", None)
        if summary is None and isinstance(error, ValidationError):
            summary = f"invoice response failed schema validation ({stage})"
        if summary is None and isinstance(error, (TimeoutError, httpx.TimeoutException)):
            summary = (
                "extraction timed out after 45 s (gemini)" if stage == "gemini"
                else "payment timed out after 15 s (nessie)" if stage == "nessie"
                else f"invoice processing timed out ({stage})"
            )
        if summary is None and isinstance(error, BankError):
            # These exact adapter messages contain no service body or URL. The
            # fallback never reflects arbitrary BankError text supplied by a SDK.
            if str(error) in {"Nessie request timed out.", "request timed out after 15 s (nessie)"}:
                summary = "payment timed out after 15 s (nessie)"
            elif match := re.fullmatch(r"Nessie returned HTTP ([1-5][0-9]{2})\.", str(error)):
                summary = f"payment request failed (nessie HTTP {match[1]})"
        if summary is None:
            summary = f"invoice processing failed ({stage}: {type(error).__name__})"
        if f"({stage}" not in summary:
            summary += f" ({stage})"
        if stage == "nessie":
            summary += "; reconcile persisted bank attempts before retrying"
        # Adapter summaries are constructed from fixed messages/status codes.
        # Defensively redact configured credentials even if an adapter regresses.
        for name in ("gemini_api_key", "nessie_api_key", "anthropic_api_key", "bitdeer_api_key", "elastic_api_key"):
            credential = getattr(self.settings, name, None)
            secret = credential.get_secret_value() if credential is not None else ""
            if secret:
                summary = summary.replace(secret, "[REDACTED]")
        return _InvoiceProcessingError(stage, summary, type(error).__name__)

    async def _processing_failed(self, run, invoice, tally, error) -> None:
        failure = self._classify_failure(error, "policy")
        _LOGGER.warning(
            "Invoice processing held run_id=%s invoice_id=%s stage=%s cause=%s summary=%s",
            run.run_id, invoice.invoice_id, failure.stage, failure.cause_type, failure.safe_summary,
        )
        await self._event(run.run_id, "error", {
            "message": failure.safe_summary, "recoverable": True, "invoice_id": invoice.invoice_id,
        })
        vendor = self.store.get_record("vendors", invoice.vendor_id) or {}
        approval_id = f"approval-{uuid4().hex}"
        decision = PolicyDecision(
            decision_id=f"decision-{uuid4().hex}", run_id=run.run_id, invoice_id=invoice.invoice_id,
            outcome="HOLD", reason_codes=["UNVERIFIED_SOURCE"], approval_id=approval_id,
            checks=[PolicyCheck(
                code="SPAN_VERIFIED", passed=False,
                message=f"Processing failed at {failure.stage}; the payment requires human review.",
                evidence_node_ids=[],
            )],
        )
        self.store.save_record("decisions", decision.decision_id, decision)
        await self._event(run.run_id, "policy.decision", {"decision": decision.model_dump(mode="python")})
        approval = Approval(
            approval_id=approval_id, run_id=run.run_id, invoice_id=invoice.invoice_id,
            invoice_number=invoice.invoice_number, vendor_name=vendor.get("name", invoice.vendor_name),
            amount_cents=invoice.amount_cents, requested_account_display="",
            account_on_file_display=str(vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))),
            reason_codes=["UNVERIFIED_SOURCE"], status="pending", created_at=datetime.now(timezone.utc),
            requested_by="person-ap-agent", required_role="ap_manager",
        )
        self.store.save_record("approvals", approval_id, approval)
        tally.held_count += 1
        await self._event(run.run_id, "payment.held", {
            "invoice_id": invoice.invoice_id, "approval_id": approval_id, "reason_codes": ["UNVERIFIED_SOURCE"],
        })

    async def _process_invoice(self, run, invoice, tally, progress) -> None:
        record = self.store.get_record("documents", invoice.document_id)
        if record is None:
            raise ValueError("Missing stored document")
        document = DocumentText.model_validate(record["text"])
        if document.invoice_id != invoice.invoice_id:
            raise ValueError("Document invoice identity mismatch")
        await self._event(run.run_id, "document.loaded", {
            "invoice_id": invoice.invoice_id, "document_id": document.document_id,
            "pages": len(document.pages), "line_count": sum(len(page.lines) for page in document.pages),
        })
        progress["stage"] = "gemini"
        extracted = await self.adapters.extractor.extract(document)
        progress["stage"] = "schema"
        extraction = FieldsExtractedPayload.model_validate(extracted)
        if extraction.invoice_id != invoice.invoice_id:
            raise ValueError("Extraction invoice identity mismatch")
        extraction_id = f"{run.run_id}:{invoice.invoice_id}"
        self.store.save_record("extractions", extraction_id, {
            "extraction_id": extraction_id, "run_id": run.run_id,
            **extraction.model_dump(mode="json"),
        })
        await self._event(run.run_id, "fields.extracted", extraction.model_dump(mode="python"), mode=self.adapters.extractor.mode)
        progress["stage"] = "span_verification"
        if not _payment_inputs_verified(document, extraction, invoice):
            raise _InvoiceProcessingError("span_verification", "payment source verification failed (span_verification)")
        progress["stage"] = "policy"
        check = temporary_v0_check(self.store, document, extraction, invoice, run.instruction)
        approval_id = f"approval-{uuid4().hex}" if check.reasons else None
        decision = PolicyDecision(
            decision_id=f"decision-{uuid4().hex}", run_id=run.run_id, invoice_id=invoice.invoice_id,
            outcome="HOLD" if check.reasons else "ALLOW", checks=check.checks, reason_codes=check.reasons,
            **({"approval_id": approval_id} if approval_id else {}),
        )
        self.store.save_record("decisions", decision.decision_id, decision)
        await self._event(run.run_id, "policy.decision", {"decision": decision.model_dump(mode="python")})
        if check.reasons:
            vendor = check.vendor or {}
            approval = Approval(
                approval_id=approval_id, run_id=run.run_id, invoice_id=invoice.invoice_id,
                invoice_number=invoice.invoice_number, vendor_name=vendor.get("name", invoice.vendor_name),
                amount_cents=check.amount_cents if check.amount_cents is not None else invoice.amount_cents,
                requested_account_display=check.requested_account,
                account_on_file_display=str(vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))),
                reason_codes=check.reasons, status="pending", created_at=datetime.now(timezone.utc),
                requested_by="person-ap-agent", required_role="ap_manager",
            )
            self.store.save_record("approvals", approval.approval_id, approval)
            tally.held_count += 1
            await self._event(run.run_id, "payment.held", {
                "invoice_id": invoice.invoice_id, "approval_id": approval.approval_id, "reason_codes": check.reasons,
            })
            return
        progress["stage"] = "nessie"
        directory = [row for row in self.store.list_records("bank_directory") if row["mode"] == self.adapters.bank.mode]
        companies = [row for row in directory if row["role"] == "company"]
        on_file = check.vendor.get("account_on_file_display", check.vendor["remit_account_number"])
        payees = [row for row in directory if row["display_number"] == on_file]
        if len(companies) != 1 or len(payees) != 1:
            raise ValueError("Bank directory is not seeded for this payment")
        payment_result = await self.adapters.bank.transfer(
            from_account_id=companies[0]["account_id"], to_account_id=payees[0]["account_id"],
            amount_cents=check.amount_cents,
            description=f"Countersign {invoice.invoice_number} ({invoice.invoice_id})",
        )
        # The current public v1 Payment has no detail field. Both paired bank IDs
        # remain in the adapter's durable record until the contract owner adds it.
        payment = Payment.model_validate(payment_result.model_dump(mode="python", exclude={"detail"}))
        if (not payment.transfer_id or payment.bank_mode != self.adapters.bank.mode
                or payment.from_account_id != companies[0]["account_id"]
                or payment.to_account_id != payees[0]["account_id"]
                or payment.amount_cents != check.amount_cents or not payment.payee_on_file
                or payment.transfer_status.casefold() in _FAILURE_STATUSES):
            raise ValueError("Bank payment result needs reconciliation")
        saved = self.store.get_record("transfers", payment.transfer_id) or payment_result.model_dump(mode="json")
        self.store.save_record("transfers", payment.transfer_id, {
            **saved, "run_id": run.run_id, "invoice_id": invoice.invoice_id,
        })
        await self._event(run.run_id, "payment.executed", {
            "invoice_id": invoice.invoice_id, "via": "policy", "payment": payment.model_dump(mode="python"),
        }, mode=self.adapters.bank.mode)
        # Contract paid_* tallies count accepted submissions, preserving the real
        # transfer_status (including pending); they do not claim bank settlement.
        tally.paid_count += 1
        tally.paid_cents += payment.amount_cents
