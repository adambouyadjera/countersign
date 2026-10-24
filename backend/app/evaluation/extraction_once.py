"""A durable once-per-document quota boundary for fixed-suite extraction.

Receipts contain attempt state and producer metadata, never extraction claims.
Successful replay still comes from the verified extraction cache. Failed or
interrupted attempts are not silently retried by a later evaluation job.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.adapters.extraction import EXTRACTION_PROMPT_VERSION, verified_payload
from app.contract_models import ExtractionProvenance
from app.pipeline.extractor import ExtractorError
from app.security.extraction_cache import cache_key, document_sha256


_CLASSIFICATIONS = frozenset({
    "pdf_text", "gemini", "haiku", "schema", "span_verification", "cancelled",
    "configuration", "timeout", "request", "rate_limit", "output_limit", "invalid_schema",
    "providers_exhausted", "source_mismatch", "unexpected_error", "eval_receipt_invalid",
    "eval_cache_unavailable", "eval_cache_invalid", "extraction_in_progress",
})


def _classification(value, default):
    return value if isinstance(value, str) and value in _CLASSIFICATIONS else default


class OncePerDocumentExtractor:
    def __init__(self, delegate, receipt_store):
        self.delegate, self.receipt_store = delegate, receipt_store
        self.receipt_events = []

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def _observe(self, document, event, *, receipt_id=None, digest=None,
                 delegate_called=False, payload=None, failure=None):
        observation = {"document_id": document.document_id, "invoice_id": document.invoice_id,
                       "event": event, "delegate_called": delegate_called,
                       "observed_at": datetime.now(timezone.utc).isoformat()}
        if receipt_id is not None:
            observation.update(receipt_id=receipt_id, document_sha256=digest,
                               prompt_version=EXTRACTION_PROMPT_VERSION)
        if payload is not None and payload.extraction_provenance is not None:
            origin = payload.extraction_provenance
            observation.update(cached=origin.cached, provider=origin.provider,
                               model=origin.model, extracted_at=origin.extracted_at.isoformat())
        if failure is not None:
            observation["failure"] = failure
        self.receipt_events.append(observation)

    def _claim(self, digest):
        identifier = "eval-extraction-" + cache_key(digest, EXTRACTION_PROMPT_VERSION)
        with self.receipt_store.transaction():
            previous = self.receipt_store.get_record("eval_receipts", identifier)
            if previous is not None:
                if (previous.get("receipt_id") != identifier or previous.get("document_sha256") != digest
                        or previous.get("prompt_version") != EXTRACTION_PROMPT_VERSION
                        or previous.get("status") not in {"started", "accepted", "failed"}):
                    raise ExtractorError("The extraction receipt is invalid; evaluation will not make another model call.",
                                         stage="schema", cause="eval_receipt_invalid")
                return previous, False
            receipt = {"receipt_id": identifier, "document_sha256": digest,
                       "prompt_version": EXTRACTION_PROMPT_VERSION,
                       "status": "started", "owner": uuid4().hex,
                       "started_at": datetime.now(timezone.utc).isoformat()}
            self.receipt_store.save_record("eval_receipts", identifier, receipt)
            return receipt, True

    def _complete(self, receipt, **result):
        with self.receipt_store.transaction():
            current = self.receipt_store.get_record("eval_receipts", receipt["receipt_id"])
            if current is None or current.get("owner") != receipt["owner"] or current.get("status") != "started":
                raise ExtractorError("The extraction receipt changed; no further model attempt is permitted.",
                                     stage="schema", cause="eval_receipt_invalid")
            self.receipt_store.save_record("eval_receipts", receipt["receipt_id"], {
                **receipt, **result, "completed_at": datetime.now(timezone.utc).isoformat(),
            })

    @staticmethod
    def _failure(error):
        details = {"stage": _classification(getattr(error, "stage", None), "schema"),
                   "cause": _classification(getattr(error, "cause", None), "unexpected_error"),
                   "summary": "The quarantined extraction attempt failed; evaluation does not repeat it."}
        status = getattr(error, "http_status", None)
        if type(status) is int and 100 <= status <= 599:
            details["http_status"] = status
        attempts = getattr(error, "attempts", None)
        if type(attempts) is int and attempts > 0:
            details["attempts"] = attempts
        if isinstance(error, asyncio.CancelledError):
            details.update(stage="cancelled", cause="cancelled")
        return details

    @staticmethod
    def _failed_receipt_error(receipt):
        failure = receipt.get("failure") or {}
        cause = _classification(failure.get("cause"), "unexpected_error")
        stage = _classification(failure.get("stage"), "schema")
        status = failure.get("http_status")
        status = status if type(status) is int and 100 <= status <= 599 else None
        error = ExtractorError(
            "An earlier extraction attempt failed; its evaluation receipt prevents a fresh model call.",
            stage=stage, cause=cause, http_status=status,
        )
        error.cached_failure = True
        error.receipt_id = receipt["receipt_id"]
        return error

    def _replay(self, document, receipt):
        cache = getattr(self.delegate, "cache", None)
        try:
            entry = cache.get(receipt["document_sha256"], EXTRACTION_PROMPT_VERSION) if cache is not None else None
        except Exception:
            entry = None
        if entry is None:
            raise ExtractorError("The accepted extraction cache is missing or invalid; evaluation will not call the model again.",
                                 stage="schema", cause="eval_cache_unavailable")
        try:
            payload = verified_payload(document, entry.to_payload(document))
            origin = ExtractionProvenance.model_validate(entry.metadata())
            previous = receipt.get("origin", {})
            if (origin.provider != previous.get("provider") or origin.model != previous.get("model")
                    or origin.extracted_at.isoformat() != previous.get("extracted_at")):
                raise ValueError("Producer metadata changed")
            payload.extraction_provenance = origin
            return payload
        except Exception:
            raise ExtractorError("The accepted extraction cache failed deterministic verification; no fresh model call was made.",
                                 stage="span_verification", cause="eval_cache_invalid") from None

    async def extract(self, document):
        if getattr(self.delegate, "mode", None) == "offline":
            # Offline parsing has no external extraction quota or provenance
            # claim. It must never be mistaken for a live cached extraction.
            result = await self.delegate.extract(document)
            self._observe(document, "offline_passthrough", delegate_called=True, payload=result)
            return result
        if getattr(self.delegate, "mode", None) != "live":
            raise ExtractorError("Evaluation extraction requires an explicit live or offline delegate.",
                                 stage="schema", cause="configuration")
        if getattr(self.delegate, "cache", None) is None:
            raise ExtractorError("Live evaluation requires the verified extraction cache to preserve the once-per-document quota.",
                                 stage="schema", cause="configuration")
        digest = document_sha256(self.delegate._source_bytes(document))
        receipt, first = self._claim(digest)
        identifier = receipt["receipt_id"]
        if not first:
            if receipt["status"] == "started":
                self._observe(document, "in_progress", receipt_id=identifier, digest=digest)
                raise ExtractorError("An earlier extraction attempt is still unresolved; evaluation will not repeat it.",
                                     stage="schema", cause="extraction_in_progress")
            if receipt["status"] == "failed":
                self._observe(document, "reused_failure", receipt_id=identifier, digest=digest,
                              failure=receipt.get("failure"))
                raise self._failed_receipt_error(receipt) from None
            try:
                result = self._replay(document, receipt)
            except ExtractorError as error:
                self._observe(document, "cache_unavailable", receipt_id=identifier, digest=digest,
                              failure=self._failure(error))
                raise
            self._observe(document, "cache_hit", receipt_id=identifier, digest=digest, payload=result)
            return result
        try:
            result = verified_payload(document, await self.delegate.extract(document))
            origin = result.extraction_provenance
            if (origin is None or origin.document_sha256 != digest
                    or origin.prompt_version != EXTRACTION_PROMPT_VERSION):
                raise ExtractorError("Live extraction has no matching producer/cache provenance.",
                                     stage="schema", cause="invalid_schema")
        except BaseException as error:
            failure = self._failure(error)
            self._complete(receipt, status="failed", failure=failure)
            self._observe(document, "first_attempt", receipt_id=identifier, digest=digest,
                          delegate_called=True, failure=failure)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ExtractorError(failure["summary"], stage=failure["stage"], cause=failure["cause"],
                                 http_status=failure.get("http_status")) from None
        self._complete(receipt, status="accepted", origin={
            "provider": origin.provider, "model": origin.model, "extracted_at": origin.extracted_at.isoformat(),
        })
        self._observe(document, "first_attempt", receipt_id=identifier, digest=digest,
                      delegate_called=True, payload=result)
        return result
