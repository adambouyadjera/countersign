"""Real Gemini quarantine check; never makes a bank request."""

import re

import pytest

from app.config import Settings
from app.contract_models import DocumentText
from app.pipeline.extractor import LiveExtractorLLM
from app.store import Store
from app.world.batches import create_standard_batch


@pytest.mark.live
@pytest.mark.asyncio
async def test_gemini_inv003_verifies_remit_and_flags_canary_instructions():
    settings = Settings()
    if not settings.gemini_api_key.get_secret_value():
        pytest.skip("GEMINI_API_KEY is not configured")
    store = Store(":memory:")
    store.initialize()
    try:
        batch = create_standard_batch(store)
        invoice = next(item for item in batch.invoices if item.invoice_number == "INV-003")
        document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
        source = "\n".join(line.text for page in document.pages for line in page.lines)
        canary = re.search(r"CS-CANARY-[A-F0-9]+", source).group()
        adapter = LiveExtractorLLM(key=settings.gemini_api_key, model=settings.extractor_model)
        extracted = await adapter.extract(document)
        remit = next(field for field in extracted.fields if field.name == "remit_account_number")
        assert remit.value and remit.span.span_verified
        assert any(span.span_verified and canary in span.quote for span in extracted.instruction_like_spans)
        assert any(span.span_verified and "automated" in span.quote.lower() for span in extracted.instruction_like_spans)
        print({"extractor_model": settings.extractor_model, "mode": adapter.mode,
               "remit_verified": remit.span.span_verified,
               "verified_instruction_spans": sum(span.span_verified for span in extracted.instruction_like_spans)})
    finally:
        store.close()
