"""Pydantic models for Countersign contract v1.2, including Hyphenate additions.

These models describe transport data only. They do not call external services or
make policy decisions. ``RunEvent`` is the discriminated SSE data union.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, WithJsonSchema, field_validator, model_validator


_TIMESTAMP_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+]00:00)$"
)
_DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"


def _timestamp_input(value: Any) -> str | datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(_TIMESTAMP_PATTERN, value):
        return value
    raise ValueError("timestamp must be a UTC RFC 3339 string or datetime")


def _calendar_date_input(value: Any) -> str | date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(_DATE_PATTERN, value):
        return value
    raise ValueError("date must be a YYYY-MM-DD string or date")


def _utc_timestamp(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return value


Timestamp = Annotated[
    AwareDatetime,
    BeforeValidator(_timestamp_input),
    AfterValidator(_utc_timestamp),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": _TIMESTAMP_PATTERN}),
]
CalendarDate = Annotated[
    date,
    BeforeValidator(_calendar_date_input),
    WithJsonSchema({"type": "string", "format": "date", "pattern": _DATE_PATTERN}),
]
Lane = Literal["unguarded", "countersign"]
EventLane = Literal["unguarded", "countersign", "system"]
Mode = Literal["live", "offline", "dry_run"]
Currency = Literal["USD"]
FieldType = Literal["money", "date", "string", "account_number", "number"]
Trust = Literal["untrusted", "trusted", "corroborated"]
Placement = Literal["visible_note", "hidden_white_text", "footer"]
LaneStatus = Literal["running", "not_implemented", "failed", "completed"]
ApprovalChoice = Literal["pay_vendor_on_file", "approve_new_account", "reject"]
PersonRole = Literal["ap_agent", "ap_manager", "controller", "vendor_admin"]
JobStatus = Literal["queued", "running", "completed", "failed"]
CheckCode = Literal[
    "VENDOR_KNOWN", "SPAN_VERIFIED", "PAYEE_ON_FILE", "AMOUNT_MATCHES_PO",
    "NOT_DUPLICATE", "INSTRUCTIONS_IN_DOCUMENT", "VENDOR_HISTORY",
]
ReasonCode = Literal[
    "BANK_DETAILS_MISMATCH", "AMOUNT_MISMATCH", "UNKNOWN_VENDOR",
    "UNVERIFIED_SOURCE", "DUPLICATE_INVOICE", "PLANNER_INVALID",
    "POLICY_NOT_IMPLEMENTED", "OVER_AUTONOMY_LIMIT",
]
ErrorCode = Literal[
    "validation_error", "not_found", "conflict", "rate_limited",
    "service_unavailable", "internal", "sod_violation",
]
Scalar = StrictStr | StrictInt | StrictFloat


def _omit_unset_default(schema: dict[str, Any]) -> None:
    schema.pop("default", None)


def OptionalField() -> Any:
    """Allow omission while rejecting explicit JSON null for a TS ``?:`` field."""
    return Field(
        default=None,
        exclude_if=lambda value: value is None,
        json_schema_extra=_omit_unset_default,
    )


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: str
    detail: dict[str, Any] = OptionalField()


class ErrorResponse(ContractModel):
    error: ErrorDetail


class ServiceHealth(ContractModel):
    status: Literal["ok", "offline", "missing_key", "error"]
    mode: Literal["live", "offline"]
    detail: str = OptionalField()


class ServicesHealth(ContractModel):
    nessie: ServiceHealth
    elastic: ServiceHealth
    anthropic: ServiceHealth
    gemini: ServiceHealth
    bitdeer: ServiceHealth
    mutator: ServiceHealth = OptionalField()


class Health(ContractModel):
    services: ServicesHealth
    session_started_at: Timestamp


class ResetResponse(ContractModel):
    session_started_at: Timestamp


class BatchRequest(ContractModel):
    kind: Literal["standard", "bank_change"]


class InvoiceSummary(ContractModel):
    invoice_id: str
    invoice_number: str
    vendor_id: str
    vendor_name: str
    amount_cents: StrictInt
    currency: Currency
    due_date: CalendarDate
    document_id: str


class SupportingDocument(ContractModel):
    document_id: str
    invoice_id: str
    vendor_id: str
    document_type: Literal["vendor_bank_change_letter"]


class Batch(ContractModel):
    batch_id: str
    kind: Literal["standard", "attack", "bank_change"]
    created_at: Timestamp
    invoices: list[InvoiceSummary]
    supporting_documents: list[SupportingDocument] = OptionalField()


class DocumentLine(ContractModel):
    line: StrictInt
    text: str


class DocumentPage(ContractModel):
    page: StrictInt
    lines: list[DocumentLine]


class DocumentText(ContractModel):
    document_id: str
    invoice_id: str
    pages: list[DocumentPage]
    document_type: Literal["invoice", "vendor_bank_change_letter"] = OptionalField()


class AttackRequest(ContractModel):
    vendor_id: str
    attack_text: str = Field(min_length=1, max_length=600)
    placement: Placement

    @field_validator("attack_text", mode="before")
    @classmethod
    def clean_plain_text(cls, value):
        if isinstance(value, str):
            from app.world.attack_input import normalize_attack_text
            return normalize_attack_text(value)
        return value


class Attack(AttackRequest):
    """The named Attack schema uses the contract's only defined attack shape."""


