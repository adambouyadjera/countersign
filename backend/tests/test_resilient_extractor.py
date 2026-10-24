"""The quarantine chain and cache never bypass deterministic source checks."""

import base64
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest

from app.adapters.extraction import EXTRACTION_PROMPT_VERSION, ResilientExtractor
from app.contract_models import DocumentText
from app.pipeline.extractor import ExtractorError
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload
from app.security.extraction_cache import ExtractionCache, ExtractionCacheError, document_sha256
from app.store import Store
from app.world.batches import create_standard_batch


class ScriptedExtractor:
    mode = "live"

    def __init__(self, provider, model, order, *, error=None, transform=None):
        self.provider, self.model, self.order = provider, model, order
        self.error, self.transform = error, transform
        self.documents = []

    async def extract(self, document):
        self.documents.append(document.model_copy(deep=True))
        self.order.append(self.provider)
        if self.error is not None:
            raise self.error
        payload = to_payload(document, parse_offline(document), self.model)
        return self.transform(payload) if self.transform else payload


@pytest.fixture
def world():
    store = Store(":memory:")
    store.initialize()
    try:
        batch = create_standard_batch(store)
        invoice = batch.invoices[0]
        record = store.get_record("documents", invoice.document_id)
        document = DocumentText.model_validate(record["text"])
        yield store, document, record
    finally:
        store.close()


def chain(world, *, primary_error=None, fallback_error=None, primary_transform=None,
          fallback_transform=None, cache=None):
    order = []
    primary = ScriptedExtractor("gemini", "gemini-test", order,
                                error=primary_error, transform=primary_transform)
    fallback = ScriptedExtractor("anthropic", "haiku-test", order,
                                 error=fallback_error, transform=fallback_transform)
    return ResilientExtractor(primary, fallback, world[0], cache=cache), primary, fallback, order


def failure(status=None, cause="request"):
    return ExtractorError("Synthetic safe provider failure", http_status=status,
                          cause=cause, attempts=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,cause", [
    (429, "rate_limit"), (503, "request"), (None, "timeout"),
    (None, "invalid_schema"), (None, "output_limit"), (None, "span_verification"),
])
async def test_eligible_exhausted_primary_uses_haiku_after_primary(world, status, cause):
    adapter, primary, fallback, order = chain(world, primary_error=failure(status, cause))
    result = await adapter.extract(world[1])
    assert order == ["gemini", "anthropic"]
    assert len(primary.documents) == len(fallback.documents) == 1
    assert result.extractor_model == fallback.model
    provenance = result.extraction_provenance
    assert provenance.provider == "anthropic" and provenance.model == fallback.model
    assert provenance.cached is False
    assert [(attempt.provider, attempt.outcome) for attempt in provenance.attempts] == [
        ("gemini", "failed"), ("anthropic", "success")]
    assert all(field.span.span_verified for field in result.fields if field.value is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), httpx.ReadTimeout("Synthetic timeout")])
async def test_actual_timeout_exceptions_are_eligible_for_fallback(world, error):
    adapter, _, _, order = chain(world, primary_error=error)
    result = await adapter.extract(world[1])
    assert order == ["gemini", "anthropic"]
    assert result.extraction_provenance.attempts[0].error_code == "timeout"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,cause", [(401, "request"), (403, "request"), (None, "configuration")])
async def test_auth_or_configuration_errors_do_not_switch_providers(world, status, cause):
    adapter, _, fallback, order = chain(world, primary_error=failure(status, cause))
    with pytest.raises(ExtractorError) as caught:
        await adapter.extract(world[1])
    assert order == ["gemini"] and fallback.documents == []
    assert caught.value.cause == "providers_exhausted"


@pytest.mark.asyncio
async def test_primary_success_records_gemini_and_does_not_call_fallback(world):
    adapter, primary, fallback, order = chain(world)
    result = await adapter.extract(world[1])
    assert order == ["gemini"] and fallback.documents == []
    assert result.extraction_provenance.provider == "gemini"
    assert result.extraction_provenance.model == primary.model
    assert result.extraction_provenance.cached is False


