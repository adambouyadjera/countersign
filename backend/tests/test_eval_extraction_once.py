"""Fixed-suite reruns cannot consume a second fresh extraction for one PDF."""

import asyncio
import base64
from copy import deepcopy

import pytest

from app.adapters.extraction import ResilientExtractor
from app.contract_models import DocumentText
from app.evaluation.extraction_once import OncePerDocumentExtractor
from app.pipeline.extractor import ExtractorError
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload
from app.security.extraction_cache import ExtractionCache
from app.store import Store
from app.world.batches import create_standard_batch


class ScriptedProvider:
    mode = "live"

    def __init__(self, error=None):
        self.model = "gemini-scripted-test"
        self.calls = 0
        self.error = error
        self.entered = asyncio.Event()
        self.resume = None

    async def extract(self, document):
        self.calls += 1
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        return to_payload(document, parse_offline(document), self.model)


@pytest.fixture
def world(tmp_path):
    source = Store(tmp_path / "source.db")
    receipts = Store(tmp_path / "receipts.db")
    source.initialize()
    receipts.initialize()
    cache = ExtractionCache(tmp_path / "cache.db")
    batch = create_standard_batch(source)
    invoice = batch.invoices[0]
    record = source.get_record("documents", invoice.document_id)
    document = DocumentText.model_validate(record["text"])
    primary = ScriptedProvider()
    fallback = ScriptedProvider()
    fallback.model = "haiku-scripted-test"
    delegate = ResilientExtractor(primary, fallback, source, cache)
    try:
        yield source, receipts, cache, delegate, primary, document, record
    finally:
        source.close()
        receipts.close()
        cache.close()


@pytest.mark.asyncio
async def test_first_attempt_is_claimed_before_the_single_delegate_call(world):
    _, receipts, _, delegate, primary, document, _ = world
    extractor = OncePerDocumentExtractor(delegate, receipts)
    primary.resume = asyncio.Event()
    task = asyncio.create_task(extractor.extract(document))
    await primary.entered.wait()
    row = receipts.list_records("eval_receipts")[0]
    assert row["status"] == "started" and row["owner"]
    primary.resume.set()
    payload = await task
    assert payload.extraction_provenance.cached is False
    assert primary.calls == 1
    accepted = receipts.list_records("eval_receipts")[0]
    assert accepted["status"] == "accepted"
    assert not {"fields", "handles", "payload", "instruction_like_spans"} & accepted.keys()
    assert extractor.receipt_events[0]["event"] == "first_attempt"
    assert extractor.receipt_events[0]["delegate_called"] is True


@pytest.mark.asyncio
async def test_rerun_rebinds_cache_and_retains_original_producer_and_time_across_restart(world):
    source, receipts, cache, delegate, primary, document, record = world
    first = await OncePerDocumentExtractor(delegate, receipts).extract(document)
    rebound = document.model_copy(update={"document_id": "new-document", "invoice_id": "new-invoice"})
    other_record = deepcopy(record)
    other_record.update(document_id=rebound.document_id, invoice_id=rebound.invoice_id,
                        text=rebound.model_dump(mode="json"))
    source.save_record("documents", rebound.document_id, other_record)
    receipts.close()
    receipts.initialize()
    cache.close()
    wrapper = OncePerDocumentExtractor(delegate, receipts)
    result = await wrapper.extract(rebound)
    assert primary.calls == 1
    assert result.invoice_id == rebound.invoice_id
    assert result.extraction_provenance.cached is True
    assert result.extraction_provenance.extracted_at == first.extraction_provenance.extracted_at
    assert result.extraction_provenance.provider == first.extraction_provenance.provider
    assert result.extraction_provenance.model == first.extraction_provenance.model
    assert all(field.span.document_id == rebound.document_id and field.span.span_verified
               for field in result.fields if field.value is not None)
    assert wrapper.receipt_events[0]["event"] == "cache_hit"
    assert wrapper.receipt_events[0]["delegate_called"] is False


@pytest.mark.asyncio
async def test_failed_receipt_never_retries_and_does_not_store_provider_response(world):
    _, receipts, _, delegate, primary, document, _ = world
    primary.error = ExtractorError("raw provider body must not survive", stage="gemini", cause="configuration", http_status=401)
    first = OncePerDocumentExtractor(delegate, receipts)
    with pytest.raises(ExtractorError):
        await first.extract(document)
    wrapper = OncePerDocumentExtractor(delegate, receipts)
    with pytest.raises(ExtractorError) as caught:
        await wrapper.extract(document)
    assert primary.calls == 1
    assert caught.value.cached_failure is True
    assert caught.value.cause == "providers_exhausted"
    assert caught.value.http_status == 401
    assert wrapper.receipt_events[0]["event"] == "reused_failure"
    assert "raw provider body" not in str(receipts.list_records("eval_receipts"))


