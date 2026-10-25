"""Red-team extraction reserves Gemini quota and rechecks cached claims."""

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock

import pytest

from app.adapters.extraction import EXTRACTION_PROMPT_VERSION, ResilientExtractor
from app.contract_models import DocumentText, ServiceHealth
from app.pipeline.extractor import ExtractorError, OfflineExtractorLLM
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload
from app.redteam.extractor import HaikuOnlyExtractor
from app.security.extraction_cache import ExtractionCache, ExtractionCacheError, document_sha256
from app.store import Store
from app.world.batches import create_standard_batch


MODEL = "haiku-redteam-test"


class ScriptedProvider:
    mode = "live"

    def __init__(self, *, model, forbidden=False, gate=None, entered=None, error=None):
        self.model, self.forbidden = model, forbidden
        self.gate, self.entered, self.error = gate, entered, error
        self.documents = []
        self.health_check = AsyncMock(return_value=ServiceHealth(status="ok", mode="live"))

    async def extract(self, document):
        if self.forbidden:
            raise AssertionError("Red-team work must never call Gemini")
        self.documents.append(document)
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return to_payload(document, parse_offline(document), self.model)


class RecordingDelegate(ResilientExtractor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []

    async def extract(self, document, **kwargs):
        self.requests.append(kwargs)
        return await super().extract(document, **kwargs)


@pytest.fixture
def world(tmp_path):
    store = Store(":memory:")
    store.initialize()
    batch = create_standard_batch(store)
    record = store.get_record("documents", batch.invoices[0].document_id)
    document = DocumentText.model_validate(record["text"])
    cache = ExtractionCache(tmp_path / "redteam-haiku-only.db")
    try:
        yield {"store": store, "record": record, "document": document, "cache": cache,
               "pdf": base64.b64decode(record["pdf_base64"])}
    finally:
        cache.close()
        store.close()


def build(world, *, store=None, cache=None, locks=None, model=MODEL, **provider_options):
    primary = ScriptedProvider(model="gemini-forbidden", forbidden=True)
    fallback = ScriptedProvider(model=model, **provider_options)
    delegate = RecordingDelegate(primary, fallback, store or world["store"], cache=None)
    wrapper = HaikuOnlyExtractor(delegate=delegate, cache=cache or world["cache"], locks=locks)
    return wrapper, delegate, primary, fallback


@pytest.mark.asyncio
async def test_live_calls_force_haiku_and_bypass_every_delegate_cache(world):
    wrapper, delegate, primary, fallback = build(world)
    payload = await wrapper.extract(world["document"])
    assert delegate.requests == [{"force_haiku": True, "bypass_cache": True}]
    assert primary.documents == [] and len(fallback.documents) == 1
    assert wrapper.mode == "live" and wrapper.provider == "anthropic" and wrapper.model == MODEL
    origin = payload.extraction_provenance
    assert origin.provider == "anthropic" and origin.model == MODEL and origin.cached is False
    assert [(attempt.provider, attempt.outcome) for attempt in origin.attempts] == [
        ("gemini", "skipped"), ("anthropic", "success")]
    receipt = wrapper.receipts[0]
    assert receipt["source"] == "provider" and receipt["provider_call"] and receipt["cache_written"]
    assert receipt["cached"] is False and receipt["status"] == "success"
    json.dumps(wrapper.receipts, allow_nan=False)


@pytest.mark.asyncio
async def test_health_probes_only_the_actual_anthropic_fallback(world):
    wrapper, delegate, primary, fallback = build(world)
    assert (await wrapper.health_check()).status == "ok"
    fallback.health_check.assert_awaited_once_with()
    primary.health_check.assert_not_awaited()
    assert delegate.requests == []


@pytest.mark.asyncio
async def test_cache_hit_preserves_origin_time_and_rebinds_every_span_to_new_ids(world):
    wrapper, delegate, _, fallback = build(world)
    fresh = await wrapper.extract(world["document"])
    current = world["document"].model_copy(update={"document_id": "new-redteam-document", "invoice_id": "new-redteam-invoice"})
    record = deepcopy(world["record"])
    record.update(document_id=current.document_id, invoice_id=current.invoice_id, text=current.model_dump(mode="json"))
    world["store"].save_record("documents", current.document_id, record)
    cached = await wrapper.extract(current)
    assert len(delegate.requests) == len(fallback.documents) == 1
    assert cached.invoice_id == current.invoice_id
    assert cached.extraction_provenance.cached is True
    assert cached.extraction_provenance.provider == "anthropic" and cached.extractor_model == MODEL
    assert cached.extraction_provenance.extracted_at == fresh.extraction_provenance.extracted_at
    assert cached.extraction_provenance.document_sha256 == document_sha256(world["pdf"])
    for field in cached.fields:
        assert field.trust == "untrusted"
        if field.span is not None:
            assert field.span.document_id == current.document_id and field.span.span_verified
    assert cached.handles == []
    receipt = wrapper.receipts[-1]
    assert receipt["source"] == "cache" and receipt["cached"] is True
    assert receipt["provider_call"] is False and receipt["cache_written"] is False
    assert receipt["extracted_at"] == wrapper.receipts[0]["extracted_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", [("gemini", MODEL), ("anthropic", "different-haiku-model")])
async def test_cache_entries_from_wrong_provider_or_model_are_not_reused(world, provider, model):
    document = world["document"]
    wrong = to_payload(document, parse_offline(document), model)
    world["cache"].put(document_sha256(world["pdf"]), EXTRACTION_PROMPT_VERSION, wrong,
                       provider=provider, model=model, extracted_at=datetime.now(timezone.utc))
    wrapper, delegate, primary, fallback = build(world)
    result = await wrapper.extract(document)
    assert result.extraction_provenance.cached is False
    assert result.extraction_provenance.model == MODEL and result.extraction_provenance.provider == "anthropic"
    assert len(delegate.requests) == len(fallback.documents) == 1 and primary.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["span", "amount"])
