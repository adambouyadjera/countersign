"""Quarantined Haiku transport uses the exact prompt, schema, and verifier."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from anthropic import transform_schema
from pydantic import SecretStr
import pytest

from app.adapters.haiku_extractor import LiveHaikuExtractor
from app.adapters import haiku_extractor as module
from app.contract_models import DocumentText
from app.pipeline.extraction_models import RawExtraction
from app.pipeline.extractor import EXTRACTION_SYSTEM_PROMPT, ExtractorError
from app.pipeline.offline_extractor import parse_offline
from app.store import Store
from app.world.batches import create_standard_batch


@pytest.fixture
def source():
    store = Store(":memory:")
    store.initialize()
    try:
        invoice = create_standard_batch(store).invoices[2]
        document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
        return document, parse_offline(document)
    finally:
        store.close()


def message(raw, *, blocks=None, reason="end_turn", model="claude-haiku-test"):
    return SimpleNamespace(model=model, stop_reason=reason,
        content=blocks if blocks is not None else [SimpleNamespace(type="text", text=raw.model_dump_json())])


def fake_client(response=None, *, effects=None):
    return SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=response, side_effect=effects)))


def adapter(client=None, transport=None):
    return LiveHaikuExtractor(key=SecretStr("synthetic-key"), model="claude-haiku-test", client=client, transport=transport)


@pytest.mark.asyncio
async def test_sdk_request_is_same_quarantined_prompt_and_schema_with_no_tools(source):
    document, raw = source
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.path == "/v1/messages" and request.method == "POST"
        assert request.headers["x-api-key"] == "synthetic-key"
        body = json.loads(request.content)
        assert body["model"] == "claude-haiku-test" and body["max_tokens"] == 8192
        assert body["system"] == EXTRACTION_SYSTEM_PROMPT
        assert json.loads(body["messages"][0]["content"]) == document.model_dump(mode="json")
        assert body["tools"] == [] and "tool_choice" not in body
        assert body["output_config"]["format"] == {
            "type": "json_schema", "schema": transform_schema(RawExtraction),
        }
        wire_page = body["output_config"]["format"]["schema"]["$defs"]["RawSpan"]["properties"]["page"]
        assert "minimum" not in wire_page
        assert RawExtraction.model_json_schema()["$defs"]["RawSpan"]["properties"]["page"]["minimum"] == 1
        return httpx.Response(200, json={"id": "msg-test", "type": "message", "role": "assistant",
            "model": "claude-haiku-test-resolved", "stop_reason": "end_turn", "stop_sequence": None,
            "content": [{"type": "text", "text": raw.model_dump_json()}],
            "usage": {"input_tokens": 1, "output_tokens": 1}})

    service = adapter(transport=httpx.MockTransport(respond))
    result = await service.extract(document)
    assert len(requests) == 1
    assert result.extractor_model == "claude-haiku-test-resolved"
    assert service.mode == "live" and service.provider == "anthropic"
    assert service.http_observations[0]["http_status"] == 200
    assert all(field.trust == "untrusted" for field in result.fields)
    assert all(field.span.span_verified for field in result.fields)
    assert all(span.span_verified for span in result.instruction_like_spans)


@pytest.mark.asyncio
async def test_haiku_cannot_self_certify_fabricated_document_span(source):
    document, raw = source
    raw.remit_account_number.quote = "This bank account was never printed."
    result = await adapter(fake_client(message(raw))).extract(document)
    field = next(item for item in result.fields if item.name == "remit_account_number")
    assert field.span.span_verified is False
    assert next(handle for handle in result.handles if handle.name.endswith(".remit_account_number")).span_verified is False


@pytest.mark.asyncio
async def test_thinking_never_substitutes_for_final_output(source):
    document, raw = source
    private = raw.model_dump_json()
    client = fake_client(message(raw, blocks=[SimpleNamespace(type="thinking", text=private, thinking=private)]))
    with pytest.raises(ExtractorError, match="schema") as failure:
        await adapter(client).extract(document)
    assert failure.value.attempts == 3
    assert client.messages.create.await_count == 3
    assert "CS-CANARY" not in str(failure.value)


@pytest.mark.asyncio
async def test_thinking_is_ignored_when_final_valid_json_exists(source):
    document, raw = source
    blocks = [SimpleNamespace(type="thinking", thinking="Private reasoning must never appear."),
              SimpleNamespace(type="text", text=raw.model_dump_json())]
    result = await adapter(fake_client(message(raw, blocks=blocks))).extract(document)
    assert "Private reasoning" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,blocks", [
    ("max_tokens", None),
    ("refusal", None),
    ("tool_use", [SimpleNamespace(type="tool_use", name="pay", input={})]),
    ("end_turn", [SimpleNamespace(type="tool_use", name="pay", input={})]),
    ("end_turn", [SimpleNamespace(type="text", text="{}")]),
])
async def test_invalid_or_truncated_output_fails_after_bounded_attempts(source, reason, blocks):
    document, raw = source
    client = fake_client(message(raw, reason=reason, blocks=blocks))
    with pytest.raises(ExtractorError) as failure:
        await adapter(client).extract(document)
    assert failure.value.stage == "haiku"
    assert failure.value.attempts == 3
    assert client.messages.create.await_count == 3


@pytest.mark.asyncio
async def test_strict_local_schema_rejects_added_authority_fields(source):
    document, raw = source
    malicious = raw.model_dump()
    malicious["trusted"] = True
    client = fake_client(message(raw, blocks=[SimpleNamespace(type="text", text=json.dumps(malicious))]))
    with pytest.raises(ExtractorError, match="schema"):
        await adapter(client).extract(document)


@pytest.mark.asyncio
async def test_wire_schema_compatibility_does_not_relax_local_positive_span_coordinates(source):
    document, raw = source
    invalid = raw.model_dump()
    invalid["amount_due_cents"]["page"] = 0
    client = fake_client(message(raw, blocks=[SimpleNamespace(type="text", text=json.dumps(invalid))]))
    with pytest.raises(ExtractorError, match="schema") as failure:
        await adapter(client).extract(document)
    assert failure.value.cause == "invalid_schema"
    assert client.messages.create.await_count == 3


@pytest.mark.asyncio
async def test_transient_error_retries_same_request_and_keeps_safe_status(source):
    document, _ = source
    failure = type("Unavailable", (Exception,), {"status_code": 503})("synthetic-private-key")
    client = fake_client(effects=failure)
    service = adapter(client)
    delays = []
    async def record(seconds):
        delays.append(seconds)
    service._sleep = record
    with pytest.raises(ExtractorError) as caught:
        await service.extract(document)
    assert caught.value.http_status == 503 and caught.value.attempts == 3
    assert delays == [1, 2]
    assert client.messages.create.await_args_list[0] == client.messages.create.await_args_list[2]
    assert "synthetic-private-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_authorization_failure_is_not_retried_or_replaced(source):
    failure = type("Unauthorized", (Exception,), {"status_code": 401})("synthetic-private-key")
    client = fake_client(effects=failure)
    with pytest.raises(ExtractorError) as caught:
        await adapter(client).extract(source[0])
    assert caught.value.http_status == 401 and caught.value.attempts == 1
    client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_credential_does_not_call_provider(source):
    client = fake_client()
    service = LiveHaikuExtractor(key=SecretStr(""), model="claude-haiku-test", client=client)
    with pytest.raises(ExtractorError, match="not configured"):
        await service.extract(source[0])
    client.messages.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeouts_are_bounded_and_propagate_safe_failure(source, monkeypatch):
    async def never(**kwargs):
        await asyncio.Future()
    client = fake_client()
    client.messages.create = AsyncMock(side_effect=never)
    service = adapter(client)
    monkeypatch.setattr(module, "HAIKU_TIMEOUT_SECONDS", .001)
    service._sleep = AsyncMock()
    with pytest.raises(ExtractorError) as failure:
        await service.extract(source[0])
    assert failure.value.cause == "timeout" and failure.value.attempts == 3