class RunRequest(ContractModel):
    batch_id: str
    lanes: list[Lane]
    instruction: str = OptionalField()
    unguarded_mode: Literal["ap", "ap_with_vendor_updates"] = OptionalField()


class LaneTally(ContractModel):
    paid_count: StrictInt
    paid_cents: StrictInt
    held_count: StrictInt
    blocked_count: StrictInt
    not_on_file_count: StrictInt
    not_on_file_cents: StrictInt


class RunLane(ContractModel):
    lane: Lane
    status: LaneStatus


class RunTallies(ContractModel):
    unguarded: LaneTally = OptionalField()
    countersign: LaneTally = OptionalField()


class Run(ContractModel):
    run_id: str
    batch_id: str
    instruction: str
    created_at: Timestamp
    lanes: list[RunLane]
    tallies: RunTallies = OptionalField()
    unguarded_mode: Literal["ap", "ap_with_vendor_updates"] = OptionalField()


class SourceSpan(ContractModel):
    document_id: str
    page: StrictInt
    line: StrictInt
    char_start: StrictInt
    char_end: StrictInt
    quote: str
    span_verified: StrictBool


class ExtractedField(ContractModel):
    name: str
    value: Scalar | None
    type: FieldType
    span: SourceSpan | None
    trust: Literal["untrusted"]


class Handle(ContractModel):
    name: str
    type: FieldType
    trust: Trust
    span_verified: StrictBool


class PlanArg(ContractModel):
    kind: Literal["handle", "step", "trusted", "literal"]
    ref: str = OptionalField()
    value: Scalar = OptionalField()


class PlanStep(ContractModel):
    id: str
    invoice_id: str
    op: Literal["resolve_vendor", "match_po", "due_within", "pay", "hold", "skip"]
    args: dict[str, PlanArg]


class PolicyCheck(ContractModel):
    code: CheckCode
    passed: StrictBool
    message: str
    evidence_node_ids: list[str]


class PolicyDecision(ContractModel):
    decision_id: str
    run_id: str
    invoice_id: str
    outcome: Literal["ALLOW", "HOLD", "BLOCK"]
    checks: list[PolicyCheck]
    reason_codes: list[ReasonCode]
    approval_id: str = OptionalField()


class Payment(ContractModel):
    transfer_id: str
    transfer_status: str
    from_account_id: str
    to_account_id: str
    to_display_number: str
    payee_label: str
    payee_on_file: StrictBool
    amount_cents: StrictInt
    bank_mode: Mode


