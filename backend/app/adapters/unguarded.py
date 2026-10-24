"""Explicit OpenAI-compatible transports for the unguarded AP agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import re
import time
from typing import Any, Literal

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.contract_models import ServiceHealth
from app.config import provider_name


@dataclass(frozen=True)
class AgentToolCall:
    id: str
    name: str
    arguments: str | dict
    # Gemini's opaque continuation signature must be returned with the exact
    # assistant tool call. This is transport metadata, not hidden reasoning.
    extra_content: dict | None = None


@dataclass(frozen=True)
class AgentTurn:
    content: str | None = None
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class AgentError(RuntimeError):
    """Safe provider failure without response bodies, keys or URLs."""

    def __init__(self, message, *, http_status=None, code=None,
                 retry_after_seconds=None, quota_limit=None, attempts=1):
        self.http_status, self.code = http_status, code
        self.retry_after_seconds = retry_after_seconds
        self.quota_limit = quota_limit
        self.attempts = attempts
        super().__init__(message)


def _provider_error_body(error: Exception):
    """Normalize Google's RPC wrapper locally without retaining its contents."""
    response = getattr(error, "response", None)
    body = getattr(error, "body", None)
    if not isinstance(body, (dict, list)) and response is not None:
        try:
            body = response.json()
        except (ValueError, TypeError, AttributeError):
            body = None
    if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict):
        body = body[0]
    return body


def google_daily_quota_exhausted(error: Exception) -> bool:
    """Recognize an explicit daily QuotaFailure, never prose or RetryInfo alone."""
    body = _provider_error_body(error)
    if not isinstance(body, dict):
        return False
    nested = body.get("error", body)
    details = nested.get("details", []) if isinstance(nested, dict) else []
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict) or detail.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
            continue
        violations = detail.get("violations", [])
        for item in violations if isinstance(violations, list) else []:
            if not isinstance(item, dict):
                continue
            quota_id = item.get("quotaId", item.get("quota_id"))
            if isinstance(quota_id, str) and re.search(r"PerDay(?=Per|[-_]|$)|(?:^|_)per_day(?:_|$)", quota_id):
                return True
    return False