@pytest.mark.asyncio
async def test_unexpected_provider_exception_body_is_not_retained_or_repeated(world):
    _, receipts, _, delegate, _, document, _ = world
    calls = []

    async def broken_provider(document):
        calls.append(document.document_id)
        raise RuntimeError("private-provider-body-and-credentials-must-not-be-saved")

    delegate.extract = broken_provider
    wrapper = OncePerDocumentExtractor(delegate, receipts)
    with pytest.raises(ExtractorError) as first:
        await wrapper.extract(document)
    with pytest.raises(ExtractorError) as second:
        await wrapper.extract(document)
    assert len(calls) == 1
    assert first.value.cause == second.value.cause == "unexpected_error"
    assert second.value.cached_failure is True
    assert "private-provider" not in str(first.value)
    assert "private-provider" not in str(receipts.list_records("eval_receipts"))
    assert "private-provider" not in str(wrapper.receipt_events)


@pytest.mark.asyncio
async def test_accepted_receipt_with_deleted_cache_fails_closed_without_new_call(world):
    _, receipts, cache, delegate, primary, document, _ = world
    await OncePerDocumentExtractor(delegate, receipts).extract(document)
    cache._db().execute("DELETE FROM extraction_cache_v1")
    wrapper = OncePerDocumentExtractor(delegate, receipts)
    with pytest.raises(ExtractorError, match="missing or invalid") as caught:
        await wrapper.extract(document)
    assert primary.calls == 1 and caught.value.cause == "eval_cache_unavailable"


@pytest.mark.asyncio
async def test_accepted_receipt_with_unverified_cache_fails_closed_without_new_call(world):
    _, receipts, cache, delegate, primary, document, _ = world
    first = await OncePerDocumentExtractor(delegate, receipts).extract(document)
    amount = next(field for field in first.fields if field.name == "amount_due_cents")
    amount.value += 1
    origin = first.extraction_provenance
    cache.put(origin.document_sha256, origin.prompt_version, first,
              provider=origin.provider, model=origin.model, extracted_at=origin.extracted_at)
    with pytest.raises(ExtractorError) as caught:
        await OncePerDocumentExtractor(delegate, receipts).extract(document)
    assert caught.value.cause == "eval_cache_invalid" and primary.calls == 1


@pytest.mark.asyncio
async def test_concurrent_receipt_blocks_a_second_fresh_call(world):
    _, receipts, _, delegate, primary, document, _ = world
    primary.resume = asyncio.Event()
    first = asyncio.create_task(OncePerDocumentExtractor(delegate, receipts).extract(document))
    await primary.entered.wait()
    second = OncePerDocumentExtractor(delegate, receipts)
    with pytest.raises(ExtractorError) as caught:
        await second.extract(document)
    assert caught.value.cause == "extraction_in_progress"
    assert primary.calls == 1
    primary.resume.set()
    await first


@pytest.mark.asyncio
async def test_cancellation_receipt_prevents_an_automatic_later_retry(world):
    _, receipts, _, delegate, primary, document, _ = world
    primary.resume = asyncio.Event()
    task = asyncio.create_task(OncePerDocumentExtractor(delegate, receipts).extract(document))
    await primary.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ExtractorError) as caught:
        await OncePerDocumentExtractor(delegate, receipts).extract(document)
    assert caught.value.cached_failure is True and caught.value.cause == "cancelled"
    assert primary.calls == 1


@pytest.mark.asyncio
async def test_live_cache_off_does_not_spend_the_first_attempt(world):
    _, receipts, _, delegate, primary, document, _ = world
    delegate.cache = None
    with pytest.raises(ExtractorError, match="requires the verified extraction cache"):
        await OncePerDocumentExtractor(delegate, receipts).extract(document)
    assert primary.calls == 0 and receipts.list_records("eval_receipts") == []


@pytest.mark.asyncio
async def test_distinct_pdf_bytes_are_distinct_quota_documents(world):
    source, receipts, _, delegate, primary, document, record = world
    await OncePerDocumentExtractor(delegate, receipts).extract(document)
    changed = deepcopy(record)
    changed["pdf_base64"] = base64.b64encode(base64.b64decode(changed["pdf_base64"]) + b"\n% other bytes\n").decode()
    source.save_record("documents", document.document_id, changed)
    await OncePerDocumentExtractor(delegate, receipts).extract(document)
    assert primary.calls == 2 and len(receipts.list_records("eval_receipts")) == 2


@pytest.mark.asyncio
async def test_offline_delegate_is_explicit_and_does_not_claim_a_live_receipt(world):
    _, receipts, _, _, primary, document, _ = world
    primary.mode = "offline"
    wrapper = OncePerDocumentExtractor(primary, receipts)
    first = await wrapper.extract(document)
    second = await wrapper.extract(document)
    assert first == second and primary.calls == 2
    assert wrapper.mode == "offline" and wrapper.model == primary.model
    assert receipts.list_records("eval_receipts") == []
    assert [event["event"] for event in wrapper.receipt_events] == ["offline_passthrough", "offline_passthrough"]
