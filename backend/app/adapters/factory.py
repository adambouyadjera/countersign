"""Select configured service modes without any network-error fallback."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.config import Settings
from app.adapters.implementations import (
    AuditEventStore,
    LiveAgentLLM,
    LiveAuditSink,
    LiveBankAdapter,
    LiveExtractorLLM,
    LiveMutatorLLM,
    LivePlannerLLM,
    OfflineAgentLLM,
    OfflineAuditSink,
    OfflineBankAdapter,
    OfflineExtractorLLM,
    OfflineMutatorLLM,
    OfflinePlannerLLM,
)
from app.adapters.interfaces import (
    AgentLLM, AuditSink, BankAdapter, ExtractorLLM, MutatorLLM, PlannerLLM,
    ServiceAdapter,
)


@dataclass(frozen=True)
class AdapterBundle:
    bank: BankAdapter
    audit: AuditSink
    extractor: ExtractorLLM
    planner: PlannerLLM
    agent: AgentLLM
    mutator: MutatorLLM

    @property
    def health_services(self) -> dict[str, ServiceAdapter]:
        # Retain the v1 service key; adapter health detail identifies the actual
        # independently configured unguarded provider and model.
        return {
            "nessie": self.bank, "elastic": self.audit,
            "anthropic": self.planner, "gemini": self.extractor,
            "bitdeer": self.agent,
            "mutator": self.mutator,
        }


def _endpoint(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path}" if base_url else ""


def build_adapters(
    settings: Settings,
    store: AuditEventStore,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AdapterBundle:
    """Build service boundaries without changing configured modes after failure."""
    bank: BankAdapter = (
        OfflineBankAdapter(store=store)
        if settings.nessie_mode == "offline" else
        LiveBankAdapter(
            key=settings.nessie_api_key, base_url=settings.nessie_base_url,
            store=store, transport=transport, payment_method=settings.nessie_payment_method,
        )
    )
    audit: AuditSink = (
        OfflineAuditSink(store) if settings.elastic_mode == "offline" else
        LiveAuditSink(
            key=settings.elastic_api_key, key_name="ELASTIC_API_KEY",
            url=_endpoint(settings.elastic_url, "_security/_authenticate"),
            auth="elastic", transport=transport,
        )
    )
    extractor: ExtractorLLM = (
        LiveExtractorLLM(
            key=settings.gemini_api_key, model=settings.extractor_model, transport=transport,
        ) if settings.extractor_mode == "live" else
        OfflineExtractorLLM(
            detail="EXTRACTOR_MODE=offline; deterministic invoice-layout parser.",
        )
    )
    planner: PlannerLLM = (
        LivePlannerLLM(
            key=settings.anthropic_api_key, model=settings.planner_model, transport=transport,
        ) if settings.planner_mode == "live" else OfflinePlannerLLM()
    )
    naive = settings.naive_provider_config
    agent: AgentLLM = (
        LiveAgentLLM(key=naive.key, base_url=naive.base_url,
                     model=naive.model, provider=naive.provider, transport=transport,
                     request_interval_seconds=settings.naive_request_interval_seconds)
        if settings.agent_mode == "live" else OfflineAgentLLM()
    )
    mutation = settings.mutator_provider_config
    if settings.mutator_mode == "live":
        mutator: MutatorLLM = LiveMutatorLLM(
            key=mutation.key, base_url=mutation.base_url, model=mutation.model,
            provider=mutation.provider, transport=transport,
        )
    else:
        mutator = OfflineMutatorLLM(
            detail="MUTATOR_MODE=offline; attack generation is not implemented in offline mode.",
        )
    return AdapterBundle(
        bank=bank, audit=audit, extractor=extractor,
        planner=planner, agent=agent, mutator=mutator,
    )
