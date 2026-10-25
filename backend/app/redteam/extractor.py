"""Haiku-only quarantine for red-team work; never spend Gemini quota.

The job owns an isolated ExtractionCache and shared lock dictionary. Delegates
belong to individual cases and still verify original PDF bytes and every span.
This wrapper never reads a normal application's Gemini extraction cache.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json

from pydantic import ValidationError

from app.adapters.extraction import EXTRACTION_PROMPT_VERSION, verified_payload
from app.contract_models import ExtractionProvenance
from app.pipeline.extractor import ExtractorError
from app.security.extraction_cache import ExtractionCacheError, cache_key, document_sha256


def _now():
    return datetime.now(timezone.utc).isoformat()


class HaikuOnlyExtractor:
    """ExtractorLLM-compatible wrapper with honest per-case cache receipts.

    Live delegates must be ResilientExtractor instances configured without
    their ordinary cache. All actual calls explicitly force the quarantined
    Anthropic fallback. Explicit offline delegates pass through as offline.
    """

    def __init__(self, *, delegate, cache, locks: dict[str, asyncio.Lock] | None = None):
        self.delegate, self.cache = delegate, cache
        self.mode = delegate.mode
        self.provider = "offline" if self.mode == "offline" else "anthropic"
        self.model = delegate.model if self.mode == "offline" else delegate.fallback.model
        if self.mode not in {"live", "offline"} or not isinstance(self.model, str) or not self.model:
            raise ValueError("Red-team extractor requires an explicit provider mode and model")
        self.locks = locks if locks is not None else {}
        self.receipts: list[dict] = []

    async def health_check(self):
        if self.mode == "offline":
            return await self.delegate.health_check()
        # ResilientExtractor.health_check probes its Gemini primary. Red-team
        # work must probe only the provider it can actually call.
        return await self.delegate.fallback.health_check()

    def _cached(self, document, digest):
        if self.cache is None:
            return None
        try:
            entry = self.cache.get(digest, EXTRACTION_PROMPT_VERSION)
            if entry is None or entry.provider != "anthropic" or entry.model != self.model:
                return None
            payload = verified_payload(document, entry.to_payload(document))
            payload.extraction_provenance = ExtractionProvenance.model_validate(entry.metadata())
            return payload
        except (ExtractorError, ExtractionCacheError, ValidationError, ValueError, TypeError, OSError):
            # Cached data is a claim, never span authority. A miss can be
            # repaired only by a fresh, forced Haiku extraction.
            return None

    def _check_origin(self, payload, digest):
        origin = payload.extraction_provenance
        if (origin is None or origin.provider != "anthropic" or origin.model != self.model
                or payload.extractor_model != self.model or origin.cached is not False
                or origin.document_sha256 != digest or origin.prompt_version != EXTRACTION_PROMPT_VERSION
                or origin.cache_key != cache_key(digest, EXTRACTION_PROMPT_VERSION)):
            raise ExtractorError("Red-team extraction did not report the configured fresh Haiku producer.",
                                 stage="schema", cause="invalid_schema")

    async def extract(self, document):
        receipt = {"document_id": document.document_id, "invoice_id": document.invoice_id,
                   "provider": self.provider, "model": self.model, "mode": self.mode,
                   "requested_at": _now(), "status": "pending", "cached": False,
                   "provider_call": False, "cache_written": False}
        self.receipts.append(receipt)
        try:
            if self.mode == "offline":
                receipt["source"] = "offline"
                payload = await self.delegate.extract(document)
                receipt["status"] = "success"
                return payload

            digest = document_sha256(self.delegate._source_bytes(document))
            receipt.update(document_sha256=digest, cache_key=cache_key(digest, EXTRACTION_PROMPT_VERSION))
            identity = json.dumps([self.model, digest, EXTRACTION_PROMPT_VERSION], separators=(",", ":"))
            lock_key = hashlib.sha256(identity.encode()).hexdigest()
            async with self.locks.setdefault(lock_key, asyncio.Lock()):
                payload = self._cached(document, digest)
                if payload is not None:
                    receipt.update(source="cache", cached=True)
                else:
                    receipt.update(source="provider", provider_call=True)
                    payload = verified_payload(document, await self.delegate.extract(
                        document, force_haiku=True, bypass_cache=True,
                    ))
                    self._check_origin(payload, digest)
                    origin = payload.extraction_provenance
                    if self.cache is not None:
                        try:
                            self.cache.put(digest, EXTRACTION_PROMPT_VERSION, payload, provider=origin.provider,
                                           model=origin.model, extracted_at=origin.extracted_at)
                        except (ExtractionCacheError, OSError):
                            # A valid fresh result remains valid if optional
                            # cache persistence fails; receipts show the miss.
                            pass
                        else:
                            receipt["cache_written"] = True
                receipt.update(status="success", extracted_at=payload.extraction_provenance.extracted_at.isoformat())
                return payload
        except asyncio.CancelledError:
            receipt["status"] = "cancelled"
            raise
        except Exception:
            receipt.update(status="error", error_code="extraction_failed")
            # Never copy provider bodies, exception text or source text here.
            raise
        finally:
            receipt["completed_at"] = _now()

    async def aclose(self):
        # The job cache must remain open for other concurrent cases. Even a
        # misconfigured delegate sharing that object must not close it here.
        if getattr(self.delegate, "cache", None) is self.cache:
            return
        close = getattr(self.delegate, "aclose", None)
        if close is not None:
            await close()
