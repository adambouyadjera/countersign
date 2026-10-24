"""Labelled service skeletons and genuine, read-only health probes.

Business operations intentionally fail until their feature is implemented.
No implementation changes modes after a failed network request.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, Protocol

import httpx
from pydantic import SecretStr, TypeAdapter

from app.contract_models import (
    AgentTranscript,
    AuditAnswer,
    AuditAskRequest,
    DocumentText,
    Placement,
    RunEvent,
    ServiceHealth,
)

_EVENT = TypeAdapter(RunEvent)


class AuditEventStore(Protocol):
    def persist_audit_event(self, event: RunEvent) -> RunEvent: ...


class _RedactQueryKey(logging.Filter):
    """Nessie authenticates in the URL; httpx INFO logs must redact it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(
            r"([?&]key=)[^&\s\"']+", r"\1[REDACTED]", record.getMessage()
        )
        record.args = ()
        return True


_HTTPX_LOGGER = logging.getLogger("httpx")
if not any(isinstance(item, _RedactQueryKey) for item in _HTTPX_LOGGER.filters):
    _HTTPX_LOGGER.addFilter(_RedactQueryKey())


class _LiveService:
    mode: Literal["live"] = "live"

    def __init__(
        self,
        *,
        key: SecretStr,
        key_name: str,
        url: str,
        auth: Literal["nessie", "elastic", "anthropic", "gemini", "bearer"],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = key
        self._key_name = key_name
        self._url = url
        self._auth = auth
        self._transport = transport

    async def health_check(self) -> ServiceHealth:
        key = self._key.get_secret_value()
        if not key:
            return ServiceHealth(
                status="missing_key", mode=self.mode,
                detail=f"{self._key_name} is not configured.",
            )
        if not self._url:
            return ServiceHealth(
                status="error", mode=self.mode,
                detail="The service base URL is not configured.",
            )

        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        if self._auth == "nessie":
            params["key"] = key
        elif self._auth == "elastic":
            headers["Authorization"] = f"ApiKey {key}"
        elif self._auth == "anthropic":
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        elif self._auth == "gemini":
            headers["x-goog-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"

        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=5.0, follow_redirects=False,
            ) as client:
                response = await client.get(self._url, headers=headers, params=params)
        except httpx.TimeoutException:
            return ServiceHealth(
                status="error", mode=self.mode, detail="Read-only health probe timed out.",
            )
        except Exception:
            # Exception text may contain credential-bearing URLs. Never expose it.
            return ServiceHealth(
                status="error", mode=self.mode, detail="Read-only health probe failed.",
            )

        if not response.is_success:
            return ServiceHealth(
                status="error", mode=self.mode,
                detail=f"Read-only health probe returned HTTP {response.status_code}.",
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        valid_shape = (
            isinstance(body, list) if self._auth == "nessie" else
            isinstance(body, dict) and isinstance(body.get("username"), str)
            if self._auth == "elastic" else
            isinstance(body, dict) and isinstance(body.get("models", []), list)
            if self._auth == "gemini" else
            isinstance(body, dict) and isinstance(body.get("data"), list)
        )
        if not valid_shape:
            return ServiceHealth(
                status="error", mode=self.mode,
                detail="Read-only health probe returned an unexpected response shape.",
            )
        return ServiceHealth(
            status="ok", mode=self.mode,
            detail="Read-only health probe succeeded; business operations are not implemented.",
        )


class _OfflineService:
    mode: Literal["offline"] = "offline"

    def __init__(self, *, detail: str, missing_key: bool = False) -> None:
        self._detail = detail
        self._missing_key = missing_key

    async def health_check(self) -> ServiceHealth:
        return ServiceHealth(
            status="missing_key" if self._missing_key else "offline",
            mode=self.mode, detail=self._detail,
        )


# Preserve the B1 import surface while the full bank implementation is isolated.
from app.adapters.bank import LiveBankAdapter, OfflineBankAdapter


class LiveAuditSink(_LiveService):
    async def write_event(self, event: RunEvent) -> RunEvent:
        raise NotImplementedError("Elastic event indexing is not implemented.")

    async def ask(self, request: AuditAskRequest) -> AuditAnswer:
        raise NotImplementedError("Elastic audit questions are not implemented.")


class OfflineAuditSink(_OfflineService):
    def __init__(self, store: AuditEventStore) -> None:
        super().__init__(detail="ELASTIC_MODE=offline; audit events are stored in SQLite only.")
        self._store = store

    async def write_event(self, event: RunEvent) -> RunEvent:
        """Persist an already-sequenced event, explicitly labelled offline.

        Event producers must apply this mode before publication to keep SSE and
        stored history identical. A conflicting existing sequence is rejected.
        """
        data = _EVENT.validate_python(event).model_dump(mode="python")
        data["mode"] = "offline"
        labelled = _EVENT.validate_python(data)
        return self._store.persist_audit_event(labelled)

    async def ask(self, request: AuditAskRequest) -> AuditAnswer:
        raise NotImplementedError("Audit questions require the live Elastic adapter.")


from app.pipeline.extractor import LiveExtractorLLM, OfflineExtractorLLM


from app.adapters.planner import LivePlannerLLM, OfflinePlannerLLM


class _AgentOperations:
    async def run(
        self, *, instruction: str, document: DocumentText
    ) -> AgentTranscript:
        raise NotImplementedError("Unguarded agent requests are not implemented.")


class LiveAgentLLM(_AgentOperations, _LiveService):
    pass


class OfflineAgentLLM(_AgentOperations, _OfflineService):
    pass


class _MutatorOperations:
    async def mutate(self, *, template: str, placement: Placement) -> str:
        raise NotImplementedError("Attack mutation is not implemented.")


class LiveMutatorLLM(_MutatorOperations, _LiveService):
    pass


class OfflineMutatorLLM(_MutatorOperations, _OfflineService):
    pass
