"""Explicit live/offline planner transports. Neither transport sees a handle table."""

from __future__ import annotations

import json
from typing import Any, Literal

from anthropic import AsyncAnthropic
import httpx
from pydantic import SecretStr

from app.contract_models import ServiceHealth


PLANNER_TIMEOUT_SECONDS = 90


class PlannerError(RuntimeError):
    """Safe failure containing no provider body, credential, or document text."""

    stage = "planner"

    def __init__(self, message: str):
        self.safe_summary = message
        super().__init__(message)


class LivePlannerLLM:
    mode: Literal["live"] = "live"

    def __init__(
        self, *, key: SecretStr, model: str, client: Any = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._key = key
        self.model = model
        self._client = client
        self._transport = transport

    async def health_check(self) -> ServiceHealth:
        if not self._key.get_secret_value():
            return ServiceHealth(status="missing_key", mode=self.mode,
                                 detail="ANTHROPIC_API_KEY is not configured.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=5, follow_redirects=False) as client:
                response = await client.get("https://api.anthropic.com/v1/models", headers={
                    "x-api-key": self._key.get_secret_value(), "anthropic-version": "2023-06-01",
                })
        except Exception:
            return ServiceHealth(status="error", mode=self.mode, detail="Read-only planner health probe failed.")
        if not response.is_success:
            return ServiceHealth(status="error", mode=self.mode,
                                 detail=f"Read-only planner health probe returned HTTP {response.status_code}.")
        try:
            valid = isinstance(response.json().get("data"), list)
        except (ValueError, AttributeError):
            valid = False
        return ServiceHealth(status="ok" if valid else "error", mode=self.mode,
                             detail="Read-only planner health probe succeeded." if valid else
                             "Read-only planner health probe returned an unexpected response shape.")

    async def complete(self, *, prompt: str) -> str:
        if not self._key.get_secret_value():
            raise PlannerError("ANTHROPIC_API_KEY is not configured (planner)")
        if not self.model:
            raise PlannerError("PLANNER_MODEL is not configured (planner)")
        request = json.loads(prompt)
        if set(request) != {"system", "messages"}:
            raise PlannerError("Planner request envelope is invalid (planner)")

        async def invoke(client):
            result = await client.messages.create(
                model=self.model, max_tokens=8192,
                system=request["system"], messages=request["messages"],
            )
            # Preserve generated text exactly for the transcript; the pipeline
            # validates it and retries malformed or incomplete programs once.
            return "".join(block.text for block in result.content if block.type == "text")

        try:
            if self._client is not None:
                return await invoke(self._client)
            # Disable SDK retries: the orchestration layer records every call.
            async with AsyncAnthropic(
                api_key=self._key.get_secret_value(), timeout=PLANNER_TIMEOUT_SECONDS,
                max_retries=0,
            ) as client:
                return await invoke(client)
        except Exception:
            raise PlannerError("Planner request failed (planner); no offline fallback was used") from None


class OfflinePlannerLLM:
    """A deterministic program generator over public handle metadata only."""

    mode: Literal["offline"] = "offline"
    model = "offline-handle-planner-v1"

    async def health_check(self) -> ServiceHealth:
        return ServiceHealth(status="offline", mode=self.mode,
                             detail="PLANNER_MODE=offline; deterministic handle program generator.")

    async def complete(self, *, prompt: str) -> str:
        request = json.loads(prompt)
        metadata = json.loads(request["messages"][0]["content"])
        steps = []

        def arg(kind, ref=None, value=None):
            return {"kind": kind, **({"ref": ref} if ref is not None else {"value": value})}

        for invoice in metadata["invoices"]:
            alias = invoice["invoice_id"]
            handles = {item["name"]: item for item in invoice["handles"]}
            prefix = f"${alias}."
            required = ("vendor_name", "due_date", "amount_due_cents", "remit_account_number", "invoice_number", "po_number")
            if not all(prefix + name in handles for name in required):
                steps.append({"id": f"{alias}_hold", "invoice_id": alias, "op": "hold", "args": {
                    "invoice": arg("trusted", alias), "reason": arg("literal", value="UNVERIFIED_SOURCE"),
                }})
                continue
            vendor_step = f"{alias}_vendor"
            steps.extend([
                {"id": vendor_step, "invoice_id": alias, "op": "resolve_vendor", "args": {
                    "name_handle": arg("handle", prefix + "vendor_name"),
                }},
                {"id": f"{alias}_po", "invoice_id": alias, "op": "match_po", "args": {
                    "invoice": arg("trusted", alias), "vendor": arg("step", vendor_step),
                }},
                {"id": f"{alias}_due", "invoice_id": alias, "op": "due_within", "args": {
                    "date_handle": arg("handle", prefix + "due_date"), "days": arg("literal", value=30),
                }},
                {"id": f"{alias}_pay", "invoice_id": alias, "op": "pay", "args": {
                    "invoice": arg("trusted", alias), "vendor": arg("step", vendor_step),
                    "amount": arg("handle", prefix + "amount_due_cents"),
                    "to_account": arg("handle", prefix + "remit_account_number"),
                    "memo": arg("handle", prefix + "invoice_number"),
                }},
            ])
        return json.dumps({"steps": steps})
