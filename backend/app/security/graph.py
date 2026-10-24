"""Contract-shaped, causal provenance graphs built from execution evidence.

Edges run from an input to the value/check/action it influenced, so callers can
follow an untrusted span forward to the deterministic check that rejected it.
Document evidence always remains untrusted, including a corroborated account.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from app.contract_models import (
    DocumentText, FieldsExtractedPayload, GraphEdge, GraphNode, PlanStep,
    PolicyDecision, ProvenanceGraph, SourceSpan,
)
from app.security.interpreter import PaymentIntent
from app.security.labels import DocumentSpan, Source, TrustedRecord


def _node_id(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(encoded.encode()).hexdigest()[:16]}"


def source_node_id(source: Source | SourceSpan) -> str:
    if isinstance(source, TrustedRecord):
        return record_node_id(source.kind, source.id)
    return _node_id("span", source.document_id, source.page, source.line,
                    source.char_start, source.char_end, source.span_verified)


def field_node_id(invoice_id: str, name: str) -> str:
    return _node_id("field", invoice_id, name)


def handle_node_id(invoice_id: str, handle_name: str) -> str:
    return _node_id("handle", invoice_id, handle_name)


def record_node_id(kind: str, record_id: str) -> str:
    return _node_id("record", kind, record_id)


def plan_node_id(invoice_id: str, step_id: str) -> str:
    return _node_id("plan", invoice_id, step_id)


def check_node_id(invoice_id: str, code: str) -> str:
    return _node_id("check", invoice_id, code)


def action_node_id(invoice_id: str) -> str:
    return _node_id("action", invoice_id)


def _as_source(span: SourceSpan) -> DocumentSpan:
    return DocumentSpan(span.document_id, span.page, span.line,
                        span.char_start, span.char_end, span.span_verified)


def _coordinates(source: DocumentSpan | SourceSpan) -> tuple:
    return (source.document_id, source.page, source.line,
            source.char_start, source.char_end)


def build_graph(
    *, run_id: str, invoice_id: str, document: DocumentText,
    extraction: FieldsExtractedPayload, steps: Sequence[PlanStep],
    decision: PolicyDecision, intent: PaymentIntent | None = None,
    vendors: Sequence[Mapping[str, Any]] = (),
    purchase_orders: Sequence[Mapping[str, Any]] = (),
    action_status: str | None = None, action_detail: dict | None = None,
) -> ProvenanceGraph:
    """Build from original source, stored extraction, and actual policy output.

    An ALLOW decision alone means approved, not paid. The caller supplies
    ``action_status='paid'`` only after a validated bank response is persisted.
    """
    if (document.invoice_id != invoice_id or extraction.invoice_id != invoice_id
            or decision.invoice_id != invoice_id or decision.run_id != run_id
            or (intent is not None and intent.invoice_id != invoice_id)):
        raise ValueError("Graph evidence belongs to a different invoice or run")
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    span_claims = [field.span for field in extraction.fields if field.span is not None]
    span_claims.extend(extraction.instruction_like_spans)
    records = {
        **{("vendor", row["vendor_id"]): row for row in vendors},
        **{("purchase_order", row["purchase_order_id"]): row for row in purchase_orders},
    }

    def add(node: GraphNode) -> str:
        nodes[node.id] = node
        return node.id

    def edge(source: str, target: str, kind: str, trust: str) -> None:
        edge_id = _node_id("edge", source, target, kind)
        edges[edge_id] = GraphEdge(id=edge_id, source=source, target=target,
                                   kind=kind, trust=trust)

    def add_source(source: Source) -> str:
        identifier = source_node_id(source)
        if identifier in nodes:
            return identifier
        if isinstance(source, TrustedRecord):
            record = records.get((source.kind, source.id), {})
            detail = {"kind": source.kind, "record_id": source.id}
            available = bool(record) or (source.kind == "invoice" and source.id == invoice_id)
            if not available:
                detail["available"] = False
            allowed = (
                ("vendor_id", "name", "account_on_file_display", "remit_account_number")
                if source.kind == "vendor" else
                ("purchase_order_id", "vendor_id", "po_number", "amount_cents", "currency", "status")
                if source.kind == "purchase_order" else ()
            )
            detail.update({key: record[key] for key in allowed if key in record})
            return add(GraphNode(id=identifier, kind="trusted_record",
                                 label=f"{source.kind}: {source.id}",
                                 trust="trusted" if available else "none", detail=detail))
        claim = next((span for span in span_claims if _coordinates(span) == _coordinates(source)), None)
        quote = claim.quote if claim is not None else ""
        if source.document_id == document.document_id:
            for page in document.pages:
                for line in page.lines:
                    if (page.page == source.page and line.line == source.line
                            and 0 <= source.char_start < source.char_end <= len(line.text)):
                        quote = line.text[source.char_start:source.char_end]
        span = SourceSpan(document_id=source.document_id, page=source.page,
                          line=source.line, char_start=source.char_start,
                          char_end=source.char_end, quote=quote,
                          span_verified=source.span_verified)
        return add(GraphNode(id=identifier, kind="span", label=quote or "Unverified source span",
                             trust="untrusted", span=span,
                             detail={"span_verified": source.span_verified}))

    # Add sources first, including control dependencies and corroborating records.
    for span in span_claims:
        add_source(_as_source(span))
    if intent is not None:
        for source in sorted(intent.sources, key=source_node_id):
            add_source(source)
    evidence_ids = {identifier for check in decision.checks for identifier in check.evidence_node_ids}
    trusted_refs = {argument.ref for step in steps for argument in step.args.values()
                    if argument.kind == "trusted"}
    for kind, record_id in records:
        if record_node_id(kind, record_id) in evidence_ids or record_id in trusted_refs:
            add_source(TrustedRecord(kind, record_id))

    fields = {field.name: field for field in extraction.fields}
    for field in extraction.fields:
        field_id = add(GraphNode(id=field_node_id(invoice_id, field.name), kind="field",
                                 label=field.name, trust="untrusted",
                                 detail={"value": field.value, "type": field.type}))
        if field.span is not None:
            edge(source_node_id(field.span), field_id, "derived_from", "untrusted")
            # A verifier may downgrade a claimed true span on the execution
            # value while preserving the original extraction for inspection.
            if intent is not None:
                for source in intent.sources:
                    if isinstance(source, DocumentSpan) and _coordinates(source) == _coordinates(field.span):
                        edge(add_source(source), field_id, "derived_from", "untrusted")

    handles = {handle.name: handle for handle in extraction.handles}
    payee_ok = any(check.code == "PAYEE_ON_FILE" and check.passed for check in decision.checks)
    corroborating = (
        [source for source in intent.to_account.sources if isinstance(source, TrustedRecord) and source.kind == "vendor"]
        if intent is not None and payee_ok else []
    )
    for handle in extraction.handles:
        field_name = handle.name.partition(".")[2]
        trust = "corroborated" if field_name == "remit_account_number" and corroborating else "untrusted"
        handle_id = add(GraphNode(id=handle_node_id(invoice_id, handle.name), kind="handle",
                                  label=handle.name, trust=trust,
                                  detail={"type": handle.type, "span_verified": handle.span_verified}))
        if field_name in fields:
            edge(field_node_id(invoice_id, field_name), handle_id, "derived_from", "untrusted")
        if trust == "corroborated":
            for source in corroborating:
                edge(add_source(source), handle_id, "corroborated_by", "corroborated")

    # Per-invoice input is expected, but accepting a whole-batch public plan
    # must not accidentally include a different invoice's steps or handles.
    invoice_steps = [step for step in steps if step.invoice_id == invoice_id]
    step_ids = {step.id for step in invoice_steps}
    for step in invoice_steps:
        add(GraphNode(id=plan_node_id(invoice_id, step.id), kind="plan_step", label=step.op,
                      trust="none", detail=step.model_dump(mode="json")))
    for step in invoice_steps:
        target = plan_node_id(invoice_id, step.id)
        for argument in step.args.values():
            if argument.kind == "handle" and argument.ref in handles:
                edge(handle_node_id(invoice_id, argument.ref), target, "derived_from", "untrusted")
            elif argument.kind == "step" and argument.ref in step_ids:
                edge(plan_node_id(invoice_id, argument.ref), target, "derived_from", "untrusted")
            elif argument.kind == "trusted":
                for kind, record_id in records:
                    if record_id == argument.ref:
                        edge(record_node_id(kind, record_id), target, "derived_from", "trusted")

    field_checks = {
        "VENDOR_KNOWN": {"vendor_name"},
        "SPAN_VERIFIED": {"amount_due_cents", "remit_account_number"},
        "PAYEE_ON_FILE": {"remit_account_number"},
        "AMOUNT_MATCHES_PO": {"amount_due_cents", "po_number", "currency"},
        "NOT_DUPLICATE": {"invoice_number"},
    }
    action_id = action_node_id(invoice_id)
    for check in decision.checks:
        check_id = add(GraphNode(id=check_node_id(invoice_id, check.code), kind="policy_check",
                                 label=check.code, trust="trusted",
                                 status="passed" if check.passed else "failed",
                                 detail={"message": check.message, "evidence_node_ids": check.evidence_node_ids}))
        for evidence in check.evidence_node_ids:
            if evidence not in nodes:
                raise ValueError("Policy evidence is absent from the provenance graph")
            evidence_trust = "trusted" if nodes[evidence].trust == "trusted" else "untrusted"
            edge(evidence, check_id, "checked_by", evidence_trust)
        for handle in extraction.handles:
            if handle.name.partition(".")[2] in field_checks.get(check.code, set()):
                trust = "corroborated" if nodes[handle_node_id(invoice_id, handle.name)].trust == "corroborated" else "untrusted"
                edge(handle_node_id(invoice_id, handle.name), check_id, "checked_by", trust)
        if check.code == "INSTRUCTIONS_IN_DOCUMENT":
            for span in extraction.instruction_like_spans:
                edge(source_node_id(span), check_id, "checked_by", "untrusted")
        for step in invoice_steps:
            if step.op in {"pay", "hold", "skip"}:
                edge(plan_node_id(invoice_id, step.id), check_id, "checked_by", "trusted")
        edge(check_id, action_id, "decided", "trusted")

    status = action_status or {"ALLOW": "approved", "HOLD": "held", "BLOCK": "blocked"}[decision.outcome]
    detail = {"decision_id": decision.decision_id, "outcome": decision.outcome,
              "reason_codes": decision.reason_codes, **(action_detail or {})}
    if decision.approval_id is not None:
        detail["approval_id"] = decision.approval_id
    add(GraphNode(id=action_id, kind="action", label=status, trust="trusted", status=status, detail=detail))
    if not decision.checks:
        for step in invoice_steps:
            if step.op in {"pay", "hold", "skip"}:
                edge(plan_node_id(invoice_id, step.id), action_id, "decided", "trusted")
    if any(edge.source not in nodes or edge.target not in nodes for edge in edges.values()):
        raise ValueError("Graph contains an unresolved reference")
    return ProvenanceGraph(run_id=run_id, invoice_id=invoice_id,
                           nodes=list(nodes.values()), edges=list(edges.values()))


def overlay_resolution(graph: ProvenanceGraph, events: Sequence, approvals: Sequence = ()) -> ProvenanceGraph:
    """Reflect durable approval/payment results without rewriting source evidence."""
    graph = graph.model_copy(deep=True)
    action = next((node for node in graph.nodes if node.id == action_node_id(graph.invoice_id)), None)
    if action is None:
        return graph
    detail = dict(action.detail or {})
    for record in approvals:
        if (record.get("run_id") != graph.run_id or record.get("invoice_id") != graph.invoice_id
                or record.get("status") != "resolved"):
            continue
        resolution = record.get("resolution", {})
        action.status = "rejected" if resolution.get("decision") == "reject" else "approved"
        detail.update({"approval_id": record["approval_id"], "resolution": resolution})
    for event in events:
        if event.run_id != graph.run_id or getattr(event.payload, "invoice_id", None) != graph.invoice_id:
            continue
        if event.type == "approval.resolved":
            action.status = "rejected" if event.payload.decision == "reject" else "approved"
            detail.update({"approval_id": event.payload.approval_id, "approval_decision": event.payload.decision})
        elif event.type == "payment.executed":
            action.status = "approved" if event.payload.via == "approval" else "paid"
            detail.update({"transfer_id": event.payload.payment.transfer_id,
                           "transfer_status": event.payload.payment.transfer_status,
                           "to_account_id": event.payload.payment.to_account_id})
    action.label = action.status or action.label
    action.detail = detail
    return ProvenanceGraph.model_validate(graph.model_dump(mode="python"))
