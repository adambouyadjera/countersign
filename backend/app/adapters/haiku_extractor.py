"""Quarantined Anthropic extraction with the same schema and span verifier.

Provider output is only a source-location suggestion. It cannot certify a span,
change trust labels, call tools, or authorize a payment.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from anthropic import APIConnectionError, APITimeoutError, AsyncAnthropic, transform_schema
import httpx
import httpx2
from pydantic import SecretStr

from app.contract_models import DocumentText, FieldsExtractedPayload, ServiceHealth
from app.pipeline.extraction_models import RawExtraction
from app.pipeline.extractor import EXTRACTION_SYSTEM_PROMPT, ExtractorError, numbered_document
from app.pipeline.spans import to_payload


HAIKU_TIMEOUT_SECONDS = 45
HAIKU_MAX_ATTEMPTS = 3
_TRANSIENT = {408, 429, 500, 502, 503, 504}


class _HttpxTransportBridge(httpx2.AsyncBaseTransport):
    """Adapt the app's injectable httpx transport to this SDK's httpx2 client."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self.transport = transport

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        forwarded = httpx.Request(request.method, str(request.url), headers=request.headers.raw,
                                  content=await request.aread(), extensions=request.extensions)
        response = await self.transport.handle_async_request(forwarded)
        try:
            content = await response.aread()
            return httpx2.Response(response.status_code, headers=response.headers.raw,
                                   content=content, request=request)
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.transport.aclose()


def _status(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if type(value) is not int:
        value = getattr(getattr(error, "response", None), "status_code", None)
    return value if type(value) is int and 100 <= value <= 599 else None


def _delay(error: Exception, attempt: int) -> float:
    delay = float(2 ** (attempt - 1))
    try:
        hint = float(getattr(getattr(error, "response", None), "headers", {}).get("retry-after", 0))
        if hint >= 0:
            delay = max(delay, hint)
    except (AttributeError, TypeError, ValueError):
        pass
    return min(delay, 30)


class LiveHaikuExtractor:
    mode: Literal["live"] = "live"
    provider = "anthropic"

    def __init__(
        self, *, key: SecretStr, model: str, client: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key, self.model = key, model
        self._client, self._transport = client, transport
        self._sleep = asyncio.sleep
        self.http_observations: list[dict] = []

    def _observe_status(self, status: int) -> None:
        # Only safe status metadata is retained. Never retain URLs or headers.
        self.http_observations.append({
            "provider": self.provider, "model": self.model,
            "http_status": status,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })

    def _new_client(self) -> AsyncAnthropic:
        options = {}
        if self._transport is not None:
            options["http_client"] = httpx2.AsyncClient(transport=_HttpxTransportBridge(self._transport))
        return AsyncAnthropic(
            api_key=self._key.get_secret_value(), timeout=HAIKU_TIMEOUT_SECONDS,
            max_retries=0, **options,
        )

    async def health_check(self) -> ServiceHealth:
        if not self._key.get_secret_value():
            return ServiceHealth(status="missing_key", mode=self.mode,
                                 detail="ANTHROPIC_API_KEY is not configured (extractor fallback).")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
                response = await client.get("https://api.anthropic.com/v1/models", headers={
                    "x-api-key": self._key.get_secret_value(), "anthropic-version": "2023-06-01",
                })
            valid = response.is_success and isinstance(response.json().get("data"), list)
        except Exception:
            valid = False
        return ServiceHealth(status="ok" if valid else "error", mode=self.mode,
                             detail=f"Anthropic extractor fallback ({self.model}): read-only catalog probe "
                                    + ("succeeded." if valid else "failed."))

    async def _generate(self, client, document: DocumentText) -> tuple[RawExtraction, str]:
        arguments = dict(
            model=self.model,
            max_tokens=8192,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": numbered_document(document)}],
            output_config={"format": {
                # Anthropic does not accept numeric constraints such as
                # minimum on the wire. Its official transform preserves the
                # schema shape and places unsupported constraints in hints.
                # RawExtraction below still enforces every original constraint.
                "type": "json_schema", "schema": transform_schema(RawExtraction),
            }},
            tools=[],
        )
        if self._client is None:
            received = await client.messages.with_raw_response.create(**arguments)
            self._observe_status(received.status_code)
            response = await received.parse()
        else:
            response = await client.messages.create(**arguments)
        if getattr(response, "stop_reason", None) != "end_turn":
            raise ExtractorError(
                "Anthropic extraction was incomplete or refused (schema).",
                stage="haiku", cause="output_limit" if getattr(response, "stop_reason", None) == "max_tokens" else "invalid_schema",
            )
        blocks = list(getattr(response, "content", None) or [])
        if any(getattr(block, "type", None) not in {"text", "thinking", "redacted_thinking"} for block in blocks):
            raise ExtractorError("Anthropic extraction returned an invalid output block (schema).",
                                 stage="haiku", cause="invalid_schema")
        # Thinking is never parsed or substituted for the final JSON answer.
        text = "".join(block.text for block in blocks if block.type == "text")
        try:
            raw = RawExtraction.model_validate_json(text)
        except Exception:
            raise ExtractorError("Anthropic response did not match the invoice extraction schema (schema).",
                                 stage="haiku", cause="invalid_schema") from None
        reported_model = getattr(response, "model", None)
        return raw, reported_model if isinstance(reported_model, str) and reported_model else self.model

    async def extract(self, document: DocumentText) -> FieldsExtractedPayload:
        if not self._key.get_secret_value():
            raise ExtractorError("ANTHROPIC_API_KEY is not configured (extractor fallback).",
                                 stage="haiku", cause="configuration")
        if not self.model:
            raise ExtractorError("EXTRACTOR_FALLBACK_MODEL is not configured.", stage="haiku", cause="configuration")
        for attempt in range(1, HAIKU_MAX_ATTEMPTS + 1):
            try:
                async with asyncio.timeout(HAIKU_TIMEOUT_SECONDS):
                    if self._client is not None:
                        raw, produced_model = await self._generate(self._client, document)
                    else:
                        async with self._new_client() as client:
                            raw, produced_model = await self._generate(client, document)
                # The verifier retains actual document slices; false spans stay
                # false so the same deterministic policy can fail closed.
                return to_payload(document, raw, produced_model)
            except ExtractorError as error:
                if error.cause in {"invalid_schema", "output_limit"} and attempt < HAIKU_MAX_ATTEMPTS:
                    continue
                error.attempts = attempt
                raise
            except Exception as error:
                status = _status(error)
                if status is not None:
                    self._observe_status(status)
                timeout = isinstance(error, (TimeoutError, httpx.TimeoutException, httpx2.TimeoutException, APITimeoutError))
                retryable = timeout or status in _TRANSIENT or isinstance(error, (httpx.NetworkError, httpx2.NetworkError, APIConnectionError))
                if retryable and attempt < HAIKU_MAX_ATTEMPTS:
                    await self._sleep(_delay(error, attempt))
                    continue
                detail = f"HTTP {status}" if status is not None else "timeout" if timeout else type(error).__name__
                raise ExtractorError(
                    f"Anthropic extraction request failed ({detail}); no unverified result was accepted.",
                    stage="haiku", cause="timeout" if timeout else "request",
                    http_status=status, retryable=retryable, attempts=attempt,
                ) from None
