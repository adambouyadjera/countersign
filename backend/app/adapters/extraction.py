"""Quarantined extraction: verified cache, Gemini, then explicit Haiku fallback."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import logging

import httpx
from pydantic import ValidationError

from app.contract_models import DocumentText, ExtractionAttempt, ExtractionProvenance, FieldsExtractedPayload
from app.pipeline.extraction_models import RawExtraction
from app.pipeline.extractor import EXTRACTION_SYSTEM_PROMPT, ExtractorError
from app.pipeline.spans import field_source_verified, span_source_verified, verify_span
from app.security.extraction_cache import ExtractionCache, ExtractionCacheError, cache_key, document_sha256
from app.security.handles import field_type
from app.world.invoices import extract_document


# Includes the exact prompt and output schema so either kind of change misses
# old entries. Provider/model are deliberately not part of the key: a hit names
# its real original producer, even if today's configured chain has changed.
EXTRACTION_PROMPT_VERSION = "invoice-extraction-v1-" + hashlib.sha256((
    EXTRACTION_SYSTEM_PROMPT + "\n" + json.dumps(RawExtraction.model_json_schema(), sort_keys=True)
).encode()).hexdigest()
_PAYMENT_FIELDS = {
    "invoice_number", "vendor_name", "vendor_email", "due_date", "po_number",
    "amount_due_cents", "currency", "remit_account_number",
}
_LOGGER = logging.getLogger(__name__)


def verified_payload(document: DocumentText, candidate) -> FieldsExtractedPayload:
    """Treat live and cached output as claims; verify both identically."""
    try:
        payload = FieldsExtractedPayload.model_validate(candidate).model_copy(deep=True)
    except (ValidationError, TypeError, ValueError):
        raise ExtractorError("Extractor output failed schema validation (schema).",
                             stage="schema", cause="invalid_schema") from None
    if payload.invoice_id != document.invoice_id:
        raise ExtractorError("Extractor source identity is invalid (span_verification).",
                             stage="span_verification", cause="span_verification")
    names = set()
    for field in payload.fields:
        try:
            supported = field_type(field.name) == field.type
        except ValueError:
            supported = False
        if not supported or field.name in names:
            raise ExtractorError("Extractor field schema is invalid (schema).",
                                 stage="schema", cause="invalid_schema")
        names.add(field.name)
        if field.span is not None:
            if field.span.document_id != document.document_id:
                raise ExtractorError("Extractor source identity is invalid (span_verification).",
                                     stage="span_verification", cause="span_verification")
            field.span = verify_span(document, field.span)
        if field.value is not None and not span_source_verified(field.span, document):
            raise ExtractorError("Extractor quote is not grounded in the document (span_verification).",
                                 stage="span_verification", cause="span_verification")
    fields = {field.name: field for field in payload.fields}
    if not all(field_source_verified(fields.get(name), document) for name in _PAYMENT_FIELDS):
        raise ExtractorError("Extractor payment values are not grounded in source spans (span_verification).",
                             stage="span_verification", cause="span_verification")
    payload.instruction_like_spans = [verify_span(document, span) for span in payload.instruction_like_spans]
    if not all(span_source_verified(span, document) for span in payload.instruction_like_spans):
        raise ExtractorError("Extractor instruction quotes are not grounded (span_verification).",
                             stage="span_verification", cause="span_verification")
    # Handles are always assigned later by the server's batch-position table.
    payload.handles = []
    return payload


def fallback_eligible(error: ExtractorError) -> bool:
    return error.http_status in {429, 503, 408, 504} or error.cause in {
        "timeout", "invalid_schema", "output_limit", "span_verification",
    }


class ResilientExtractor:
    mode = "live"

    def __init__(self, primary, fallback, store, cache: ExtractionCache | None = None):
        self.primary, self.fallback, self.store, self.cache = primary, fallback, store, cache
        self.model = primary.model
        self.provider = "gemini"
        self._locks: dict[str, asyncio.Lock] = {}

    async def health_check(self):
        health = await self.primary.health_check()
        health.detail = (health.detail or "") + f" Explicit quarantined fallback: anthropic/{self.fallback.model}; cache {'on' if self.cache else 'off'}."
        return health

    def _source_bytes(self, document):
        record = self.store.get_record("documents", document.document_id)
        try:
            if record is None or record.get("invoice_id") != document.invoice_id:
                raise ValueError("Missing source")
            original = base64.b64decode(record["pdf_base64"], validate=True)
            rendered = extract_document(original, document.document_id, document.invoice_id)
            if rendered.pages != document.pages:
                raise ValueError("Source text differs")
        except Exception:
            raise ExtractorError("Original document bytes do not match extraction text (pdf_text).",
                                 stage="pdf_text", cause="source_mismatch") from None
        return original

    async def extract(self, document: DocumentText, *, force_haiku=False, bypass_cache=False):
        digest = document_sha256(self._source_bytes(document))
        key = cache_key(digest, EXTRACTION_PROMPT_VERSION)
        async with self._locks.setdefault(key, asyncio.Lock()):
            if self.cache is not None and not force_haiku and not bypass_cache:
                entry = self.cache.get(digest, EXTRACTION_PROMPT_VERSION)
                if entry is not None:
                    try:
                        payload = verified_payload(document, entry.to_payload(document))
                        payload.extraction_provenance = ExtractionProvenance.model_validate(entry.metadata())
                        return payload
                    except (ExtractorError, ValidationError, ValueError, TypeError):
                        # Invalid cache data cannot self-certify a source. A
                        # fresh quarantined extraction may repair the entry.
                        _LOGGER.warning("Extraction cache entry failed source verification; treating as a miss.")
            attempts = []
            if force_haiku:
                attempts.append(ExtractionAttempt(provider="gemini", model=self.primary.model,
                                                  outcome="skipped", error_code="forced_haiku"))
            candidates = [("anthropic", self.fallback)] if force_haiku else [
                ("gemini", self.primary), ("anthropic", self.fallback),
            ]
            last = None
            for provider, adapter in candidates:
                try:
                    payload = verified_payload(document, await adapter.extract(document))
                except (TimeoutError, httpx.TimeoutException):
                    error = ExtractorError("Extraction provider timed out (extraction).",
                                           stage="schema", cause="timeout", retryable=True)
                except ExtractorError as failure:
                    error = failure
                else:
                    produced = datetime.now(timezone.utc)
                    attempts.append(ExtractionAttempt(provider=provider, model=payload.extractor_model, outcome="success"))
                    payload.extraction_provenance = ExtractionProvenance(
                        provider=provider, model=payload.extractor_model, extracted_at=produced,
                        cached=False, document_sha256=digest, prompt_version=EXTRACTION_PROMPT_VERSION,
                        cache_key=key, attempts=attempts,
                    )
                    if self.cache is not None and not force_haiku and not bypass_cache:
                        try:
                            self.cache.put(digest, EXTRACTION_PROMPT_VERSION, payload,
                                provider=provider, model=payload.extractor_model, extracted_at=produced)
                        except ExtractionCacheError:
                            _LOGGER.warning("Extraction cache write failed; the verified result remains a fresh extraction.")
                    return payload
                last = error
                details = {"error_code": error.cause}
                if error.http_status is not None:
                    details["http_status"] = error.http_status
                attempts.append(ExtractionAttempt(provider=provider, model=adapter.model, outcome="failed", **details))
                if provider == "gemini" and not force_haiku and fallback_eligible(error):
                    _LOGGER.warning("Gemini extraction exhausted its retries; using the explicit quarantined Anthropic fallback.")
                    continue
                break
            final = ExtractorError("All eligible quarantined extraction providers failed; human review is required (extraction).",
                                   stage="schema", cause="providers_exhausted",
                                   http_status=last.http_status if last else None)
            final.extraction_attempts = attempts
            raise final from None

    async def aclose(self):
        if self.cache is not None:
            self.cache.close()