async def test_cached_claims_require_deterministic_span_and_value_reverification(world, corruption):
    document = world["document"]
    poisoned = to_payload(document, parse_offline(document), MODEL)
    if corruption == "span":
        poisoned.fields[0].span.quote = "Fabricated quote absent from this invoice"
        poisoned.fields[0].span.span_verified = True
    else:
        amount = next(field for field in poisoned.fields if field.name == "amount_due_cents")
        amount.value += 1
    world["cache"].put(document_sha256(world["pdf"]), EXTRACTION_PROMPT_VERSION, poisoned,
                       provider="anthropic", model=MODEL, extracted_at=datetime.now(timezone.utc))
    wrapper, delegate, _, _ = build(world)
    fresh = await wrapper.extract(document)
    assert fresh.extraction_provenance.cached is False and len(delegate.requests) == 1
    assert all(field.span.span_verified for field in fresh.fields if field.value is not None)
    replay = await wrapper.extract(document)
    assert replay.extraction_provenance.cached is True and len(delegate.requests) == 1


@pytest.mark.asyncio
async def test_shared_locks_and_cache_extract_identical_controls_once_across_four_cases(world):
    locks, gate, entered = {}, asyncio.Event(), asyncio.Event()
    cases, stores = [], []
    try:
        for number in range(4):
            store = Store(":memory:")
            store.initialize()
            stores.append(store)
            document = world["document"].model_copy(update={
                "document_id": f"case-{number}-document", "invoice_id": f"case-{number}-invoice"})
            record = deepcopy(world["record"])
            record.update(document_id=document.document_id, invoice_id=document.invoice_id,
                          text=document.model_dump(mode="json"))
            store.save_record("documents", document.document_id, record)
            wrapped, delegate, primary, fallback = build(world, store=store, locks=locks, gate=gate, entered=entered)
            cases.append((wrapped, delegate, primary, fallback, document))
        tasks = [asyncio.create_task(wrapped.extract(document)) for wrapped, _, _, _, document in cases]
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert sum(len(case[3].documents) for case in cases) == 1
        gate.set()
        results = await asyncio.gather(*tasks)
        assert sum(len(case[1].requests) for case in cases) == 1
        assert sum(result.extraction_provenance.cached for result in results) == 3
        assert len(locks) == 1
        for result, (wrapped, _, primary, _, document) in zip(results, cases):
            assert result.invoice_id == document.invoice_id and primary.documents == []
            assert wrapped.locks is locks
            assert all(field.span.document_id == document.document_id and field.span.span_verified
                       for field in result.fields if field.span is not None)
    finally:
        for store in stores:
            store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["provider", "model", "cached", "digest", "version", "key", "payload_model"])