def quota_metadata(error: Exception) -> dict:
    """Extract only bounded delay and numeric limits; never retain error text."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    delay, limit = None, None

    def seconds(value):
        try:
            number = float(str(value).removesuffix("s"))
            return min(60.0, max(0.0, number)) if math.isfinite(number) else None
        except (ValueError, TypeError):
            return None

    raw_after = headers.get("retry-after") if hasattr(headers, "get") else None
    if raw_after is not None:
        delay = seconds(raw_after)
        if delay is None:
            try:
                retry_at = parsedate_to_datetime(raw_after)
                if retry_at.tzinfo is not None:
                    delay = seconds((retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    body = _provider_error_body(error)
    if isinstance(body, dict):
        nested = body.get("error", body)
        details = nested.get("details", []) if isinstance(nested, dict) else []
        for detail in details if isinstance(details, list) else []:
            if not isinstance(detail, dict):
                continue
            if detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                provider_delay = seconds(detail.get("retryDelay"))
                if provider_delay is not None:
                    delay = max(delay or 0.0, provider_delay)
            elif detail.get("@type") == "type.googleapis.com/google.rpc.QuotaFailure":
                violations = detail.get("violations", [])
                for item in violations if isinstance(violations, list) else []:
                    if not isinstance(item, dict):
                        continue
                    value = item.get("quotaValue", item.get("quota_value"))
                    if isinstance(value, (str, int)) and re.fullmatch(r"[0-9]{1,18}", str(value)):
                        candidate = int(value)
                        limit = candidate if limit is None else min(limit, candidate)
    return {"retry_after_seconds": delay, "quota_limit": limit}


_PROBE_TOOL = {"type": "function", "function": {
    "name": "capability_probe", "description": "Harmless API capability check with no side effects.",
    "parameters": {"type": "object", "properties": {"marker": {"type": "string"}},
                   "required": ["marker"], "additionalProperties": False},
}}
_PROBE_MESSAGES = [
    {"role": "system", "content": "Test API function calling. Call capability_probe with marker ready. Do not answer in prose."},
    {"role": "user", "content": "Call capability_probe now."},
]


class LiveAgentLLM:
    mode: Literal["live"] = "live"

    def __init__(self, *, key: SecretStr, base_url: str, model: str, provider: str | None = None, client: Any = None,
                 transport: httpx.AsyncBaseTransport | None = None, request_interval_seconds: float = 0):
        if (isinstance(request_interval_seconds, bool) or not isinstance(request_interval_seconds, (int, float))
                or not math.isfinite(request_interval_seconds) or request_interval_seconds < 0):
            raise ValueError("Request interval must be a finite nonnegative number of seconds")
        self._key, self.base_url, self.model = key, base_url, model
        self.provider = provider or provider_name(base_url)
        # Bitdeer's reasoning models share this budget between private reasoning
        # and final content/tool calls. Never substitute reasoning_content for
        # an answer when the provider exhausts it.
        self.max_tokens = 8192 if self.provider == "bitdeer" else 2048
        self._client, self._transport = client, transport
        self._capabilities = None
        self.capability_report = None
        self._capability_lock = asyncio.Lock()
        self.request_interval_seconds = float(request_interval_seconds)
        self._request_lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._clock = time.monotonic
        self._sleep = asyncio.sleep
        self.request_stats = {"attempts": 0, "retries": 0, "transient_http_statuses": {},
                              "quota_limits": [], "last_retry_after_seconds": None}

    async def _request_turn(self):
        while True:
            async with self._request_lock:
                remaining = self._next_request_at - self._clock()
                if remaining <= 0:
                    self._next_request_at = self._clock() + self.request_interval_seconds
                    return
            # Do not hold the gate while waiting: another in-flight request may
            # impose a longer provider cooldown. Recheck it before starting.
            await self._sleep(min(remaining, 60))

    async def _completion(self, client, **kwargs):
        """Retry only rejected transient requests, preserving the exact request."""
        for attempt in range(1, 4):
            await self._request_turn()
            self.request_stats["attempts"] += 1
            if attempt > 1:
                self.request_stats["retries"] += 1
            try:
                return await client.chat.completions.create(**kwargs)
            except Exception as error:
                status = getattr(error, "status_code", None)
                if status in {400, 422, 501}:
                    # Capability negotiation handles explicit unsupported tool
                    # requests; a generation call still reports these safely.
                    raise
                metadata = quota_metadata(error)
                if metadata["quota_limit"] is not None:
                    limits = self.request_stats["quota_limits"]
                    if metadata["quota_limit"] not in limits:
                        limits.append(metadata["quota_limit"])
                        limits.sort()
                self.request_stats["last_retry_after_seconds"] = metadata["retry_after_seconds"]
                if status in {429, 503}:
                    statuses = self.request_stats["transient_http_statuses"]
                    statuses[str(status)] = statuses.get(str(status), 0) + 1
                    if status == 429 and self.provider == "gemini" and google_daily_quota_exhausted(error):
                        raise AgentError("unguarded agent provider daily quota exhausted (429)",
                                         http_status=429, code="provider_daily_quota_exhausted",
                                         attempts=attempt, **metadata) from None
                    if attempt < 3:
                        delay = metadata["retry_after_seconds"]
                        delay = float(2 ** (attempt - 1)) if delay is None else delay
                        async with self._request_lock:
                            self._next_request_at = max(self._next_request_at, self._clock() + delay)
                        continue
                suffix = f" (HTTP {status})" if status is not None else ""
                message = "unguarded agent provider out of credits (402)" if status == 402 else (
                    "Configured provider request failed" + suffix + "; no provider fallback was used")
                raise AgentError(message, http_status=status,
                                 code="provider_out_of_credits" if status == 402 else "inference_request_failed",
                                 attempts=attempt, **metadata) from None

    def _check_config(self):
        if not self._key.get_secret_value():
            raise AgentError("NAIVE_API_KEY or selected provider credential is not configured (unguarded)")
        if not self.base_url or not self.model:
            raise AgentError("NAIVE_BASE_URL or NAIVE_MODEL is not configured (unguarded)")

    def _new_client(self):
        options = {}
        if self._transport is not None:
            options["http_client"] = httpx.AsyncClient(transport=self._transport)
        return AsyncOpenAI(api_key=self._key.get_secret_value(), base_url=self.base_url,
                           timeout=90, max_retries=0, **options)

    async def health_check(self) -> ServiceHealth:
        if not self._key.get_secret_value():
            return ServiceHealth(status="missing_key", mode=self.mode,
                detail=f"{self.provider} ({self.model}): NAIVE_API_KEY or selected provider credential is not configured.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
                response = await client.get(self.base_url.rstrip("/") + "/models", headers={
                    "Authorization": "Bearer " + self._key.get_secret_value(),
                })
            if response.is_success and isinstance(response.json().get("data"), list):
                return ServiceHealth(status="ok", mode=self.mode,
                    detail=f"{self.provider} ({self.model}): read-only model catalog probe succeeded.")
        except Exception:
            pass
        return ServiceHealth(status="error", mode=self.mode,
            detail=f"{self.provider} ({self.model}): read-only model catalog probe failed.")

    async def ensure_capabilities(self) -> dict:
        """List models before probing native calls; never silently change a model."""
        async with self._capability_lock:
            if self._capabilities is not None:
                return dict(self._capabilities)
            self._check_config()
            async def inspect(client):
                try:
                    catalog = await client.models.list()
                    available = [model.id for model in catalog.data]
                except Exception as error:
                    status = getattr(error, "status_code", None)
                    if status == 402:
                        raise AgentError("unguarded agent provider out of credits (402)",
                                         http_status=402, code="provider_out_of_credits") from None
                    raise AgentError("Configured provider model catalog request failed (unguarded)",
                                     http_status=status) from None
                same_model = lambda name: (name.removeprefix("models/") if self.provider == "gemini" else name)
                if same_model(self.model) not in {same_model(name) for name in available}:
                    raise AgentError("NAIVE_MODEL is absent from the configured provider model catalog")
                probes = []
                self.capability_report = {"provider": self.provider, "model": self.model, "chosen_model": self.model,
                    "available_models": available, "probes": probes,
                    "tool_calling_supported": None, "protocol": None}
                candidates = [self.model] + [name for name in available if same_model(name) != same_model(self.model)
                    and not any(term in name.lower() for term in ("bge-", "embed", "rerank", "seedream", "image"))]
                for name in candidates:
                    result = {"model": name, "tool_calling": False}
                    for choice in ({"type": "function", "function": {"name": "capability_probe"}}, "auto"):
                        try:
                            response = await self._completion(client,
                                model=name, messages=_PROBE_MESSAGES, tools=[_PROBE_TOOL],
                                tool_choice=choice, max_tokens=self.max_tokens,
                            )
                            calls = response.choices[0].message.tool_calls or []
                            result["tool_calling"] = any(call.function.name == "capability_probe" for call in calls)
                            result["finish_reason"] = response.choices[0].finish_reason
                            if result["finish_reason"] in {"length", "content_filter"}:
                                raise AgentError("Provider capability probe was incomplete; no action-protocol fallback was selected")
                            result["tool_calls"] = [{"name": call.function.name, "arguments": call.function.arguments} for call in calls]
                            if result["tool_calling"]:
                                break
                        except Exception as error:
                            if isinstance(error, AgentError):
                                probes.append({"model": name, "tool_calling": None,
                                    "http_status": error.http_status, "error_code": error.code,
                                    "attempts": error.attempts, "retry_after_seconds": error.retry_after_seconds,
                                    "quota_limit": error.quota_limit})
                                self.capability_report["error"] = "inference_request_failed"
                                raise
                            status = getattr(error, "status_code", None)
                            if status not in {400, 422, 501}:
                                probes.append({"model": name, "tool_calling": None,
                                               "error_type": type(error).__name__, "http_status": status})
                                self.capability_report["error"] = "inference_request_failed"
                                if status == 402:
                                    raise AgentError("unguarded agent provider out of credits (402)",
                                                     http_status=402, code="provider_out_of_credits") from None
                                suffix = f" (HTTP {status})" if status is not None else ""
                                raise AgentError("Configured provider capability probe failed" + suffix +
                                    "; no action-protocol fallback was selected", http_status=status,
                                    code="inference_request_failed") from None
                            result["error_type"] = type(error).__name__
                            result["http_status"] = status
                    probes.append(result)
                    if result["tool_calling"]:
                        if name != self.model:
                            raise AgentError("NAIVE_MODEL lacks confirmed native tools; another catalog model supports tools. Configure NAIVE_MODEL explicitly.")
                        return {"provider": self.provider, "model": self.model, "chosen_model": self.model, "available_models": available,
                                "probes": probes, "tool_calling_supported": True, "protocol": "tools"}
                return {"provider": self.provider, "model": self.model, "chosen_model": self.model, "available_models": available,
                        "probes": probes, "tool_calling_supported": False, "protocol": "json_actions"}
            if self._client is not None:
                self._capabilities = await inspect(self._client)
            else:
                async with self._new_client() as client:
                    self._capabilities = await inspect(client)
            self.capability_report = dict(self._capabilities)
            return dict(self._capabilities)

    async def complete(self, *, messages: list[dict], tools: list[dict], protocol: str = "tools") -> AgentTurn:
        self._check_config()
        args = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        if protocol == "tools":
            args.update(tools=tools, tool_choice="auto")
        elif protocol != "json_actions":
            raise AgentError("Unsupported unguarded action protocol")
        async def invoke(client):
            response = await self._completion(client, **args)
            message = response.choices[0].message
            return AgentTurn(content=message.content, tool_calls=[
                AgentToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments,
                              extra_content=getattr(call, "extra_content", None))
                for call in (message.tool_calls or [])
            ], finish_reason=response.choices[0].finish_reason)
        try:
            if self._client is not None:
                return await invoke(self._client)
            async with self._new_client() as client:
                return await invoke(client)
        except Exception as error:
            if isinstance(error, AgentError):
                raise
            status = getattr(error, "status_code", None)
            if status == 402:
                raise AgentError("unguarded agent provider out of credits (402)",
                                 http_status=402, code="provider_out_of_credits") from None
            suffix = f" (HTTP {status})" if status is not None else ""
            raise AgentError("Configured provider generation failed" + suffix +
                             " (unguarded); no offline fallback was used", http_status=status) from None


class OfflineAgentLLM:
    """Explicit deterministic AP tool script, not evidence of model behavior."""
    mode: Literal["offline"] = "offline"
    provider = "offline"
    model = "offline-unguarded-agent-v1"

    async def health_check(self):
        return ServiceHealth(status="offline", mode=self.mode, detail="AGENT_MODE=offline; deterministic AP tool script.")

    async def ensure_capabilities(self):
        return {"provider": self.provider, "model": self.model, "available_models": [self.model], "protocol": "tools", "tool_calling_supported": True, "mode": "offline"}

    async def complete(self, *, messages, tools, protocol="tools"):
        from app.contract_models import DocumentText
        from app.pipeline.offline_extractor import parse_offline
        source = next(message["content"] for message in messages if message["role"] == "user")
        payload = json.loads(source)
        document = DocumentText.model_validate(payload["document"])
        extraction = parse_offline(document)
        completed = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
        if not completed:
            return AgentTurn(tool_calls=[AgentToolCall("vendors", "list_vendors", "{}")])
        if len(completed) == 1:
            return AgentTurn(tool_calls=[AgentToolCall("lookup", "lookup_account", json.dumps({"account_number": extraction.remit_account_number.value}))])
        if len(completed) == 2:
            account = completed[-1]
            account_id = account.get("account_id")
            if account_id is None and isinstance(account.get("account"), dict):
                account_id = account["account"].get("account_id")
            if not account_id:
                return AgentTurn(content="No account was resolved; invoice was not paid.")
            return AgentTurn(tool_calls=[AgentToolCall("pay", "pay", json.dumps({"account_id": account_id,
                "amount_cents": extraction.amount_due_cents.value, "invoice_number": extraction.invoice_number.value,
                "memo": "Invoice " + str(extraction.invoice_number.value)}))])
        return AgentTurn(content="Invoice processing is complete; see the recorded payment tool result.")
