"""Immutable, structured audit facts derived from validated events.

The disabled payload preserves the complete original envelope. Agent Builder
tools query the explicit scalar fields, never raw invoice instructions.
"""

from collections.abc import Mapping
import re

from pydantic import TypeAdapter

from app.contract_models import RunEvent


_EVENT = TypeAdapter(RunEvent)
_KEYWORDS = (
    "run_id", "lane", "type", "invoice_id", "vendor_id", "outcome",
    "reason_codes", "transfer_id", "mode", "attack_placement", "invoice_number",
    "vendor_name", "batch_id", "requested_account", "account_on_file",
)
EVENT_MAPPINGS = {"dynamic": "strict", "properties": {
    **{name: {"type": "keyword"} for name in _KEYWORDS},
    "seq": {"type": "long"}, "ts": {"type": "date"},
    "amount_cents": {"type": "long"}, "payee_on_file": {"type": "boolean"},
    "payee_document_derived": {"type": "boolean"},
    "payload": {"type": "object", "enabled": False},
}}
INDEX_NAME = "countersign-events"


def _dict(value):
    return value if isinstance(value, Mapping) else {}


def _record(store, table, key):
    if not isinstance(key, str) or not key:
        return {}
    try:
        return _dict(store.get_record(table, key))
    except Exception:
        return {}


def _account(value):
    return re.sub(r"[\s-]", "", value) if isinstance(value, str) else ""


def _text(value):
    return value if isinstance(value, str) and value else None


def _long(value):
    return value if type(value) is int else None


def _ledger_context(store, entry):
    try:
        with store._lock:
            row = store._db().execute(
                "SELECT invoice_id, vendor_id, transfer_id FROM ledger_journal WHERE entry_id = ?",
                (entry.get("entry_id"),),
            ).fetchone()
        return dict(row) if row is not None else {}
    except Exception:
        return {}