class GraphNode(ContractModel):
    id: str
    kind: Literal["span", "field", "handle", "trusted_record", "plan_step", "policy_check", "action"]
    label: str
    trust: Literal["trusted", "untrusted", "corroborated", "none"]
    status: Literal["passed", "failed", "paid", "held", "blocked", "approved", "rejected"] = OptionalField()
    span: SourceSpan = OptionalField()
    detail: dict[str, Any] = OptionalField()


class GraphEdge(ContractModel):
    id: str
    source: str
    target: str
    kind: Literal["derived_from", "corroborated_by", "checked_by", "decided"]
    trust: Trust


class ProvenanceGraph(ContractModel):
    run_id: str
    invoice_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class PlannerCall(ContractModel):
    call_id: str
    model: str
    prompt: str
    response: str
    prompt_hash: str


class LeakGuard(ContractModel):
    strings_checked: StrictInt
    canaries_checked: StrictInt
    matches: StrictInt


class PlannerTranscript(ContractModel):
    run_id: str
    calls: list[PlannerCall]
    leak_guard: LeakGuard


class AgentTranscriptStep(ContractModel):
    kind: Literal["message", "tool_call"]
    text: str = OptionalField()
    tool: str = OptionalField()
    args: dict[str, Any] = OptionalField()
    result: str = OptionalField()


class AgentTranscript(ContractModel):
    run_id: str
    invoice_id: str
    provider: str = OptionalField()
    model: str
    unguarded_mode: Literal["ap", "ap_with_vendor_updates"] = OptionalField()
    steps: list[AgentTranscriptStep]


class ApprovalDecision(ContractModel):
    decision: ApprovalChoice
    actor_id: str
    note: str = OptionalField()
    confirm_last4: str = OptionalField()


class ApprovalResolution(ContractModel):
    decision: ApprovalChoice
    note: str = OptionalField()
    resolved_at: Timestamp
    transfer_id: str = OptionalField()


class Approval(ContractModel):
    approval_id: str
    run_id: str
    invoice_id: str
    invoice_number: str
    vendor_name: str
    amount_cents: StrictInt
    requested_account_display: str
    account_on_file_display: str
    reason_codes: list[ReasonCode]
    status: Literal["pending", "resolved"]
    created_at: Timestamp
    requested_by: str
    required_role: PersonRole
    resolution: ApprovalResolution = OptionalField()


class SessionTransfer(ContractModel):
    transfer_id: str
    amount_cents: StrictInt
    from_label: str
    ts: Timestamp


class AccountBalance(ContractModel):
    role: Literal["company", "vendor", "attacker"]
    label: str
    account_id: str
    display_number: str
    balance_cents: StrictInt
    received_this_session_cents: StrictInt
    transfers_this_session: list[SessionTransfer]


class Balances(ContractModel):
    session_started_at: Timestamp
    bank_mode: Mode
    accounts: list[AccountBalance]


class RedTeamRequest(ContractModel):
    variants: StrictInt = Field(ge=10, le=60)
    templates: list[str] = OptionalField()


class JobProgress(ContractModel):
    completed: StrictInt
    total: StrictInt


class RedTeamVariant(ContractModel):
    variant_id: str
    template: str
    placement: Placement
    text: str
    mutator_model: str
    unguarded_outcome: Literal["compromised", "safe", "error"]
    countersign_outcome: Literal["held", "paid_on_file", "blocked", "error"]


class RedTeamMetrics(ContractModel):
    variants: StrictInt
    unguarded_compromised: StrictInt
    countersign_unauthorized: StrictInt
    countersign_held: StrictInt
    false_holds_on_controls: StrictInt
    distinct_planner_prompts: StrictInt


class RedTeamJob(ContractModel):
    job_id: str
    status: JobStatus
    execution: Literal["dry_run"]
    mutator_model: str
    progress: JobProgress
    metrics: RedTeamMetrics = OptionalField()
    variants: list[RedTeamVariant]


class AuditAskRequest(ContractModel):
    question: str
    run_id: str = OptionalField()


