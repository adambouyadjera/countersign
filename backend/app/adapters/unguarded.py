"""Explicit Bitdeer transports for the intentionally unguarded AP agent."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any, Literal

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.contract_models import ServiceHealth


@dataclass(frozen=True)
class AgentToolCall:
    id: str
    name: str
    arguments: str | dict


@dataclass(frozen=True)
class AgentTurn:
    content: str | None = None
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class AgentError(RuntimeError):
    """Safe provider failure without response bodies, keys or URLs."""

    def __init__(self, message, *, http_status=None, code=None):
        self.http_status, self.code = http_status, code
        super().__init__(message)


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

    def __init__(self, *, key: SecretStr, base_url: str, model: str, client: Any = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._key, self.base_url, self.model = key, base_url, model
        self._client, self._transport = client, transport
        self._capabilities = None
        self.capability_report = None
        self._capability_lock = asyncio.Lock()

    def _check_config(self):
        if not self._key.get_secret_value():
            raise AgentError("BITDEER_API_KEY is not configured (unguarded)")
        if not self.base_url or not self.model:
            raise AgentError("Bitdeer URL or NAIVE_MODEL is not configured (unguarded)")

    def _new_client(self):
        options = {}
        if self._transport is not None:
            options["http_client"] = httpx.AsyncClient(transport=self._transport)
        return AsyncOpenAI(api_key=self._key.get_secret_value(), base_url=self.base_url,
                           timeout=90, max_retries=0, **options)

    async def health_check(self) -> ServiceHealth:
        if not self._key.get_secret_value():
            return ServiceHealth(status="missing_key", mode=self.mode, detail="BITDEER_API_KEY is not configured.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5) as client:
                response = await client.get(self.base_url.rstrip("/") + "/models", headers={
                    "Authorization": "Bearer " + self._key.get_secret_value(),
                })
            if response.is_success and isinstance(response.json().get("data"), list):
                return ServiceHealth(status="ok", mode=self.mode, detail="Read-only Bitdeer model catalog probe succeeded.")
        except Exception:
            pass
        return ServiceHealth(status="error", mode=self.mode, detail="Read-only Bitdeer model catalog probe failed.")

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
                except Exception:
                    raise AgentError("Bitdeer model catalog request failed (unguarded)") from None
                if self.model not in available:
                    raise AgentError("NAIVE_MODEL is absent from the Bitdeer model catalog")
                probes = []
                self.capability_report = {"model": self.model, "chosen_model": self.model,
                    "available_models": available, "probes": probes,
                    "tool_calling_supported": None, "protocol": None}
                candidates = [self.model] + [name for name in available if name != self.model
                    and not any(term in name.lower() for term in ("bge-", "embed", "rerank", "seedream", "image"))]
                for name in candidates:
                    result = {"model": name, "tool_calling": False}
                    for choice in ({"type": "function", "function": {"name": "capability_probe"}}, "auto"):
                        try:
                            response = await client.chat.completions.create(
                                model=name, messages=_PROBE_MESSAGES, tools=[_PROBE_TOOL],
                                tool_choice=choice, max_tokens=2048,
                            )
                            calls = response.choices[0].message.tool_calls or []
                            result["tool_calling"] = any(call.function.name == "capability_probe" for call in calls)
                            result["finish_reason"] = response.choices[0].finish_reason
                            if result["finish_reason"] in {"length", "content_filter"}:
                                raise AgentError("Bitdeer capability probe was incomplete; no action-protocol fallback was selected")
                            result["tool_calls"] = [{"name": call.function.name, "arguments": call.function.arguments} for call in calls]
                            if result["tool_calling"]:
                                break
                        except Exception as error:
                            if isinstance(error, AgentError):
                                raise
                            status = getattr(error, "status_code", None)
                            if status not in {400, 422, 501}:
                                probes.append({"model": name, "tool_calling": None,
                                               "error_type": type(error).__name__, "http_status": status})
                                self.capability_report["error"] = "inference_request_failed"
                                suffix = f" (HTTP {status})" if status is not None else ""
                                raise AgentError("Bitdeer capability probe failed" + suffix +
                                    "; no action-protocol fallback was selected", http_status=status,
                                    code="inference_request_failed") from None
                            result["error_type"] = type(error).__name__
                            result["http_status"] = status
                    probes.append(result)
                    if result["tool_calling"]:
                        if name != self.model:
                            raise AgentError("NAIVE_MODEL lacks confirmed native tools; another catalog model supports tools. Configure NAIVE_MODEL explicitly.")
                        return {"model": self.model, "chosen_model": self.model, "available_models": available,
                                "probes": probes, "tool_calling_supported": True, "protocol": "tools"}
                return {"model": self.model, "chosen_model": self.model, "available_models": available,
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
        args = {"model": self.model, "messages": messages, "max_tokens": 2048}
        if protocol == "tools":
            args.update(tools=tools, tool_choice="auto")
        elif protocol != "json_actions":
            raise AgentError("Unsupported unguarded action protocol")
        async def invoke(client):
            response = await client.chat.completions.create(**args)
            message = response.choices[0].message
            return AgentTurn(content=message.content, tool_calls=[
                AgentToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
                for call in (message.tool_calls or [])
            ], finish_reason=response.choices[0].finish_reason)
        try:
            if self._client is not None:
                return await invoke(self._client)
            async with self._new_client() as client:
                return await invoke(client)
        except Exception as error:
            status = getattr(error, "status_code", None)
            suffix = f" (HTTP {status})" if status is not None else ""
            raise AgentError("Bitdeer generation failed" + suffix +
                             " (unguarded); no offline fallback was used", http_status=status) from None


class OfflineAgentLLM:
    """Explicit deterministic AP tool script, not evidence of model behavior."""
    mode: Literal["offline"] = "offline"
    model = "offline-unguarded-agent-v1"

    async def health_check(self):
        return ServiceHealth(status="offline", mode=self.mode, detail="AGENT_MODE=offline; deterministic AP tool script.")

    async def ensure_capabilities(self):
        return {"model": self.model, "available_models": [self.model], "protocol": "tools", "tool_calling_supported": True, "mode": "offline"}

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