def _enrich(store, event, data):
    raw = data["payload"]
    payload = _dict(raw.get("payload"))
    decision = _dict(payload.get("decision")) if event.type == "policy.decision" else {}
    payment = _dict(payload.get("payment"))
    approval = _record(store, "approvals", payload.get("approval_id") or decision.get("approval_id"))
    entry = _dict(payload.get("entry"))
    ledger = _ledger_context(store, entry) if entry else {}
    transfer = _record(store, "transfers", payment.get("transfer_id") or ledger.get("transfer_id"))
    item = _dict(payload.get("item"))
    document = _record(store, "documents", item.get("document_id"))
    invoice_id = (payload.get("invoice_id") or decision.get("invoice_id")
                  or approval.get("invoice_id") or ledger.get("invoice_id")
                  or transfer.get("invoice_id") or document.get("invoice_id"))
    source = _dict(entry.get("source"))
    if not invoice_id and source.get("kind") == "invoice":
        invoice_id = source.get("id")
    data["invoice_id"] = _text(invoice_id)
    run = _record(store, "runs", event.run_id)
    data["batch_id"] = _text(run.get("batch_id"))
    batch = _record(store, "batches", run.get("batch_id"))
    invoices = [row for row in batch.get("invoices", [])
                if isinstance(row, Mapping) and row.get("invoice_id") == invoice_id]
    invoice = invoices[0] if len(invoices) == 1 else {}
    data["vendor_id"] = _text(transfer.get("vendor_id") or ledger.get("vendor_id") or invoice.get("vendor_id"))
    data["invoice_number"] = _text(transfer.get("invoice_number") or approval.get("invoice_number") or invoice.get("invoice_number"))
    data["vendor_name"] = _text(approval.get("vendor_name") or invoice.get("vendor_name"))
    scoring = _record(store, "invoice_scoring", invoice_id)
    if not invoice and scoring.get("batch_id"):
        original = _record(store, "batches", scoring["batch_id"])
        matches = [row for row in original.get("invoices", [])
                   if isinstance(row, Mapping) and row.get("invoice_id") == invoice_id]
        if len(matches) == 1:
            invoice = matches[0]
            data["vendor_id"] = data["vendor_id"] or _text(invoice.get("vendor_id"))
            data["invoice_number"] = data["invoice_number"] or _text(invoice.get("invoice_number"))
            data["vendor_name"] = data["vendor_name"] or _text(invoice.get("vendor_name"))
            data["batch_id"] = data["batch_id"] or _text(scoring.get("batch_id"))
    placement = scoring.get("placement")
    if not placement:
        placement = {"hidden_white_text": "hidden_white_text", "footer_processing_note": "footer",
                     "remittance_update_visible": "visible_note", "amount_inflation": "visible_note",
                     "extra_fee": "visible_note"}.get(scoring.get("template"))
    data["attack_placement"] = _text(placement)
    if decision:
        data["outcome"] = _text(decision.get("outcome"))
        data["reason_codes"] = list(decision.get("reason_codes", []))
    elif event.type == "payment.held":
        data["outcome"] = "HOLD"
        data["reason_codes"] = list(payload.get("reason_codes", []))
    elif event.type == "payment.executed":
        data["outcome"] = "PAID"
    elif event.type == "approval.resolved":
        data["outcome"] = "REJECTED" if payload.get("decision") == "reject" else "APPROVED"
    if payment:
        data["amount_cents"] = _long(payment.get("amount_cents"))
        data["payee_on_file"] = payment.get("payee_on_file") if type(payment.get("payee_on_file")) is bool else None
        data["transfer_id"] = _text(payment.get("transfer_id"))
        data["requested_account"] = _text(payment.get("to_display_number"))
    elif approval:
        data["amount_cents"] = _long(approval.get("amount_cents"))
        data["requested_account"] = _text(approval.get("requested_account_display"))
        data["account_on_file"] = _text(approval.get("account_on_file_display"))
        requested, expected = _account(data["requested_account"]), _account(data["account_on_file"])
        if requested and expected:
            data["payee_on_file"] = requested == expected
    elif invoice:
        data["amount_cents"] = _long(invoice.get("amount_cents"))
    if entry:
        lines = entry.get("lines", [])
        debits = [line.get("debit_cents") for line in lines if isinstance(line, Mapping)]
        if debits and all(type(amount) is int for amount in debits):
            data["amount_cents"] = sum(debits)
        data["transfer_id"] = _text(ledger.get("transfer_id"))
    # Only financial decision/checkpoint events have this provenance fact.
    # Earlier extraction/document events must not acquire a future decision.
    if event.type not in {"policy.decision", "payment.held", "payment.executed", "approval.resolved"}:
        return
    if not decision:
        context = _record(store, "approval_contexts", payload.get("approval_id"))
        decision = _record(store, "decisions", context.get("decision_id"))
        if not decision:
            graph = _record(store, "graphs", f"{event.run_id}:{invoice_id}")
            checks = [node for node in graph.get("nodes", []) if isinstance(node, Mapping)
                      and node.get("kind") == "policy_check" and node.get("label") == "PAYEE_ON_FILE"]
            if checks:
                check = checks[0]
                decision = {"checks": [{"code": "PAYEE_ON_FILE", "passed": check.get("status") == "passed",
                                        "evidence_node_ids": _dict(check.get("detail")).get("evidence_node_ids", [])}]}
    checks = [check for check in decision.get("checks", []) if isinstance(check, Mapping)
              and check.get("code") == "PAYEE_ON_FILE"]
    graph = _record(store, "graphs", f"{event.run_id}:{invoice_id}")
    if checks:
        check = checks[0]
        if data["payee_on_file"] is None and type(check.get("passed")) is bool:
            data["payee_on_file"] = check["passed"]
        evidence = set(check.get("evidence_node_ids", []))
        nodes = [node for node in graph.get("nodes", []) if isinstance(node, Mapping)]
        if not evidence or evidence <= {node.get("id") for node in nodes}:
            data["payee_document_derived"] = any(
                node.get("kind") == "span" and node.get("id") in evidence
                and node.get("trust") == "untrusted" for node in nodes
            )
        account_fields = {edge.get("target") for edge in graph.get("edges", [])
                          if isinstance(edge, Mapping) and edge.get("source") in evidence
                          and edge.get("kind") == "derived_from"}
        candidates = [node for node in graph.get("nodes", []) if isinstance(node, Mapping)
                      and node.get("id") in account_fields and node.get("label") == "remit_account_number"]
        if not data["requested_account"] and len(candidates) == 1:
            data["requested_account"] = _text(_dict(candidates[0].get("detail")).get("value"))
        vendor_snapshots = [_dict(node.get("detail")) for node in graph.get("nodes", [])
                            if isinstance(node, Mapping) and node.get("kind") == "trusted_record"
                            and _dict(node.get("detail")).get("kind") == "vendor"
                            and _dict(node.get("detail")).get("vendor_id") == data["vendor_id"]]
        if not data["account_on_file"] and len(vendor_snapshots) == 1:
            snapshot = vendor_snapshots[0]
            data["account_on_file"] = _text(snapshot.get("account_on_file_display") or snapshot.get("remit_account_number"))
    # The graph's supporting field captures the requested letter account even
    # when the original invoice printed the unchanged on-file account.
    requested = [node for node in graph.get("nodes", []) if isinstance(node, Mapping)
                 and node.get("kind") == "field" and _dict(node.get("detail")).get("role") == "requested_remittance"]
    if not data["requested_account"] and len(requested) == 1:
        data["requested_account"] = _text(_dict(requested[0].get("detail")).get("value"))


def map_event(store, event):
    """Total defensive enrichment; failure cannot erase the original event."""
    event = _EVENT.validate_python(event)
    raw = event.model_dump(mode="json", exclude_none=True)
    result = {name: None for name in _KEYWORDS}
    result.update(run_id=event.run_id, seq=event.seq, ts=raw["ts"], lane=event.lane,
                  type=event.type, mode=event.mode or "unknown", reason_codes=[],
                  amount_cents=None, payee_on_file=None, payee_document_derived=None, payload=raw)
    try:
        _enrich(store, event, result)
    except Exception:
        # Preserve the full validated envelope even if optional historical
        # enrichment is unavailable or corrupted. Never invent missing facts.
        pass
    return result
