"""Unguarded audit records identify the selected transport even on failure."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.adapters.bank import OfflineBankAdapter
from app.adapters.unguarded import AgentError, AgentToolCall, AgentTurn
from app.contract_models import AgentTranscript, DocumentText
from app.events import EventBus
from app.pipeline.unguarded_agent import SYSTEM_PROMPT, UnguardedAgent
from app.pipeline.unguarded_tools import UnguardedTools
from app.store import Store
from app.world.batches import create_standard_batch
from app.world.seed import seed_world


def test_ap_prompt_matches_the_merged_b5_prompt_bytes():
    # Frozen before provider switching: evaluation must not tune the baseline.
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == (
        "3876772d8b2d421d5a9be9740632801e31864f2c7438d6b54d22ae6cc1d4d253"
    )


class ProviderScript:
    mode = "live"

    def __init__(self, turns=(), *, provider="gemini", model="gemini-test", preflight_error=None):
        self.provider, self.model = provider, model
        self.turns, self.requests = list(turns), []
        self.preflight_error = preflight_error
        self.preflights = 0

    async def ensure_capabilities(self):
        self.preflights += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        return {"protocol": "tools", "provider": self.provider, "model": self.model}

    async def complete(self, **request):
        self.requests.append(deepcopy(request))
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        return turn


@pytest_asyncio.fixture
async def world(tmp_path):
    store = Store(tmp_path / "provider-events.sqlite")
    store.initialize()
    bank = OfflineBankAdapter(store=store)
    await seed_world(store, bank)
    batch = create_standard_batch(store)
    invoice = batch.invoices[0]
    document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
    bus = EventBus(store)
    tools = UnguardedTools(store, bus, bank, run_id="provider-run", invoice_id=invoice.invoice_id,
                           vendor_id=invoice.vendor_id)
    target = next(row for row in store.list_records("bank_directory") if row.get("vendor_id") == invoice.vendor_id)
    try:
        yield store, bank, invoice, document, bus, tools, target
    finally:
        store.close()


def tool(name, args, *, identifier="call", extra_content=None):
    kwargs = {"extra_content": extra_content} if extra_content is not None else {}
    return AgentTurn(tool_calls=[AgentToolCall(identifier, name, json.dumps(args), **kwargs)])


@pytest.mark.asyncio
async def test_provider_and_model_label_messages_tools_payments_and_transcript(world):
    store, bank, invoice, document, bus, tools, target = world
    # A caller can retain tools from an older configuration. The active agent,
    # rather than that stale label or bank mode, identifies this execution.
    tools.provider, tools.model = "bitdeer", "stale-Qwen-model"
    adapter = ProviderScript([
        tool("pay", {"account_id": target["account_id"], "amount_cents": invoice.amount_cents,
                     "invoice_number": invoice.invoice_number, "memo": "Invoice payment"}),
        AgentTurn(content="Payment submitted."),
    ])
    result = await UnguardedAgent(adapter, tools, store, bus).run(
        run_id="provider-run", instruction="Pay the invoice.", document=document)
    assert result.completed and result.transcript.provider == "gemini"
    assert result.transcript.model == "gemini-test"
    assert result.transcript.steps[0].text == SYSTEM_PROMPT
    saved = AgentTranscript.model_validate({key: value for key, value in store.get_record(
        "agent_transcripts", f"provider-run:{invoice.invoice_id}").items() if key != "transcript_id"})
    assert saved.provider == "gemini" and saved.model == "gemini-test"
    events = store.list_events("provider-run")
    assert {event.type for event in events} >= {"agent.tool_call", "agent.message", "payment.executed"}
    assert all(event.provider == "gemini" and event.model == "gemini-test" for event in events)
    # The model provider label is independent of the bank's offline mode.
    assert all(event.mode == "offline" for event in events)
    assert store.get_record("transfers", result.payments[0].transfer_id)["provider"] == "gemini"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "generation"])
async def test_402_has_exact_failure_message_and_never_switches_provider(world, phase):
    store, bank, invoice, document, bus, tools, target = world
    failure = AgentError("private response must not be reflected", http_status=402, code="provider_out_of_credits")
    adapter = ProviderScript([failure], provider="bitdeer", model="Qwen/test",
                             preflight_error=failure if phase == "preflight" else None)
    bank.transfer = AsyncMock(wraps=bank.transfer)
    result = await UnguardedAgent(adapter, tools, store, bus).run(
        run_id="provider-run", instruction="Pay the invoice.", document=document)
    assert not result.completed
    assert result.error == "unguarded agent provider out of credits (402)"
    assert result.transcript.provider == "bitdeer" and result.transcript.model == "Qwen/test"
    assert adapter.preflights == 1
    assert len(adapter.requests) == (0 if phase == "preflight" else 1)
    bank.transfer.assert_not_awaited()
    events = store.list_events("provider-run")
    error = next(event for event in events if event.type == "error")
    assert error.payload.message == "unguarded agent provider out of credits (402)"
    assert error.provider == "bitdeer" and error.model == "Qwen/test"
    assert "private response" not in json.dumps([event.model_dump(mode="json") for event in events])


@pytest.mark.asyncio
async def test_non_402_provider_error_is_safe_and_keeps_actual_provider(world):
    store, bank, invoice, document, bus, tools, target = world
    adapter = ProviderScript(preflight_error=AgentError("synthetic-private", http_status=401))
    result = await UnguardedAgent(adapter, tools, store, bus).run(
        run_id="provider-run", instruction="Pay the invoice.", document=document)
    assert not result.completed and "HTTP 401" in result.error
    assert "out of credits" not in result.error and "synthetic-private" not in result.error
    assert all(event.provider == "gemini" and event.model == "gemini-test"
               for event in store.list_events("provider-run"))


@pytest.mark.asyncio
async def test_gemini_function_metadata_is_replayed_with_tool_result(world):
    store, bank, invoice, document, bus, tools, target = world
    extra = {"google": {"thought_signature": "opaque-provider-signature"}}
    adapter = ProviderScript([
        tool("list_vendors", {}, extra_content=extra),
        AgentTurn(content="Vendor details reviewed."),
    ])
    result = await UnguardedAgent(adapter, tools, store, bus).run(
        run_id="provider-run", instruction="Inspect the invoice.", document=document)
    assert result.completed
    second_messages = adapter.requests[1]["messages"]
    assert second_messages[-2]["tool_calls"][0]["extra_content"] == extra
    assert second_messages[-1]["tool_call_id"] == second_messages[-2]["tool_calls"][0]["id"]
    assert result.transcript.provider == "gemini"


@pytest.mark.asyncio
async def test_sdk_shaped_tool_metadata_is_replayed_without_loss(world):
    store, bank, invoice, document, bus, tools, target = world
    extra = {"google": {"thought_signature": "opaque-sdk-signature"}}
    shaped = SimpleNamespace(content=None, finish_reason="tool_calls", tool_calls=[SimpleNamespace(
        id="native-call", function=SimpleNamespace(name="list_vendors", arguments="{}"), extra_content=extra,
    )])
    adapter = ProviderScript([shaped, AgentTurn(content="Done.")])
    result = await UnguardedAgent(adapter, tools, store, bus).run(
        run_id="provider-run", instruction="Inspect the invoice.", document=document)
    assert result.completed
    assert adapter.requests[1]["messages"][-2]["tool_calls"][0]["extra_content"] == extra


@pytest.mark.asyncio
async def test_max_step_error_retains_provider_and_model(world):
    store, bank, invoice, document, bus, tools, target = world
    adapter = ProviderScript([tool("list_vendors", {})])
    result = await UnguardedAgent(adapter, tools, store, bus, max_steps=1).run(
        run_id="provider-run", instruction="Inspect the invoice.", document=document)
    assert not result.completed and "maximum step" in result.error
    assert all(event.provider == "gemini" and event.model == "gemini-test"
               for event in store.list_events("provider-run"))
