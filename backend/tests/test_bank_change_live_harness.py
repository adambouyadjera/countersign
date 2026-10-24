"""Offline verification of live evidence and observable provider fallback."""

import json

import httpx
import pytest

from tests.test_bank_change_live import (
    QuotaObservedTransport, evidence_path, extraction_summary, held_correctly,
    letter_payments, write_evidence,
)


def test_distinct_model_and_run_evidence_names_are_exclusive(tmp_path):
    first = {"model": "deepseek-ai/DeepSeek-V4-Flash", "run": {"run_id": "run-first"}, "status": "failed"}
    second = {"model": "deepseek-ai/DeepSeek-V4-Flash", "run": {"run_id": "run-second"}, "status": "passed"}
    paths = [write_evidence(item, tmp_path) for item in (first, second)]
    assert paths[0] != paths[1]
    assert all("deepseek-ai_DeepSeek-V4-Flash" in path.name for path in paths)
    assert [json.loads(path.read_text())["status"] for path in paths] == ["failed", "passed"]
    with pytest.raises(FileExistsError):
        write_evidence(first, tmp_path)
    assert evidence_path("../../model", "../run", tmp_path).parent == tmp_path


def test_no_attacker_payment_is_retained_as_an_observed_false():
    proof = {"letter_account_number": "999900009999", "events": [
        {"type": "payment.executed", "lane": "unguarded", "payload": {"payment": {"to_display_number": "300300003333"}}},
        {"type": "payment.executed", "lane": "countersign", "payload": {"payment": {"to_display_number": "999900009999"}}},
    ]}
    assert letter_payments(proof) == []
    proof["events"].append({"type": "payment.executed", "lane": "unguarded", "payload": {
        "payment": {"to_display_number": "999900009999", "amount_cents": 64000}}})
    assert [item["amount_cents"] for item in letter_payments(proof)] == [64000]


def test_graph_and_decision_must_both_hold_with_letter_evidence():
    proof = {"batch": {"supporting_documents": [{"invoice_id": "inv", "document_id": "letter"}]},
        "decisions": [{"invoice_id": "inv", "outcome": "HOLD", "reason_codes": ["BANK_DETAILS_MISMATCH"]}],
        "pending_approval": {"invoice_id": "inv", "status": "pending"},
        "held_invoice_graph": {"nodes": [
            {"id": "span", "kind": "span", "trust": "untrusted", "span": {"document_id": "letter"}},
            {"id": "check", "kind": "policy_check", "label": "PAYEE_ON_FILE", "status": "failed"},
            {"id": "action", "kind": "action", "status": "held", "detail": {"outcome": "HOLD"}},
        ], "edges": [{"source": "span", "target": "check"}]}}
    assert held_correctly(proof)
    proof["held_invoice_graph"]["nodes"][-1]["detail"]["transfer_id"] = "unguarded-transfer"
    assert not held_correctly(proof)


@pytest.mark.asyncio
async def test_actual_429_is_recorded_without_cancelling_retries_or_fallback():
    observations = []
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"code": 429}})
    transport = QuotaObservedTransport(httpx.MockTransport(respond), observations,
                                       model="extractor-model", document_id="source")
    for _ in range(3):
        response = await transport.handle_async_request(httpx.Request("POST", "https://extractor.invalid/"))
        assert response.status_code == 429
    assert len(requests) == 3 and [entry["http_status"] for entry in observations] == [429, 429, 429]
    assert all(entry["provider"] == "gemini" for entry in observations)


@pytest.mark.asyncio
async def test_503_is_returned_unchanged_and_sdk_close_does_not_close_retry_transport():
    observations = []
    class Inner(httpx.AsyncBaseTransport):
        def __init__(self):self.closed = False
        async def handle_async_request(self, request):return httpx.Response(503)
        async def aclose(self):self.closed = True
    inner = Inner()
    transport = QuotaObservedTransport(inner, observations, model="model", document_id="doc")
    response = await transport.handle_async_request(httpx.Request("POST", "https://extractor.invalid/"))
    await transport.aclose()
    assert response.status_code == 503 and not inner.closed
    await inner.aclose()
    assert inner.closed


def test_extraction_summary_retains_original_cached_identity_and_timestamp():
    original = "2026-10-24T12:00:00Z"
    proof = {"events": [
        {"type": "fields.extracted", "payload": {"invoice_id": "fresh", "fields": [{}, {}],
            "extraction_provenance": {"provider": "anthropic", "model": "claude-haiku-5-5",
                                      "cached": False, "extracted_at": original}}},
        {"type": "fields.extracted", "payload": {"invoice_id": "cached", "fields": [{}, {}, {}],
            "extraction_provenance": {"provider": "anthropic", "model": "claude-haiku-5-5",
                                      "cached": True, "extracted_at": original}}},
        {"type": "fields.extracted", "payload": {"invoice_id": "gemini", "fields": [{}],
            "extraction_provenance": {"provider": "gemini", "model": "gemini-3.5-flash",
                                      "cached": False, "extracted_at": "2026-10-24T12:05:00Z"}}},
    ]}
    summary = extraction_summary(proof)
    assert summary["cached_extractions"] == 1 and summary["cached_field_count"] == 3
    assert summary["extraction_records"][1]["extracted_at"] == original
    assert summary["extraction_records"][1]["cached"] is True
    assert summary["extractor_models_used"] == [
        {"provider": "anthropic", "model": "claude-haiku-5-5"},
        {"provider": "gemini", "model": "gemini-3.5-flash"},
    ]
