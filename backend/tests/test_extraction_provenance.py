"""Extraction origin and cache reuse remain visible without granting trust."""

from datetime import datetime, timezone

from pydantic import TypeAdapter, ValidationError
import pytest

from app.contract_models import (
    DocumentText, ExtractionAttempt, ExtractionProvenance, FieldsExtractedPayload,
    PolicyCheck, PolicyDecision, RunEvent,
)
from app.security.graph import build_graph, extraction_node_id, field_node_id, handle_node_id, source_node_id
from app.security.handles import HandleTable


ORIGINAL_TIME = "2026-10-24T18:00:00Z"


def provenance(*, cached=False, provider="anthropic", model="claude-haiku-5-5"):
    return ExtractionProvenance(
        provider=provider, model=model, extracted_at=ORIGINAL_TIME, cached=cached,
        document_sha256="a" * 64, prompt_version="quarantined-invoice-v1",
        cache_key="b" * 64,
        attempts=[
            ExtractionAttempt(provider="gemini", model="gemini-3.5-flash", outcome="failed",
                              http_status=429, error_code="rate_limit"),
            ExtractionAttempt(provider=provider, model=model, outcome="success", http_status=200),
        ],
    )


def graph_inputs(origin=None, *, verified=True):
    document = DocumentText(document_id="new-document-id", invoice_id="new-invoice-id", pages=[{
        "page": 1, "lines": [
            {"line": 1, "text": "$640.00"},
            {"line": 2, "text": "999900009999"},
        ],
    }])
    extraction = FieldsExtractedPayload(
        invoice_id=document.invoice_id, extractor_model=origin.model if origin else "legacy-model",
        fields=[
            {"name": name, "value": value, "type": kind, "trust": "untrusted", "span": {
                "document_id": document.document_id, "page": 1, "line": line,
                "char_start": 0, "char_end": len(quote), "quote": quote,
                "span_verified": verified,
            }} for name, value, kind, line, quote in (
                ("amount_due_cents", 64000, "money", 1, "$640.00"),
                ("remit_account_number", "999900009999", "account_number", 2, "999900009999"),
            )
        ],
        handles=[], instruction_like_spans=[],
        **({"extraction_provenance": origin} if origin else {}),
    )
    extraction.handles = HandleTable().add_invoice(document, extraction, 1)
    account = next(field for field in extraction.fields if field.name == "remit_account_number")
    decision = PolicyDecision(
        decision_id="test-decision", run_id="test-run", invoice_id=document.invoice_id,
        outcome="HOLD", reason_codes=["BANK_DETAILS_MISMATCH"],
        checks=[PolicyCheck(code="PAYEE_ON_FILE", passed=False, message="Requested account is not on file.",
                            evidence_node_ids=[source_node_id(account.span)])],
    )
    return dict(run_id="test-run", invoice_id=document.invoice_id, document=document,
                extraction=extraction, decision=decision, steps=[])


def test_fresh_fallback_origin_is_recorded_on_operation_fields_and_handles():
    origin = provenance()
    inputs = graph_inputs(origin)
    graph = build_graph(**inputs)
    operation = next(node for node in graph.nodes if node.id == extraction_node_id(
        inputs["invoice_id"], inputs["document"].document_id))
    assert operation.kind == "plan_step" and operation.trust == "none"
    assert operation.label == "Extraction: anthropic/claude-haiku-5-5 (fresh)"
    assert operation.detail["operation"] == "quarantined_extraction"
    assert operation.detail["provider"] == "anthropic" and operation.detail["cached"] is False
    assert [item["outcome"] for item in operation.detail["attempts"]] == ["failed", "success"]
    for kind in ("field", "handle"):
        for node in (node for node in graph.nodes if node.kind == kind):
            assert node.detail["extraction_provenance"] == origin.model_dump(mode="json")
    for field in inputs["extraction"].fields:
        assert any(edge.source == operation.id and edge.target == field_node_id(graph.invoice_id, field.name)
                   and edge.trust == "untrusted" for edge in graph.edges)


def test_cache_hit_is_visible_and_retains_original_provider_model_and_timestamp():
    origin = provenance(cached=True)
    graph = build_graph(**graph_inputs(origin))
    operation = next(node for node in graph.nodes if node.detail and node.detail.get("operation") == "quarantined_extraction")
    assert operation.label == "Extraction: anthropic/claude-haiku-5-5 (cached)"
    assert operation.detail["cached"] is True
    assert operation.detail["provider"] == "anthropic"
    assert operation.detail["model"] == "claude-haiku-5-5"
    assert operation.detail["extracted_at"] == ORIGINAL_TIME
    assert all(node.trust == "untrusted" for node in graph.nodes if node.kind in {"span", "field", "handle"})
    assert all("(fresh)" not in node.label for node in graph.nodes)


def test_cached_label_cannot_upgrade_an_unverified_span_or_handle():
    inputs = graph_inputs(provenance(cached=True), verified=False)
    graph = build_graph(**inputs)
    assert all(node.span.span_verified is False for node in graph.nodes if node.kind == "span")
    handle = next(node for node in graph.nodes if node.id == handle_node_id(graph.invoice_id, "$D1.remit_account_number"))
    assert handle.trust == "untrusted" and handle.detail["span_verified"] is False
    check = next(node for node in graph.nodes if node.kind == "policy_check")
    assert check.status == "failed"


def test_legacy_extraction_does_not_invent_provider_timestamp_or_cache_metadata():
    inputs = graph_inputs()
    graph = build_graph(**inputs)
    assert inputs["extraction"].extraction_provenance is None
    assert not any(node.detail and node.detail.get("operation") == "quarantined_extraction" for node in graph.nodes)
    assert all("extraction_provenance" not in (node.detail or {}) for node in graph.nodes)


def test_fields_extracted_event_carries_the_same_cached_origin():
    origin = provenance(cached=True)
    payload = graph_inputs(origin)["extraction"]
    event = TypeAdapter(RunEvent).validate_python({
        "run_id": "test-run", "seq": 1, "ts": datetime.now(timezone.utc), "lane": "countersign",
        "mode": "live", "type": "fields.extracted", "payload": payload,
    })
    assert event.model_dump(mode="json")["payload"]["extraction_provenance"] == origin.model_dump(mode="json")


def test_extractor_identity_cannot_disagree_with_recorded_producer():
    payload = graph_inputs()["extraction"].model_dump(mode="json")
    payload["extraction_provenance"] = provenance().model_dump(mode="json")
    with pytest.raises(ValidationError, match="Extractor model must match extraction provenance"):
        FieldsExtractedPayload.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("provider", ""), ("model", ""), ("cached", "true"),
    ("document_sha256", "not-a-sha256"), ("prompt_version", ""),
    ("extracted_at", "2026-10-24T18:00:00"),
])
def test_invalid_origin_metadata_is_rejected(field, value):
    payload = provenance().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        ExtractionProvenance.model_validate(payload)