def wrong_amount(payload):
    field = next(field for field in payload.fields if field.name == "amount_due_cents")
    field.value += 1
    return payload


def fabricated_span(payload):
    field = next(field for field in payload.fields if field.name == "remit_account_number")
    field.span.quote = "This source does not exist in the invoice"
    field.span.span_verified = True
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("transform", [wrong_amount, fabricated_span, lambda payload: {"fields": []}])
async def test_invalid_primary_schema_values_or_spans_use_verified_fallback(world, transform):
    adapter, _, fallback, order = chain(world, primary_transform=transform)
    result = await adapter.extract(world[1])
    assert order == ["gemini", "anthropic"]
    assert result.extraction_provenance.model == fallback.model
    assert all(field.span.span_verified for field in result.fields if field.value is not None)


@pytest.mark.asyncio
async def test_both_providers_fail_no_result_or_cache_entry_is_manufactured(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, _, _, order = chain(world, primary_error=failure(503),
                                     fallback_error=failure(503), cache=cache)
        with pytest.raises(ExtractorError) as caught:
            await adapter.extract(world[1])
        assert order == ["gemini", "anthropic"]
        assert caught.value.cause == "providers_exhausted"
        assert [(attempt.provider, attempt.outcome) for attempt in caught.value.extraction_attempts] == [
            ("gemini", "failed"), ("anthropic", "failed")]
        digest = document_sha256(base64.b64decode(world[2]["pdf_base64"]))
        assert cache.get(digest, EXTRACTION_PROMPT_VERSION) is None
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_invalid_haiku_span_is_rejected_instead_of_becoming_authority(world):
    adapter, _, _, order = chain(world, primary_error=failure(429), fallback_transform=fabricated_span)
    with pytest.raises(ExtractorError):
        await adapter.extract(world[1])
    assert order == ["gemini", "anthropic"]


@pytest.mark.asyncio
async def test_cache_hit_reuses_original_origin_timestamp_and_rebinds_new_ids(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, _, fallback, order = chain(world, primary_error=failure(429), cache=cache)
        original = await adapter.extract(world[1])
        current = world[1].model_copy(update={"document_id": "same-bytes-new-document", "invoice_id": "new-invoice"})
        record = deepcopy(world[2])
        record.update(document_id=current.document_id, invoice_id=current.invoice_id,
                      text=current.model_dump(mode="json"))
        world[0].save_record("documents", current.document_id, record)
        cached = await adapter.extract(current)
        assert order == ["gemini", "anthropic"]
        assert cached.invoice_id == current.invoice_id and cached.extractor_model == fallback.model
        provenance = cached.extraction_provenance
        assert provenance.cached is True and provenance.provider == "anthropic"
        assert provenance.model == original.extraction_provenance.model
        assert provenance.extracted_at == original.extraction_provenance.extracted_at
        assert provenance.document_sha256 == document_sha256(base64.b64decode(world[2]["pdf_base64"]))
        assert provenance.prompt_version == EXTRACTION_PROMPT_VERSION
        for field in cached.fields:
            if field.span is not None:
                assert field.span.document_id == current.document_id
                if field.value is not None:
                    assert field.span.span_verified is True
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_file_byte_change_misses_even_if_extracted_pages_are_unchanged(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, _, _, order = chain(world, cache=cache)
        first = await adapter.extract(world[1])
        record = deepcopy(world[2])
        pdf = base64.b64decode(record["pdf_base64"]) + b"\n% distinct document file bytes\n"
        record["pdf_base64"] = base64.b64encode(pdf).decode()
        world[0].save_record("documents", world[1].document_id, record)
        second = await adapter.extract(world[1])
        assert order == ["gemini", "gemini"]
        assert second.extraction_provenance.cached is False
        assert second.extraction_provenance.document_sha256 != first.extraction_provenance.document_sha256
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_previous_prompt_version_cannot_supply_cache_hit(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, primary, _, order = chain(world, cache=cache)
        payload = to_payload(world[1], parse_offline(world[1]), primary.model)
        cache.put(document_sha256(base64.b64decode(world[2]["pdf_base64"])), "older-prompt-version",
                  payload, provider="gemini", model=primary.model, extracted_at=datetime.now(timezone.utc))
        result = await adapter.extract(world[1])
        assert order == ["gemini"] and result.extraction_provenance.cached is False
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_poisoned_cached_amount_is_semantically_rechecked_and_repaired(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, primary, _, order = chain(world, cache=cache)
        poisoned = wrong_amount(to_payload(world[1], parse_offline(world[1]), primary.model))
        cache.put(document_sha256(base64.b64decode(world[2]["pdf_base64"])), EXTRACTION_PROMPT_VERSION,
                  poisoned, provider="gemini", model=primary.model, extracted_at=datetime.now(timezone.utc))
        result = await adapter.extract(world[1])
        assert order == ["gemini"] and result.extraction_provenance.cached is False
        original = parse_offline(world[1]).amount_due_cents.value
        assert next(field.value for field in result.fields if field.name == "amount_due_cents") == original
        replay = await adapter.extract(world[1])
        assert replay.extraction_provenance.cached and order == ["gemini"]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_forced_haiku_bypasses_primary_and_existing_cache(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, _, fallback, order = chain(world, cache=cache)
        await adapter.extract(world[1])
        result = await adapter.extract(world[1], force_haiku=True)
        assert order == ["gemini", "anthropic"]
        assert result.extractor_model == fallback.model and result.extraction_provenance.cached is False
        assert [(attempt.provider, attempt.outcome) for attempt in result.extraction_provenance.attempts] == [
            ("gemini", "skipped"), ("anthropic", "success")]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_bypass_cache_makes_a_fresh_call_without_changing_chain(world):
    cache = ExtractionCache(":memory:")
    try:
        adapter, _, _, order = chain(world, cache=cache)
        await adapter.extract(world[1])
        result = await adapter.extract(world[1], bypass_cache=True)
        assert order == ["gemini", "gemini"] and result.extraction_provenance.cached is False
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_cache_off_never_reuses_previous_result(world):
    adapter, _, _, order = chain(world, cache=None)
    first = await adapter.extract(world[1])
    second = await adapter.extract(world[1])
    assert order == ["gemini", "gemini"]
    assert not first.extraction_provenance.cached and not second.extraction_provenance.cached


@pytest.mark.asyncio
async def test_cache_write_failure_retains_fresh_verified_origin(world):
    class BrokenWriteCache:
        def get(self, *args):
            return None

        def put(self, *args, **kwargs):
            raise ExtractionCacheError("Synthetic cache write failure")

    adapter, _, _, order = chain(world, cache=BrokenWriteCache())
    result = await adapter.extract(world[1])
    assert order == ["gemini"]
    assert result.extraction_provenance.cached is False and result.extraction_provenance.provider == "gemini"
    assert all(field.span.span_verified for field in result.fields if field.value is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing_bytes", "mismatched_text"])
async def test_original_file_bytes_are_required_and_must_match_text(world, mutation):
    adapter, _, _, order = chain(world)
    record = deepcopy(world[2])
    document = world[1]
    if mutation == "missing_bytes":
        record.pop("pdf_base64")
    else:
        document = document.model_copy(deep=True)
        document.pages[0].lines[0].text = "Tampered text that is not in the PDF"
        record["text"] = document.model_dump(mode="json")
    world[0].save_record("documents", document.document_id, record)
    with pytest.raises(ExtractorError) as caught:
        await adapter.extract(document)
    assert caught.value.cause == "source_mismatch" and order == []