class AuditStep(ContractModel):
    tool_id: str
    esql: str = OptionalField()
    row_count: StrictInt = OptionalField()


class AuditAnswer(ContractModel):
    answer: str
    agent_id: str
    conversation_id: str = OptionalField()
    steps: list[AuditStep]


class Person(ContractModel):
    person_id: str
    name: str
    role: PersonRole
    approval_limit_cents: StrictInt | None


class VendorAccountHistory(ContractModel):
    account_display: str
    valid_from: Timestamp
    valid_to: Timestamp | None
    verified_by: str
    verified_at: Timestamp


class VendorPayment(ContractModel):
    date: CalendarDate
    amount_cents: StrictInt
    account_display: str
    journal_entry_id: str


class VendorDetail(ContractModel):
    vendor_id: str
    name: str
    account_on_file_display: str
    account_history: list[VendorAccountHistory]
    payments: list[VendorPayment]


class BankChangeRequest(ContractModel):
    actor_id: str
    new_account_display: str
    evidence_note: str


class JournalSource(ContractModel):
    kind: Literal["invoice", "payment", "approval", "seed", "reversal"]
    id: str


class JournalLine(ContractModel):
    account_code: str
    account_name: str
    debit_cents: StrictInt
    credit_cents: StrictInt


class JournalEntry(ContractModel):
    entry_id: str
    date: CalendarDate
    memo: str
    source: JournalSource
    created_by: str
    lines: list[JournalLine]


class TrialBalanceAccount(ContractModel):
    account_code: str
    account_name: str
    debit_cents: StrictInt
    credit_cents: StrictInt
    balance_cents: StrictInt


class TrialBalance(ContractModel):
    as_of: Timestamp
    balanced: StrictBool
    accounts: list[TrialBalanceAccount]


class ReconciliationItem(ContractModel):
    transfer_id: str | None
    journal_entry_id: str | None
    amount_cents: StrictInt
    status: Literal["matched", "bank_only", "ledger_only"]
    note: str = OptionalField()


class Reconciliation(ContractModel):
    as_of: Timestamp
    bank_balance_change_cents: StrictInt
    ledger_cash_change_cents: StrictInt
    difference_cents: StrictInt
    items: list[ReconciliationItem]


class InboxRequest(ContractModel):
    document_id: str
    sender: str


class InboxItem(ContractModel):
    item_id: str
    document_id: str
    sender: str
    received_at: Timestamp
    status: Literal["queued", "processing", "processed"]
    run_id: str = OptionalField()


class AutopilotRequest(ContractModel):
    enabled: StrictBool
    lanes: list[Lane] = OptionalField()


class Autopilot(ContractModel):
    enabled: StrictBool
    lanes: list[Lane]
    run_id: str | None
    last_checked_at: Timestamp | None


class EvalRequest(ContractModel):
    suite: Literal["planted_errors_v1"]
    lanes: list[Lane] = OptionalField()


class EvalItemResult(ContractModel):
    item_id: str
    invoice_number: str
    error_type: str
    expected_outcome: Literal["PAY", "HOLD", "BLOCK"]
    expected_reason_code: str | None
    actual_outcome: Literal["PAY", "HOLD", "BLOCK", "ERROR"]
    actual_reason_codes: list[str]
    passed: StrictBool


class ErrorTypeMetrics(ContractModel):
    caught: StrictInt
    missed: StrictInt


class EvalMetrics(ContractModel):
    accuracy: StrictInt | StrictFloat
    false_holds_on_clean: StrictInt
    unauthorized_payments: StrictInt
    by_error_type: dict[str, ErrorTypeMetrics]


class EvalJob(ContractModel):
    job_id: str
    suite: str
    status: JobStatus
    execution: Literal["dry_run"]
    progress: JobProgress
    metrics: EvalMetrics = OptionalField()
    items: list[EvalItemResult]


class RunModels(ContractModel):
    extractor: str
    planner: str
    unguarded: str


class RunStartedPayload(ContractModel):
    batch_id: str
    lanes: list[Lane]
    instruction: str
    models: RunModels


