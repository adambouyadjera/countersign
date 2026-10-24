"""Boundaries for external services; implementations contain no payment policy."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from app.adapters.bank import BankBalance, BankTransfer
from app.contract_models import (
    AgentTranscript,
    AuditAnswer,
    AuditAskRequest,
    Balances,
    DocumentText,
    FieldsExtractedPayload,
    Payment,
    Placement,
    RunEvent,
    ServiceHealth,
)

AdapterMode = Literal["live", "offline"]


@runtime_checkable
class ServiceAdapter(Protocol):
    @property
    def mode(self) -> AdapterMode: ...

    async def health_check(self) -> ServiceHealth: ...


@runtime_checkable
class BankAdapter(ServiceAdapter, Protocol):
    async def get_balance(self, account_id: str) -> BankBalance: ...

    async def list_transfers(self, account_id: str) -> list[BankTransfer]: ...

    async def list_customers(self) -> list[dict[str, Any]]: ...

    async def create_customer(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def list_accounts(self, customer_id: str) -> list[dict[str, Any]]: ...

    async def create_account(self, customer_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def get_balances(self, session_started_at: datetime) -> Balances: ...

    async def transfer(
        self, from_id: str | None = None, to_id: str | None = None,
        amount_cents: int | None = None, description: str = "", *,
        from_account_id: str | None = None, to_account_id: str | None = None,
    ) -> Payment: ...


@runtime_checkable
class AuditSink(ServiceAdapter, Protocol):
    async def write_event(self, event: RunEvent) -> RunEvent: ...

    async def ask(self, request: AuditAskRequest) -> AuditAnswer: ...


@runtime_checkable
class ExtractorLLM(ServiceAdapter, Protocol):
    # There is deliberately no tool parameter on this boundary.
    async def extract(self, document: DocumentText) -> FieldsExtractedPayload: ...


@runtime_checkable
class PlannerLLM(ServiceAdapter, Protocol):
    # The pipeline assembles and checks this serialized system/messages envelope
    # before entering the transport. No handle table is exposed here.
    model: str

    async def complete(self, *, prompt: str) -> str: ...


@runtime_checkable
class AgentLLM(ServiceAdapter, Protocol):
    async def run(
        self, *, instruction: str, document: DocumentText
    ) -> AgentTranscript: ...


@runtime_checkable
class MutatorLLM(ServiceAdapter, Protocol):
    async def mutate(self, *, template: str, placement: Placement) -> str: ...
