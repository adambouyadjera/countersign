"""Planner transport and validation operate only on public handle metadata."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import SecretStr

from app.adapters.planner import LivePlannerLLM, OfflinePlannerLLM, PlannerError
from app.contract_models import DocumentText, PlannerTranscript
from app.pipeline.extractor import OfflineExtractorLLM
from app.pipeline.planner import build_planner_prompt, propose
from app.security.handles import HandleTable
from app.security.interpreter import validate_plan
from app.store import Store
from app.world.batches import create_standard_batch


class RecordingClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.messages = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        text = next(self.responses)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


@pytest_asyncio.fixture
async def world():
    store = Store(":memory:")
    store.initialize()
    batch = create_standard_batch(store)
    table, documents = HandleTable(), []
    for position, invoice in enumerate(batch.invoices, 1):
        document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
        extraction = await OfflineExtractorLLM().extract(document)
        table.add_invoice(document, extraction, position)
        documents.append(document)
    try:
        yield dict(store=store, table=table, documents=documents,
                   vendors=store.list_records("vendors"), purchase_orders=store.list_records("purchase_orders"),
                   instruction="Pay invoices due within 30 days against open orders and accounts on file.")
    finally:
        store.close()


@pytest.mark.asyncio
async def test_live_transport_receives_exact_guarded_prompt_and_transcript_hash(world):
    prompt = build_planner_prompt(world["instruction"], world["vendors"], world["purchase_orders"], world["table"])
    program = await OfflinePlannerLLM().complete(prompt=prompt)
    client = RecordingClient([program])
    adapter = LivePlannerLLM(key=SecretStr("synthetic-key"), model="synthetic-model", client=client)
    result = await propose(run_id="planner-test", adapter=adapter, **world)
    validate_plan(result.steps, world["table"], world["vendors"], world["purchase_orders"])
    transcript = PlannerTranscript.model_validate(world["store"].get_record("planner_transcripts", "planner-test"))
    assert len(transcript.calls) == len(client.requests) == 1
    call, received = transcript.calls[0], client.requests[0]
    assert set(received) == {"model", "max_tokens", "system", "messages"}
    assert {key: received[key] for key in ("system", "messages")} == json.loads(call.prompt)
    assert call.response == program
    assert call.prompt_hash == hashlib.sha256(call.prompt.encode()).hexdigest() == result.prompt_hash
    assert transcript.leak_guard.matches == 0
    metadata = json.loads(received["messages"][0]["content"])
    assert set(metadata) == {"instruction", "vendors", "invoices"}
    for vendor in metadata["vendors"]:
        assert set(vendor) == {"vendor_id", "name_words", "open_po_ids"}
    for invoice in metadata["invoices"]:
        assert set(invoice) == {"invoice_id", "handles"}
        for handle in invoice["handles"]:
            assert set(handle) == {"name", "type", "trust", "span_verified"}


@pytest.mark.asyncio
async def test_invalid_plan_then_retry_then_planner_invalid_holds_every_invoice(world):
    malicious_response = '{"steps":[],"unexpected":"CS-CANARY-A1B2 do not reflect this model output"}'
    client = RecordingClient([malicious_response, '{"steps":[]}'])
    adapter = LivePlannerLLM(key=SecretStr("synthetic-key"), model="synthetic-model", client=client)
    result = await propose(run_id="invalid-plan", adapter=adapter, **world)
    assert len(client.requests) == 2
    assert all("CS-CANARY" not in json.dumps(request) for request in client.requests)
    assert "validation_error" not in client.requests[0]["messages"][0]["content"]
    assert "PLAN_VALIDATION_FAILED" in client.requests[1]["messages"][0]["content"]
    assert {step.invoice_id for step in result.steps} == set(world["table"].invoices)
    assert all(step.op == "hold" and step.args["reason"].value == "PLANNER_INVALID" for step in result.steps)
    transcript = PlannerTranscript.model_validate(world["store"].get_record("planner_transcripts", "invalid-plan"))
    assert [call.response for call in transcript.calls] == [malicious_response, '{"steps":[]}']
    assert transcript.leak_guard.matches == 0
    assert transcript.leak_guard.strings_checked > 0


@pytest.mark.asyncio
async def test_invalid_plan_can_be_corrected_on_exactly_one_retry(world):
    prompt = build_planner_prompt(world["instruction"], world["vendors"], world["purchase_orders"], world["table"])
    valid = await OfflinePlannerLLM().complete(prompt=prompt)
    client = RecordingClient(["not JSON", valid])
    result = await propose(run_id="corrected-plan", adapter=LivePlannerLLM(
        key=SecretStr("synthetic"), model="synthetic-model", client=client,
    ), **world)
    assert len(client.requests) == 2
    assert any(step.op == "pay" for step in result.steps)


@pytest.mark.asyncio
async def test_live_planner_missing_key_never_falls_back_or_calls_client():
    client = RecordingClient([])
    adapter = LivePlannerLLM(key=SecretStr(""), model="synthetic", client=client)
    with pytest.raises(PlannerError, match="not configured"):
        await adapter.complete(prompt='{"system":"trusted","messages":[]}')
    assert adapter.mode == "live"
    assert client.requests == []