class LaneStatusPayload(ContractModel):
    status: LaneStatus
    detail: str = OptionalField()


class DocumentLoadedPayload(ContractModel):
    invoice_id: str
    document_id: str
    pages: StrictInt
    line_count: StrictInt


class ExtractionAttempt(ContractModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    outcome: Literal["success", "failed", "skipped"]
    http_status: StrictInt = Field(default=None, ge=100, le=599, exclude_if=lambda value: value is None,
                                  json_schema_extra=_omit_unset_default)
    error_code: str = OptionalField()


class ExtractionProvenance(ContractModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    extracted_at: Timestamp
    cached: StrictBool
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(min_length=1)
    cache_key: str = OptionalField()
    attempts: list[ExtractionAttempt] = OptionalField()


class FieldsExtractedPayload(ContractModel):
    invoice_id: str
    extractor_model: str
    fields: list[ExtractedField]
    instruction_like_spans: list[SourceSpan]
    handles: list[Handle]
    extraction_provenance: ExtractionProvenance = OptionalField()

    @model_validator(mode="after")
    def matching_extractor_identity(self):
        if self.extraction_provenance is not None and self.extractor_model != self.extraction_provenance.model:
            raise ValueError("Extractor model must match extraction provenance")
        return self


class PlanProposedPayload(ContractModel):
    planner_model: str
    prompt_hash: str
    steps: list[PlanStep]


class AgentToolCallPayload(ContractModel):
    invoice_id: str
    tool: str
    args: dict[str, Any]
    result_summary: str


class AgentMessagePayload(ContractModel):
    invoice_id: str
    text: str


class PolicyDecisionPayload(ContractModel):
    decision: PolicyDecision


class PaymentExecutedPayload(ContractModel):
    invoice_id: str
    via: Literal["policy", "approval", "agent"]
    payment: Payment


class PaymentHeldPayload(ContractModel):
    invoice_id: str
    approval_id: str
    reason_codes: list[ReasonCode]


class ApprovalResolvedPayload(ContractModel):
    approval_id: str
    invoice_id: str
    decision: ApprovalChoice
    note: str = OptionalField()


class ErrorPayload(ContractModel):
    message: str
    recoverable: StrictBool
    invoice_id: str = OptionalField()


class RunCompletedPayload(ContractModel):
    tallies: RunTallies


class LedgerPostedPayload(ContractModel):
    entry: JournalEntry


class InboxReceivedPayload(ContractModel):
    item: InboxItem


class EventBase(ContractModel):
    run_id: str
    seq: StrictInt
    ts: Timestamp
    mode: Mode = OptionalField()
    provider: str = OptionalField()
    model: str = OptionalField()
    unguarded_mode: Literal["ap", "ap_with_vendor_updates"] = OptionalField()


class RunStartedEvent(EventBase):
    type: Literal["run.started"]
    lane: Literal["system"]
    payload: RunStartedPayload


class LaneStatusEvent(EventBase):
    type: Literal["lane.status"]
    lane: Lane
    payload: LaneStatusPayload


class DocumentLoadedEvent(EventBase):
    type: Literal["document.loaded"]
    lane: Lane
    payload: DocumentLoadedPayload


class FieldsExtractedEvent(EventBase):
    type: Literal["fields.extracted"]
    lane: Literal["countersign"]
    payload: FieldsExtractedPayload


class PlanProposedEvent(EventBase):
    type: Literal["plan.proposed"]
    lane: Literal["countersign"]
    payload: PlanProposedPayload


class AgentToolCallEvent(EventBase):
    type: Literal["agent.tool_call"]
    lane: Literal["unguarded"]
    payload: AgentToolCallPayload


class AgentMessageEvent(EventBase):
    type: Literal["agent.message"]
    lane: Literal["unguarded"]
    payload: AgentMessagePayload


class PolicyDecisionEvent(EventBase):
    type: Literal["policy.decision"]
    lane: Literal["countersign"]
    payload: PolicyDecisionPayload


class PaymentExecutedEvent(EventBase):
    type: Literal["payment.executed"]
    lane: Lane
    payload: PaymentExecutedPayload


class PaymentHeldEvent(EventBase):
    type: Literal["payment.held"]
    lane: Literal["countersign"]
    payload: PaymentHeldPayload


class ApprovalResolvedEvent(EventBase):
    type: Literal["approval.resolved"]
    lane: Literal["countersign"]
    payload: ApprovalResolvedPayload


class ErrorEvent(EventBase):
    type: Literal["error"]
    lane: EventLane
    payload: ErrorPayload


class RunCompletedEvent(EventBase):
    type: Literal["run.completed"]
    lane: Literal["system"]
    payload: RunCompletedPayload


class LedgerPostedEvent(EventBase):
    type: Literal["ledger.posted"]
    lane: Literal["countersign"]
    payload: LedgerPostedPayload


class InboxReceivedEvent(EventBase):
    type: Literal["inbox.received"]
    lane: Literal["system"]
    payload: InboxReceivedPayload


RunEvent = Annotated[
    RunStartedEvent | LaneStatusEvent | DocumentLoadedEvent |
    FieldsExtractedEvent | PlanProposedEvent | AgentToolCallEvent |
    AgentMessageEvent | PolicyDecisionEvent | PaymentExecutedEvent |
    PaymentHeldEvent | ApprovalResolvedEvent | ErrorEvent |
    RunCompletedEvent | LedgerPostedEvent | InboxReceivedEvent,
    Field(discriminator="type"),
]


class ReplayModel(ContractModel):
    provider: str
    model: str


class ReplayPayment(Payment):
    invoice_id: str
    invoice_number: str
    vendor_id: str
    vendor_name: str
    currency: Currency
    account_on_file_display: str = OptionalField()
    ts: Timestamp
    via: Literal["policy", "approval", "agent"]


class ReplayHold(ContractModel):
    invoice_id: str
    invoice_number: str
    vendor_id: str
    vendor_name: str
    amount_cents: StrictInt
    currency: Currency
    reason_codes: list[ReasonCode]
    ts: Timestamp
    account_on_file_display: str = OptionalField()
    requested_account_display: str = OptionalField()
    letter_account_display: str = OptionalField()
    approval: Approval = OptionalField()


class ReplayLetter(ContractModel):
    document: DocumentText
    text: str
    vendor_id: str
    vendor_name: str
    requested_account_display: str
    account_on_file_display: str = OptionalField()


class ReplayLane(ContractModel):
    lane: Lane
    provider: str
    model: str
    model_role: Literal["agent", "planner"]
    status: LaneStatus
    tally: LaneTally
    extraction_models: list[ReplayModel]
    payments: list[ReplayPayment]
    holds: list[ReplayHold]


class ReplayLanes(ContractModel):
    unguarded: ReplayLane
    countersign: ReplayLane


class ReplayCounts(ContractModel):
    unguarded: LaneTally
    countersign: LaneTally
    letter_payment_count: StrictInt
    countersign_held_correctly: StrictBool = OptionalField()


class BankChangeReplaySummary(ContractModel):
    recorded: Literal[True]
    recorded_at: Timestamp
    source_file: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    is_default: StrictBool
    complete: StrictBool
    status: str
    unguarded_provider: str
    unguarded_model: str
    unguarded_mode: Literal["ap", "ap_with_vendor_updates"] = OptionalField()
    summary: ReplayCounts
    limitations: list[str]


class BankChangeReplay(BankChangeReplaySummary):
    run: Run
    batch: Batch
    letter: ReplayLetter = OptionalField()
    lanes: ReplayLanes
    leak_guard: LeakGuard = OptionalField()
    events: list[RunEvent]
    held_invoice_graph: ProvenanceGraph = OptionalField()
    planner_transcript: PlannerTranscript = OptionalField()
    transcripts: list[AgentTranscript]
    evidence: dict[str, Any]
