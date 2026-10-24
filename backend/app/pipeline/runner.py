"""Quarantine extraction, plan over handles, and interpret with provenance."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
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
    InvoiceSummary, LaneTally, PolicyCheck, PolicyDecision, Run,
    RunLane, RunRequest, RunTallies, SourceSpan,
)
from app.pipeline.extractor import ExtractorError
from app.pipeline.spans import field_source_verified as _field_verified, span_source_verified as _span_verified
from app.pipeline.planner import propose
from app.pipeline.unguarded_agent import UnguardedAgent
from app.pipeline.unguarded_tools import UnguardedTools
from app.pipeline.payments import DuplicatePaymentError, PaymentExecutor, refresh_run_tallies
from app.security.graph import build_graph
from app.security.labels import DocumentSpan, Labeled
from app.security.policy import PolicyEngine
from app.security.handles import HandleTable
from app.security.interpreter import Interpreter
from app.security.leak_guard import ProvenanceLeakError
from app.world.batches import supporting_documents_for_invoice
from app.world.bank_changes import remittance_field


DEFAULT_INSTRUCTION = (
    "Pay every invoice in this batch that is due within 30 days and matches an "
    "open purchase order. Pay only to accounts on file; anything else needs "
    "human approval."
)
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


def _payment_inputs_verified(document, extraction, invoice) -> bool:
    fields = {field.name: field for field in extraction.fields}
    return (
        all(_field_verified(fields.get(name), document) for name in _PAYMENT_FIELDS)
        and all(_span_verified(span, document) for span in extraction.instruction_like_spans)
        and _text(fields["invoice_number"].value) == _text(invoice.invoice_number)
    )


class RunManager:
    """Keep background runs alive independently of the initiating HTTP request."""

    def __init__(self, store, bus, adapters, settings, payment_executor=None, *, dry_run=False, as_of=None):
        self.store = store
        self.bus = bus
        self.adapters = adapters
        self.settings = settings
        if dry_run and adapters.bank.mode != "dry_run":
            raise ValueError("Evaluation requires a capability-denied dry-run bank")
        self.dry_run = dry_run
        self.as_of = as_of
        self.payments = payment_executor or PaymentExecutor(store, bus, adapters.bank)
        self.policy = PolicyEngine(store, amount_tolerance_cents=settings.policy_amount_tolerance_cents,
                                   autonomy_limit_cents=settings.agent_autonomy_limit_cents, today=as_of)
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False

    @property
    def mode(self):
        if self.dry_run:
            return "offline" if "offline" in {self.adapters.extractor.mode, self.adapters.planner.mode} else "dry_run"
        return "offline" if "offline" in {self.adapters.extractor.mode, self.adapters.planner.mode, self.adapters.bank.mode} else "live"

    def start(self, request: RunRequest) -> Run:
        if self._closed:
            raise HTTPException(503, "Run manager is shutting down")
        if not request.lanes or len(set(request.lanes)) != len(request.lanes):
            raise HTTPException(422, "At least one distinct lane is required")
        record = self.store.get_record("batches", request.batch_id)
        if record is None:
            raise HTTPException(404, "Batch not found")
        batch = Batch.model_validate(record)
        lanes = list(request.lanes)
        run = Run(
            run_id=f"run-{uuid4().hex}", batch_id=batch.batch_id,
            instruction=request.instruction if request.instruction is not None else DEFAULT_INSTRUCTION,
            created_at=datetime.now(timezone.utc),
            lanes=[RunLane(lane=lane, status="running") for lane in lanes],
            unguarded_mode=request.unguarded_mode or "ap",
        )
        self.store.save_run(run)
        self.store.save_record("planner_transcripts", run.run_id, {
            "run_id": run.run_id, "calls": [],
            "leak_guard": {"strings_checked": 0, "canaries_checked": 0, "matches": 0},
        })
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
            if run is None or not any(lane.status == "running" for lane in run.lanes):
                continue
            # A task cancelled before its coroutine first executes cannot enter
            # that coroutine's finally block. Finalize that accepted run here.
            if not self.store.list_events(run_id):
                await self._started(run)
            await self._event(run_id, "error", {
                "message": "Run interrupted before processing began (run).", "recoverable": False,
            }, lane="system")
            await self._finish(run, LaneTally(
                paid_count=0, paid_cents=0, held_count=0, blocked_count=0,
                not_on_file_count=0, not_on_file_cents=0,
            ), failed=True)
        self._tasks.clear()

    async def _event(self, run_id: str, event_type: str, payload: dict, *, lane="countersign", mode=None,
                     provider=None, model=None):
        run = self.store.get_run(run_id)
        if mode is None and lane == "system":
            run = self.store.get_run(run_id)
            active_modes = [self._unguarded_mode if item.lane == "unguarded" else self.mode
                            for item in run.lanes] if run is not None else [self.mode]
            mode = "offline" if "offline" in active_modes else "dry_run" if "dry_run" in active_modes else "live"
        return await self.bus.publish(run_id, {
            "type": event_type, "lane": lane, "mode": mode or (self._unguarded_mode if lane == "unguarded" else self.mode),
            **({"provider": getattr(self.adapters.agent, "provider", "offline" if self.adapters.agent.mode == "offline" else "unknown"),
                "model": self.adapters.agent.model,
                "unguarded_mode": (run.unguarded_mode if run else None) or "ap"}
               if lane == "unguarded" else
               {key: value for key, value in {"provider": provider, "model": model}.items() if value is not None}),
            "payload": payload,
        })

    async def _started(self, run: Run) -> None:
        await self._event(run.run_id, "run.started", {
            "batch_id": run.batch_id, "lanes": [lane.lane for lane in run.lanes],
            "instruction": run.instruction,
            "models": {"extractor": getattr(self.adapters.extractor, "model", self.settings.extractor_model),
                       "planner": self.adapters.planner.model, "unguarded": self.adapters.agent.model},
        }, lane="system")

    @property
    def _unguarded_mode(self):
        if self.settings.unguarded_dry_run:
            return "dry_run"
        return "offline" if "offline" in {self.adapters.agent.mode, self.adapters.bank.mode} else "live"

    async def _finish(self, run: Run, tally=None, *, failed=False, statuses=None) -> None:
        statuses = statuses or {lane.lane: "failed" if failed else "completed" for lane in run.lanes}
        run.lanes = [RunLane(lane=lane.lane, status=statuses[lane.lane]) for lane in run.lanes]
        self.store.save_run(run)
        refreshed = refresh_run_tallies(self.store, run.run_id) if not self.dry_run else None
        if self.dry_run:
            decisions = {row["invoice_id"]: row for row in self.store.list_records("decisions", run_id=run.run_id)}
            tallies = {}
            for lane in run.lanes:
                intents = [item for item in self.adapters.bank.intents
                           if item["run_id"] == run.run_id and item["lane"] == lane.lane]
                tallies[lane.lane] = LaneTally(paid_count=len(intents),
                    paid_cents=sum(item["amount_cents"] for item in intents),
                    held_count=sum(item["outcome"] == "HOLD" for item in decisions.values()) if lane.lane == "countersign" else 0,
                    blocked_count=sum(item["outcome"] == "BLOCK" for item in decisions.values()) if lane.lane == "countersign" else 0,
                    not_on_file_count=sum(not item.get("payee_on_file", True) for item in intents),
                    not_on_file_cents=sum(item["amount_cents"] for item in intents if not item.get("payee_on_file", True)))
            run.tallies = RunTallies(**tallies)
            self.store.save_run(run)
        if refreshed is not None:
            run.tallies = refreshed.tallies
        for lane in run.lanes:
            await self._event(run.run_id, "lane.status", {"status": lane.status}, lane=lane.lane)
        await self._event(run.run_id, "run.completed", {"tallies": run.tallies.model_dump(mode="python")}, lane="system")

    async def _execute(self, run: Run, batch: Batch) -> None:
        tasks = {}
        statuses = {lane.lane: "failed" for lane in run.lanes}
        try:
            await self._started(run)
            for lane in run.lanes:
                await self._event(run.run_id, "lane.status", {"status": "running"}, lane=lane.lane)
            for lane in run.lanes:
                execute = self._countersign if lane.lane == "countersign" else self._unguarded
                tasks[lane.lane] = asyncio.create_task(execute(run, batch), name=f"{run.run_id}:{lane.lane}")
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            for lane, result in zip(tasks, results):
                statuses[lane] = "failed" if isinstance(result, BaseException) or result else "completed"
                if isinstance(result, BaseException):
                    await self._event(run.run_id, "error", {
                        "message": f"Lane failed ({type(result).__name__}).", "recoverable": False,
                    }, lane=lane)
        except asyncio.CancelledError:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)
            await self._event(run.run_id, "error", {
                "message": "Run interrupted during shutdown (run).", "recoverable": False,
            }, lane="system")
        except Exception as error:
            await self._event(run.run_id, "error", {
                "message": f"Run failed ({type(error).__name__}).", "recoverable": False,
            }, lane="system")
        finally:
            await self._finish(run, statuses=statuses)

    async def _unguarded(self, run: Run, batch: Batch) -> bool:
        failed = False
        remittance_updates = {}
        for invoice in batch.invoices:
            try:
                record = self.store.get_record("documents", invoice.document_id)
                if record is None:
                    raise ValueError("Missing document")
                document = DocumentText.model_validate(record["text"])
                if document.invoice_id != invoice.invoice_id:
                    raise ValueError("Document identity mismatch")
                supporting = supporting_documents_for_invoice(self.store, batch, invoice.invoice_id)
                await self._event(run.run_id, "document.loaded", {
                    "invoice_id": invoice.invoice_id, "document_id": document.document_id,
                    "pages": len(document.pages), "line_count": sum(len(page.lines) for page in document.pages),
                }, lane="unguarded")
                for letter in supporting:
                    await self._event(run.run_id, "document.loaded", {
                        "invoice_id": invoice.invoice_id, "document_id": letter.document_id,
                        "pages": len(letter.pages), "line_count": sum(len(page.lines) for page in letter.pages),
                    }, lane="unguarded")
                tools = UnguardedTools(self.store, self.bus, self.adapters.bank,
                    run_id=run.run_id, invoice_id=invoice.invoice_id, vendor_id=invoice.vendor_id,
                    dry_run=self.settings.unguarded_dry_run, effective_mode=self._unguarded_mode,
                    provider=getattr(self.adapters.agent, "provider", "offline" if self.adapters.agent.mode == "offline" else "unknown"),
                    model=self.adapters.agent.model, unguarded_mode=run.unguarded_mode or "ap",
                    remittance_updates=remittance_updates)
                result = await UnguardedAgent(self.adapters.agent, tools, self.store, self.bus,
                    max_steps=self.settings.agent_max_steps).run(
                        run_id=run.run_id, instruction=run.instruction, document=document,
                        supporting_documents=supporting, unguarded_mode=run.unguarded_mode or "ap")
                failed = failed or not result.completed
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failed = True
                await self._event(run.run_id, "error", {
                    "message": f"Unguarded invoice processing failed ({type(error).__name__}).",
                    "recoverable": True, "invoice_id": invoice.invoice_id,
                }, lane="unguarded")
        return failed

    async def _countersign(self, run: Run, batch: Batch) -> bool:
        tally = LaneTally(paid_count=0, paid_cents=0, held_count=0, blocked_count=0,
                          not_on_file_count=0, not_on_file_cents=0)
        failed = False
        pending = {invoice.invoice_id: invoice for invoice in batch.invoices}
        documents = []
        table = HandleTable()
        try:
            # Quarantine and authorization are separate boundaries. An
            # available referenced source must be scanned even if its claimed
            # invoice association will subsequently fail validation. Dedup only
            # this scanner input; duplicate routing requests still fail closed.
            quarantined_ids = set()
            for reference in batch.supporting_documents or []:
                record = self.store.get_record("documents", reference.document_id)
                try:
                    source_document = DocumentText.model_validate(record["text"]) if record else None
                except (ValueError, KeyError, TypeError):
                    continue
                if source_document is not None and source_document.document_id not in quarantined_ids:
                    quarantined_ids.add(source_document.document_id)
                    documents.append(source_document)
            supporting_by_invoice = {}
            requested_by_invoice = {}
            for position, invoice in enumerate(batch.invoices, 1):
                progress = {"stage": "span_verification"}
                try:
                    supporting = supporting_documents_for_invoice(self.store, batch, invoice.invoice_id)
                    for letter in supporting:
                        await self._event(run.run_id, "document.loaded", {
                            "invoice_id": invoice.invoice_id, "document_id": letter.document_id,
                            "pages": len(letter.pages), "line_count": sum(len(page.lines) for page in letter.pages),
                        })
                    if len(supporting) > 1:
                        raise ValueError("Ambiguous supporting remittance requests")
                    requested_by_invoice[invoice.invoice_id] = remittance_field(supporting[0]) if supporting else None
                    supporting_by_invoice[invoice.invoice_id] = supporting
                    progress["stage"] = "pdf_text"
                    await self._extract(run, invoice, position, table, documents, progress)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    failed = True
                    failure = self._classify_failure(error, progress["stage"])
                    await self._processing_failed(run, invoice, tally, failure)
                    pending.pop(invoice.invoice_id, None)
            if table.invoices:
                vendors = self.store.list_records("vendors")
                purchase_orders = self.store.list_records("purchase_orders")
                plan = await propose(
                    run_id=run.run_id, instruction=run.instruction, vendors=vendors,
                    purchase_orders=purchase_orders, table=table, documents=documents,
                    adapter=self.adapters.planner, store=self.store,
                )
                # The program uses document aliases; the public event binds its
                # steps back to API invoice identities for graph/event consumers.
                public_plan = plan.model_copy(update={"steps": [
                    step.model_copy(update={"invoice_id": table.invoices[step.invoice_id]})
                    for step in plan.steps
                ]})
                await self._event(run.run_id, "plan.proposed", public_plan.model_dump(mode="python"))
                # Programs produce intents. Only the deterministic policy gate
                # can authorize the payment service to use a trusted destination.
                interpreter = Interpreter(table, vendors, purchase_orders, today=self.as_of)
                result = interpreter.execute(plan.steps)
                for intent in result.intents:
                    invoice = pending[intent.invoice_id]
                    supporting = supporting_by_invoice[invoice.invoice_id]
                    requested = requested_by_invoice[invoice.invoice_id]
                    if requested is not None:
                        # A letter is a requested routing change, not authority
                        # to change the master. Submit its labeled account to
                        # policy so a human reviews the outstanding request.
                        # Keep original extraction and plan intact for audit.
                        span = requested.span
                        intent = replace(intent, to_account=Labeled(requested.value, {
                            DocumentSpan(span.document_id, span.page, span.line,
                                         span.char_start, span.char_end, span.span_verified),
                        }))
                    alias = next(alias for alias, identity in table.invoices.items() if identity == invoice.invoice_id)
                    fields = {handle.name.split(".", 1)[1]: table.values[handle.name]
                              for handle in table.invoice_handles[alias]}
                    number = fields.get("invoice_number", Labeled("", {DocumentSpan(invoice.document_id, 0, 0, 0, 0, False)}))
                    extraction = self.store.get_record("extractions", f"{run.run_id}:{invoice.invoice_id}")
                    flagged = FieldsExtractedPayload.model_validate({
                        key: value for key, value in extraction.items() if key not in {"run_id", "extraction_id"}
                    }).instruction_like_spans
                    decision = self.policy.evaluate(
                        intent, run_id=run.run_id, invoice_number=number,
                        matched_po=interpreter.match_po(alias, intent.vendor),
                        instruction_like_spans=flagged, field_values=fields,
                        trusted_instruction=run.instruction,
                        supporting_documents=supporting,
                    )
                    corroborated = self.policy.corroborate(intent, supporting_documents=supporting)
                    steps = [step for step in public_plan.steps if step.invoice_id == invoice.invoice_id]
                    try:
                        await self._apply_decision(run, invoice, tally, decision, intent=corroborated,
                                                   steps=steps, invoice_number=str(number.value))
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        failed = True
                        await self._processing_failed(run, invoice, tally, self._classify_failure(error, "nessie"))
                    pending.pop(invoice.invoice_id, None)
                for invoice_id, reasons in result.outcomes.items():
                    invoice = pending[invoice_id]
                    await self._hold(run, invoice, tally, reasons,
                                     steps=[step for step in public_plan.steps if step.invoice_id == invoice_id])
                    pending.pop(invoice_id)
        except asyncio.CancelledError:
            failed = True
            await self._event(run.run_id, "error", {
                "message": "Run interrupted during shutdown (run).", "recoverable": False,
            })
        except Exception as error:
            failed = True
            _LOGGER.error("Run processing failed run_id=%s stage=planner cause=%s", run.run_id, type(error).__name__)
            message = (
                "Planner prompt rejected by provenance leak guard (leak_guard: ProvenanceLeakError)."
                if isinstance(error, ProvenanceLeakError) else
                f"Run processing failed (planner: {type(error).__name__})."
            )
            await self._event(run.run_id, "error", {"message": message, "recoverable": False})
        finally:
            # Provider failures, guard failures, cancellation, or unexpected
            # interpreter errors leave a durable hold for every pending invoice.
            for invoice in pending.values():
                await self._hold(run, invoice, tally, ["PLANNER_INVALID"])
        return failed

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
        await self._hold(run, invoice, tally, ["UNVERIFIED_SOURCE"], checks=[PolicyCheck(
            code="SPAN_VERIFIED", passed=False,
            message=f"Processing failed at {failure.stage}; the payment requires human review.",
            evidence_node_ids=[],
        )])

    def _save_graph(self, run, invoice, decision, *, intent=None, steps=(), action_status=None):
        source = self.store.get_record("documents", invoice.document_id)
        try:
            document = DocumentText.model_validate(source["text"]) if source else None
        except (ValueError, KeyError):
            document = None
        if document is None:
            document = DocumentText(document_id=invoice.document_id, invoice_id=invoice.invoice_id, pages=[])
        raw = self.store.get_record("extractions", f"{run.run_id}:{invoice.invoice_id}")
        extraction = FieldsExtractedPayload.model_validate({
            key: value for key, value in raw.items() if key not in {"run_id", "extraction_id"}
        }) if raw else FieldsExtractedPayload(invoice_id=invoice.invoice_id,
            extractor_model=getattr(self.adapters.extractor, "model", self.settings.extractor_model),
            fields=[], handles=[], instruction_like_spans=[])
        # Error holds must remain reviewable even when the supporting document
        # itself was missing, malformed, duplicated or incorrectly associated.
        # This tolerant path only builds evidence; authorization uses the strict
        # loader and parser above before any intent can reach policy or the bank.
        supporting, supporting_fields, references = [], [], []
        batch_record = self.store.get_record("batches", run.batch_id)
        if batch_record is not None:
            batch = Batch.model_validate(batch_record)
            references = [reference for reference in batch.supporting_documents or []
                          if reference.invoice_id == invoice.invoice_id]
            seen = {document.document_id}
            for reference in references:
                if reference.document_id in seen:
                    continue
                seen.add(reference.document_id)
                record = self.store.get_record("documents", reference.document_id)
                try:
                    letter = DocumentText.model_validate(record["text"]) if record else None
                except (ValueError, KeyError, TypeError):
                    continue
                if (letter is None or letter.invoice_id != invoice.invoice_id
                        or letter.document_id != reference.document_id):
                    continue
                supporting.append(letter)
                try:
                    supporting_fields.append(remittance_field(letter).model_copy(update={
                        "name": "bank_change_remit_account_number",
                    }))
                except ValueError:
                    continue
        evidence_complete = len(supporting) == len(references) and len(supporting_fields) == len(supporting)
        graph = build_graph(
            run_id=run.run_id, invoice_id=invoice.invoice_id, document=document, extraction=extraction,
            steps=steps, decision=decision, intent=intent, vendors=self.store.list_records("vendors"),
            purchase_orders=self.store.list_records("purchase_orders"), action_status=action_status,
            supporting_documents=supporting,
            supporting_fields=supporting_fields,
            action_detail={"supporting_evidence_complete": False} if not evidence_complete else None,
        )
        key = f"{run.run_id}:{invoice.invoice_id}"
        self.store.save_record("graphs", key, {"graph_id": key, **graph.model_dump(mode="json")})

    async def _apply_decision(self, run, invoice, tally, decision, *, intent=None, steps=(), invoice_number=None):
        vendor_id = intent.vendor.value.vendor_id if intent and intent.vendor.value is not None else invoice.vendor_id
        vendor = self.store.get_record("vendors", vendor_id) or {}
        amount = intent.amount.value if intent and type(intent.amount.value) is int else invoice.amount_cents
        requested = str(intent.to_account.value) if intent else ""
        if decision.outcome == "HOLD":
            approval_id = f"approval-{uuid4().hex}"
            decision.approval_id = approval_id
            approval = Approval(
                approval_id=approval_id, run_id=run.run_id, invoice_id=invoice.invoice_id,
                invoice_number=invoice_number or invoice.invoice_number,
                vendor_name=vendor.get("name", invoice.vendor_name), amount_cents=amount,
                requested_account_display=requested,
                account_on_file_display=str(vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))),
                reason_codes=decision.reason_codes, status="pending", created_at=datetime.now(timezone.utc),
                requested_by="person-ap-agent", required_role="controller" if amount > 5_000_000 else "ap_manager",
            )
            self.store.save_record("approvals", approval_id, approval)
            self.store.save_record("approval_contexts", approval_id, {
                "approval_id": approval_id, "run_id": run.run_id, "invoice_id": invoice.invoice_id,
                "vendor_id": vendor_id, "invoice_number": approval.invoice_number,
                "session_started_at": self.store.session_started_at.isoformat(), "decision_id": decision.decision_id,
                "mode": self.mode,
            })
        self.store.save_record("decisions", decision.decision_id, decision)
        self._save_graph(run, invoice, decision, intent=intent, steps=steps)
        await self._event(run.run_id, "policy.decision", {"decision": decision.model_dump(mode="python")})
        if decision.outcome == "HOLD":
            tally.held_count += 1
            await self._event(run.run_id, "payment.held", {
                "invoice_id": invoice.invoice_id, "approval_id": decision.approval_id,
                "reason_codes": decision.reason_codes,
            })
        elif decision.outcome == "BLOCK":
            tally.blocked_count += 1
        else:
            if intent is None or not vendor:
                raise ValueError("Authorized intent is missing its trusted vendor")
            trusted_account = str(vendor.get("account_on_file_display", vendor.get("remit_account_number", "")))
            if self.dry_run:
                number = invoice_number or invoice.invoice_number
                if not self.store.claim_session_payment(number, f"dry-run:{run.run_id}:{invoice.invoice_id}"):
                    decision.outcome, decision.reason_codes = "BLOCK", ["DUPLICATE_INVOICE"]
                    for check in decision.checks:
                        if check.code == "NOT_DUPLICATE":
                            check.passed = False
                            check.message = "Another evaluation intent already reserved this invoice number."
                    self.store.save_record("decisions", decision.decision_id, decision)
                    self._save_graph(run, invoice, decision, intent=intent, steps=steps)
                    await self._event(run.run_id, "policy.decision", {"decision": decision.model_dump(mode="python")})
                    tally.blocked_count += 1
                    return
                self.adapters.bank.record_intent(run_id=run.run_id, invoice_id=invoice.invoice_id,
                    invoice_number=number, vendor_id=vendor_id, amount_cents=amount,
                    to_display_number=trusted_account, requested_account=str(intent.to_account.value),
                    memo=str(intent.memo.value), lane="countersign", payee_on_file=True)
                tally.paid_count += 1
                tally.paid_cents += amount
                return
            try:
                payment = await self.payments.execute(
                    run_id=run.run_id, invoice_id=invoice.invoice_id,
                    invoice_number=invoice_number or invoice.invoice_number, vendor_id=vendor_id,
                    amount_cents=amount, to_display_number=trusted_account, via="policy",
                    effective_mode=self.mode,
                )
            except DuplicatePaymentError:
                # The DB reservation closes the race between evaluation and I/O.
                decision.outcome = "BLOCK"
                decision.reason_codes = ["DUPLICATE_INVOICE"]
                for check in decision.checks:
                    if check.code == "NOT_DUPLICATE":
                        check.passed = False
                        check.message = "Another payment already reserved this invoice in this session."
                self.store.save_record("decisions", decision.decision_id, decision)
                self._save_graph(run, invoice, decision, intent=intent, steps=steps)
                await self._event(run.run_id, "policy.decision", {"decision": decision.model_dump(mode="python")})
                tally.blocked_count += 1
                return
            tally.paid_count += 1
            tally.paid_cents += payment.amount_cents
            self._save_graph(run, invoice, decision, intent=intent, steps=steps, action_status="paid")

    async def _hold(self, run, invoice, tally, reasons, *, checks=None, amount=None, requested_account="", steps=()):
        decision = PolicyDecision(
            decision_id=f"decision-{uuid4().hex}", run_id=run.run_id, invoice_id=invoice.invoice_id,
            outcome="HOLD", reason_codes=reasons, checks=checks or [],
        )
        await self._apply_decision(run, invoice, tally, decision, steps=steps)

    async def _extract(self, run, invoice, position, table, documents, progress):
        record = self.store.get_record("documents", invoice.document_id)
        if record is None:
            raise ValueError("Missing stored document")
        document = DocumentText.model_validate(record["text"])
        if document.invoice_id != invoice.invoice_id:
            raise ValueError("Document invoice identity mismatch")
        documents.append(document)
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
        # Discard adapter-supplied handle names. Only the server's batch position
        # and fixed extraction field vocabulary can name a planner handle.
        extraction.handles = HandleTable().add_invoice(document, extraction, position)
        extraction_id = f"{run.run_id}:{invoice.invoice_id}"
        self.store.save_record("extractions", extraction_id, {
            "extraction_id": extraction_id, "run_id": run.run_id,
            **extraction.model_dump(mode="json"),
        })
        origin = extraction.extraction_provenance
        await self._event(run.run_id, "fields.extracted", extraction.model_dump(mode="python"),
                          mode=self.adapters.extractor.mode,
                          provider=origin.provider if origin is not None else None,
                          model=origin.model if origin is not None else None)
        progress["stage"] = "span_verification"
        if not _payment_inputs_verified(document, extraction, invoice):
            await self._event(run.run_id, "error", {
                "message": "payment source verification failed (span_verification)",
                "recoverable": True, "invoice_id": invoice.invoice_id,
            })
        table.add_invoice(document, extraction, position)
        return extraction