async def test_fresh_result_must_report_matching_actual_haiku_origin(world, monkeypatch, change):
    wrapper, delegate, _, _ = build(world)
    extract = delegate.extract

    async def altered(document, **kwargs):
        result = await extract(document, **kwargs)
        origin = result.extraction_provenance
        if change == "provider":
            origin.provider = "gemini"
        elif change == "model":
            origin.model = "other-model"
        elif change == "cached":
            origin.cached = True
        elif change == "digest":
            origin.document_sha256 = "0" * 64
        elif change == "version":
            origin.prompt_version = "different-prompt"
        elif change == "key":
            origin.cache_key = "wrong-key"
        else:
            result.extractor_model = "other-model"
        return result

    monkeypatch.setattr(delegate, "extract", altered)
    with pytest.raises(ExtractorError) as caught:
        await wrapper.extract(world["document"])
    assert caught.value.cause == "invalid_schema"
    assert world["cache"].get(document_sha256(world["pdf"]), EXTRACTION_PROMPT_VERSION) is None
    assert wrapper.receipts[0]["status"] == "error" and wrapper.receipts[0]["cached"] is False


@pytest.mark.asyncio
async def test_source_text_mismatch_is_rejected_before_cache_or_haiku(world):
    wrapper, delegate, _, fallback = build(world)
    await wrapper.extract(world["document"])
    changed = world["document"].model_copy(deep=True)
    changed.pages[0].lines[0].text = "Altered text does not match the PDF bytes"
    with pytest.raises(ExtractorError) as caught:
        await wrapper.extract(changed)
    assert caught.value.cause == "source_mismatch"
    assert len(delegate.requests) == len(fallback.documents) == 1
    assert wrapper.receipts[-1]["provider_call"] is False and wrapper.receipts[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_haiku_failure_remains_failure_and_does_not_spend_gemini_or_fill_cache(world):
    failure = ExtractorError("safe synthetic failure", cause="request", http_status=503)
    wrapper, delegate, primary, fallback = build(world, error=failure)
    with pytest.raises(ExtractorError):
        await wrapper.extract(world["document"])
    assert len(fallback.documents) == len(delegate.requests) == 1 and primary.documents == []
    assert world["cache"].get(document_sha256(world["pdf"]), EXTRACTION_PROMPT_VERSION) is None
    assert "safe synthetic failure" not in json.dumps(wrapper.receipts)


@pytest.mark.asyncio
async def test_optional_cache_write_failure_keeps_fresh_verified_result(world, monkeypatch):
    wrapper, _, _, fallback = build(world)

    def unavailable(*args, **kwargs):
        raise ExtractionCacheError("safe cache failure")

    monkeypatch.setattr(world["cache"], "put", unavailable)
    result = await wrapper.extract(world["document"])
    assert result.extraction_provenance.cached is False
    assert wrapper.receipts[0]["cache_written"] is False and wrapper.receipts[0]["status"] == "success"
    assert len(fallback.documents) == 1


@pytest.mark.asyncio
async def test_explicit_offline_delegate_is_never_reported_as_haiku(world, monkeypatch):
    offline = OfflineExtractorLLM()
    wrapper = HaikuOnlyExtractor(delegate=offline, cache=world["cache"], locks={})

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline fakes must not use the Haiku cache")

    monkeypatch.setattr(world["cache"], "get", forbidden)
    result = await wrapper.extract(world["document"])
    assert result.extractor_model == offline.model and result.extraction_provenance is None
    assert wrapper.mode == wrapper.provider == "offline" and wrapper.model == offline.model
    assert (await wrapper.health_check()).mode == "offline"
    assert wrapper.receipts[0]["source"] == "offline" and wrapper.receipts[0]["provider"] == "offline"
    assert wrapper.receipts[0]["provider_call"] is False and wrapper.receipts[0]["cached"] is False


@pytest.mark.asyncio
async def test_case_close_never_closes_job_global_cache(world):
    wrapper, delegate, _, _ = build(world)
    await wrapper.extract(world["document"])
    connection = world["cache"]._connection
    await wrapper.aclose()
    assert world["cache"]._connection is connection
    assert world["cache"].get(document_sha256(world["pdf"]), EXTRACTION_PROMPT_VERSION) is not None
    # Protect the shared resource even if a caller accidentally supplied it to
    # the delegate as well; ResilientExtractor.aclose would close its cache.
    delegate.cache = world["cache"]
    await wrapper.aclose()
    assert world["cache"]._connection is connection
