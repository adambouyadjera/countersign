"""Read recorded bank-change comparisons without contacting a live service.

Only saved snapshots supply replay data. In particular, today's vendor master,
model settings, bank balances, and filesystem timestamps are never consulted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from app.contract_models import (
    AgentTranscript, Approval, BankChangeReplay, BankChangeReplaySummary, Batch,
    DocumentText, LaneTally, PlannerTranscript, ReplayCounts, ReplayHold, ReplayLane,
    ReplayLanes, ReplayLetter, ReplayModel, ReplayPayment, Run, RunEvent,
)


DEFAULT_BANK_CHANGE_RUN_ID = "run-1b936c2ea67f4236b644609ac7c72089"
DEFAULT_EVIDENCE_DIRECTORY = Path(__file__).resolve().parents[1] / "docs"
_EVENT = TypeAdapter(RunEvent)


class EvidenceUnavailable(RuntimeError):
    """A recorded snapshot is missing, unreadable or internally inconsistent."""


def _object(text):
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _provider(model: str) -> str:
    # Legacy recordings name the model family but predate extraction metadata.
    # This classification never uses the server's current configuration.
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "gemini"
    return "not_recorded"


def _on_file_accounts(transcripts):
    accounts = {}
    for transcript in transcripts:
        for step in transcript.steps:
            if step.kind != "tool_call" or step.tool != "list_vendors":
                continue
            for vendor in _object(step.result).get("vendors", []):
                if vendor.get("remittance_source") == "trusted_vendor_master":
                    accounts.setdefault(vendor["vendor_id"], vendor["account_on_file_display"])
    return accounts


def _letter(batch, transcripts, accounts):
    associations = {item.document_id: item for item in batch.supporting_documents or []}
    invoices = {invoice.invoice_id: invoice for invoice in batch.invoices}
    for transcript in transcripts:
        for step in transcript.steps:
            if step.kind != "message":
                continue
            for candidate in _object(step.text).get("supporting_documents", []):
                association = associations.get(candidate.get("document_id"))
                if association is None:
                    continue
                document = DocumentText.model_validate(candidate)
                if document.invoice_id != association.invoice_id:
                    raise ValueError("Letter association differs from the recorded batch")
                lines = [line.text for page in document.pages for line in page.lines]
                requested = [line.partition(":")[2].strip() for line in lines
                             if line.casefold().startswith("account number:")]
                if len(requested) != 1 or not requested[0]:
                    raise ValueError("Recorded letter account is ambiguous")
                invoice = invoices[association.invoice_id]
                fields = dict(document=document, text="\n".join(lines),
                              vendor_id=association.vendor_id, vendor_name=invoice.vendor_name,
                              requested_account_display=requested[0])
                if association.vendor_id in accounts:
                    fields["account_on_file_display"] = accounts[association.vendor_id]
                return ReplayLetter(**fields)
    return None


def _tally(events, lane):
    payments = [event.payload.payment for event in events
                if event.type == "payment.executed" and event.lane == lane]
    return LaneTally(
        paid_count=len(payments), paid_cents=sum(payment.amount_cents for payment in payments),
        held_count=sum(event.type == "payment.held" and event.lane == lane for event in events),
        blocked_count=sum(event.type == "policy.decision" and event.lane == lane
                          and event.payload.decision.outcome == "BLOCK" for event in events),
        not_on_file_count=sum(not payment.payee_on_file for payment in payments),
        not_on_file_cents=sum(payment.amount_cents for payment in payments if not payment.payee_on_file),
    )


def build_replay(source: dict, *, source_file: str, source_sha256: str) -> BankChangeReplay:
    """Deterministic UI projection; the original complete JSON is also retained."""
    run = Run.model_validate(source["run"])
    batch = Batch.model_validate(source["batch"])
    if run.batch_id != batch.batch_id or batch.kind != "bank_change":
        raise ValueError("Evidence does not describe this bank-change batch")
    events = sorted((_EVENT.validate_python(item) for item in source["events"]), key=lambda item: item.seq)
    if not events or len({event.seq for event in events}) != len(events):
        raise ValueError("Recorded events are missing or duplicated")
    if any(event.run_id != run.run_id for event in events):
        raise ValueError("Recorded events belong to another run")
    transcripts = [AgentTranscript.model_validate(item) for item in source["transcripts"]]
    planner = (PlannerTranscript.model_validate(source["planner_transcript"])
               if source.get("planner_transcript") else None)
    if (planner is not None and planner.run_id != run.run_id) or any(item.run_id != run.run_id for item in transcripts):
        raise ValueError("Recorded transcripts belong to another run")
    invoices = {invoice.invoice_id: invoice for invoice in batch.invoices}
    approvals = {item["approval_id"]: Approval.model_validate(item) for item in source.get("approvals", [])}
    if source.get("pending_approval"):
        approval = Approval.model_validate(source["pending_approval"])
        approvals[approval.approval_id] = approval
    accounts = _on_file_accounts(transcripts)
    for approval in approvals.values():
        invoice = invoices[approval.invoice_id]
        existing = accounts.setdefault(invoice.vendor_id, approval.account_on_file_display)
        if existing != approval.account_on_file_display:
            raise ValueError("Recorded trusted account snapshots disagree")
    letter = _letter(batch, transcripts, accounts)
    limitations = []
    if planner is None:
        limitations.append("The original snapshot did not record a planner transcript or leak-guard counts.")
    if letter is None:
        limitations.append("The original snapshot did not record the letter text; no letter was reconstructed.")
    if source.get("graph_known_issue"):
        limitations.append("Historical graph defect recorded by the original run: " + str(source["graph_known_issue"]))
    complete = bool(run.lanes) and all(lane.status == "completed" for lane in run.lanes)
    complete = complete and any(event.type == "run.completed" for event in events)
    if not complete:
        limitations.append("This is an incomplete or failed recorded run; payments shown are only those actually recorded.")
    unguarded_provider = source.get("provider") or next((item.provider for item in transcripts if item.provider), "not_recorded")
    unguarded_model = source.get("model") or next((item.model for item in transcripts), "not_recorded")
    if any((item.provider is not None and item.provider != unguarded_provider)
           or item.model != unguarded_model for item in transcripts):
        raise ValueError("Recorded unguarded identities disagree")
    primary_planner_model = next((call.model for call in planner.calls), "not_recorded") if planner else next(
        (event.payload.planner_model for event in events if event.type == "plan.proposed"), "not_recorded")
    extraction_models = {}
    for event in events:
        if event.type == "fields.extracted":
            origin = event.payload.extraction_provenance
            model = origin.model if origin else event.payload.extractor_model
            provider = origin.provider if origin else _provider(model)
            extraction_models[(provider, model)] = ReplayModel(provider=provider, model=model)
    lanes = {}
    for lane in ("unguarded", "countersign"):
        payments, holds = [], []
        for event in events:
            if event.lane != lane or event.type not in {"payment.executed", "payment.held"}:
                continue
            invoice = invoices[event.payload.invoice_id]
            identity = dict(invoice_id=invoice.invoice_id, invoice_number=invoice.invoice_number,
                            vendor_id=invoice.vendor_id, vendor_name=invoice.vendor_name, currency=invoice.currency)
            if invoice.vendor_id in accounts:
                identity["account_on_file_display"] = accounts[invoice.vendor_id]
            if event.type == "payment.executed":
                payments.append(ReplayPayment(**event.payload.payment.model_dump(), **identity,
                                              ts=event.ts, via=event.payload.via))
            else:
                extra = {}
                approval = approvals.get(event.payload.approval_id)
                if approval is not None:
                    extra.update(approval=approval, requested_account_display=approval.requested_account_display)
                if letter is not None and invoice.vendor_id == letter.vendor_id:
                    extra["letter_account_display"] = letter.requested_account_display
                holds.append(ReplayHold(**identity, **extra, amount_cents=invoice.amount_cents,
                                        reason_codes=event.payload.reason_codes, ts=event.ts))
        status = next((item.status for item in run.lanes if item.lane == lane), "not_implemented")
        lanes[lane] = ReplayLane(
            lane=lane, provider=unguarded_provider if lane == "unguarded" else _provider(primary_planner_model),
            model=unguarded_model if lane == "unguarded" else primary_planner_model,
            model_role="agent" if lane == "unguarded" else "planner", status=status,
            tally=_tally(events, lane), payments=payments, holds=holds,
            extraction_models=list(extraction_models.values()) if lane == "countersign" else [],
        )
    letter_account = letter.requested_account_display if letter is not None else source.get("letter_account_number")
    counts = dict(unguarded=lanes["unguarded"].tally, countersign=lanes["countersign"].tally,
                  letter_payment_count=sum(payment.to_display_number == letter_account
                                           for payment in lanes["unguarded"].payments))
    if "countersign_held_correctly" in source:
        counts["countersign_held_correctly"] = source["countersign_held_correctly"]
    extra = {}
    if letter is not None:
        extra["letter"] = letter
    if planner is not None:
        extra.update(planner_transcript=planner, leak_guard=planner.leak_guard)
    if source.get("held_invoice_graph"):
        extra["held_invoice_graph"] = source["held_invoice_graph"]
    mode = source.get("unguarded_mode") or run.unguarded_mode
    if mode is not None:
        extra["unguarded_mode"] = mode
    return BankChangeReplay(
        recorded=True, recorded_at=events[-1].ts, source_file=source_file, source_sha256=source_sha256,
        run_id=run.run_id, is_default=run.run_id == DEFAULT_BANK_CHANGE_RUN_ID,
        complete=complete, status=source.get("status") or ("completed" if complete else "incomplete"),
        unguarded_provider=unguarded_provider, unguarded_model=unguarded_model,
        summary=ReplayCounts(**counts), limitations=limitations, run=run, batch=batch,
        lanes=ReplayLanes(**lanes), events=events,
        transcripts=transcripts, evidence=source, **extra,
    )


class BankChangeEvidenceRepository:
    def __init__(self, directory: Path = DEFAULT_EVIDENCE_DIRECTORY):
        self.directory = Path(directory)

    def _runs(self) -> dict[str, BankChangeReplay]:
        runs = {}
        try:
            for path in sorted(self.directory.glob("bank_change_live*.json")):
                if path.name == "bank_change_live_comparison_summary.json" or path.is_symlink():
                    continue
                contents = path.read_bytes()
                source = json.loads(contents)
                # Summary documents are not individual run snapshots.
                if not isinstance(source, dict) or "run" not in source:
                    continue
                replay = build_replay(source, source_file=f"docs/{path.name}",
                                      source_sha256=hashlib.sha256(contents).hexdigest())
                existing = runs.get(replay.run_id)
                if existing is not None:
                    if existing.evidence != replay.evidence:
                        raise ValueError("Conflicting snapshots for one run")
                    # Prefer the model-named copy over the legacy generic name.
                    if "_run-" not in path.name:
                        continue
                runs[replay.run_id] = replay
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise EvidenceUnavailable("Recorded bank-change evidence is unavailable or invalid.") from None
        return runs

    def list_runs(self) -> list[BankChangeReplaySummary]:
        runs = sorted(self._runs().values(), key=lambda item: item.recorded_at, reverse=True)
        runs.sort(key=lambda item: not item.is_default)
        return [BankChangeReplaySummary.model_validate(item.model_dump(include=set(BankChangeReplaySummary.model_fields)))
                for item in runs]

    def get_run(self, run_id: str = "default") -> BankChangeReplay:
        selected = DEFAULT_BANK_CHANGE_RUN_ID if run_id == "default" else run_id
        # The untrusted route value is an index key, never part of a file path.
        try:
            return self._runs()[selected]
        except KeyError:
            raise LookupError("Recorded run not found") from None
