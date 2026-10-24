"""Scripted tool loops verify execution, transcripts, bounds and honest errors."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import SecretStr
import pytest
import pytest_asyncio

from app.adapters.bank import OfflineBankAdapter
from app.adapters.unguarded import AgentError, AgentToolCall, AgentTurn, LiveAgentLLM
from app.contract_models import DocumentText
from app.events import EventBus
from app.pipeline.unguarded_agent import SYSTEM_PROMPT, UnguardedAgent
from app.pipeline.unguarded_tools import UnguardedTools
from app.store import Store
from app.world.batches import create_standard_batch
from app.world.seed import seed_world


class ScriptedLLM:
    mode, model = "offline", "scripted-test-model"

    def __init__(self, turns, repeat=False):
        self.turns, self.repeat, self.requests = list(turns), repeat, []

    async def ensure_capabilities(self):
        return {"protocol": "tools"}

    async def complete(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        return self.turns[0] if self.repeat else self.turns.pop(0)


def call(name, args, identifier="call"):
    return AgentTurn(tool_calls=[AgentToolCall(identifier, name, json.dumps(args))])


@pytest_asyncio.fixture
async def world(tmp_path):
    store = Store(tmp_path / "agent.sqlite")
    store.initialize()
    bank = OfflineBankAdapter(store=store)
    await seed_world(store, bank)
    batch = create_standard_batch(store)
    bus = EventBus(store)
    try:
        yield store, bank, batch, bus
    finally:
        store.close()


def inputs(world, position=0):
    store, bank, batch, bus = world
    invoice = batch.invoices[position]
    document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
    tools = UnguardedTools(store, bus, bank, run_id="run-agent", invoice_id=invoice.invoice_id,
                           vendor_id=invoice.vendor_id)
    target = next(row for row in store.list_records("bank_directory") if row.get("vendor_id") == invoice.vendor_id)
    return invoice, document, tools, target


@pytest.mark.asyncio
async def test_scripted_loop_executes_pay_and_stores_exact_inputs_and_results(world):
    store, bank, batch, bus = world
    invoice, document, tools, target = inputs(world)
    adapter = ScriptedLLM([
        call("list_vendors", {}, "vendors"),
        call("lookup_account", {"account_number": target["display_number"]}, "lookup"),
        call("pay", {"account_id": target["account_id"], "amount_cents": invoice.amount_cents,
                     "invoice_number": invoice.invoice_number, "memo": "Pay invoice"}, "pay"),
        AgentTurn(content="Payment submitted."),
    ])
    bank.transfer = AsyncMock(wraps=bank.transfer)
    result = await UnguardedAgent(adapter, tools, store, bus).run(run_id="run-agent", instruction="Pay this invoice.", document=document)
    assert result.completed and result.error is None
    assert len(result.payments) == len(result.pay_calls) == 1
    bank.transfer.assert_awaited_once()
    assert result.transcript.steps[0].text == SYSTEM_PROMPT
    original = json.loads(result.transcript.steps[1].text)
    assert original == {"instruction": "Pay this invoice.", "document": document.model_dump(mode="json")}
    assert adapter.requests[0]["messages"] == [{"role": "system", "content": SYSTEM_PROMPT},
                                              {"role": "user", "content": result.transcript.steps[1].text}]
    saved = store.get_record("agent_transcripts", f"run-agent:{invoice.invoice_id}")
    assert saved["steps"] == result.transcript.model_dump(mode="json")["steps"]
    events = store.list_events("run-agent")
    assert sum(event.type == "agent.tool_call" for event in events) == 3
    assert any(event.type == "agent.message" for event in events)
    assert any(event.type == "payment.executed" and event.payload.via == "agent" for event in events)
    assert all(event.lane == "unguarded" and event.mode == "offline" for event in events)


@pytest.mark.asyncio
async def test_scripted_loop_processes_multiple_invoices_with_separate_transcripts(world):
    store, _, _, bus = world
    identities, transfers = [], []
    for position in (0, 1):
        invoice, document, tools, target = inputs(world, position)
        adapter = ScriptedLLM([call("pay", {"account_id": target["account_id"], "amount_cents": invoice.amount_cents,
            "invoice_number": invoice.invoice_number, "memo": "Pay invoice"}), AgentTurn(content="Done.")])
        result = await UnguardedAgent(adapter, tools, store, bus).run(run_id="run-agent", instruction="Pay invoices.", document=document)
        assert result.completed
        identities.append(result.transcript.invoice_id)
        transfers.extend(result.payments)
    assert len(set(identities)) == len({payment.transfer_id for payment in transfers}) == 2
    assert len(store.list_records("agent_transcripts", run_id="run-agent")) == 2


@pytest.mark.asyncio
async def test_malformed_tool_call_is_reported_to_model_and_recovers_without_payment(world):
    store, bank, _, bus = world
    _, document, tools, _ = inputs(world)
    adapter = ScriptedLLM([AgentTurn(tool_calls=[AgentToolCall("bad", "pay", "{broken")]),
                           call("unknown_function", {}), AgentTurn(content="I could not make a payment.")])
    bank.transfer = AsyncMock(wraps=bank.transfer)
    result = await UnguardedAgent(adapter, tools, store, bus).run(run_id="run-agent", instruction="Pay invoice.", document=document)
    assert result.completed and result.payments == []
    bank.transfer.assert_not_awaited()
    feedback = json.loads(adapter.requests[1]["messages"][-1]["content"])
    assert feedback["error"] == "malformed_arguments"
    events = [event for event in store.list_events("run-agent") if event.type == "agent.tool_call"]
    assert len(events) == 2 and events[0].payload.tool == "pay"


@pytest.mark.asyncio
async def test_maximum_steps_exits_with_error_instead_of_claiming_completion(world):
    store, bank, _, bus = world
    _, document, tools, _ = inputs(world)
    adapter = ScriptedLLM([call("list_vendors", {})], repeat=True)
    result = await UnguardedAgent(adapter, tools, store, bus, max_steps=3).run(run_id="run-agent", instruction="Pay invoice.", document=document)
    assert len(adapter.requests) == 3
    assert not result.completed and "maximum step" in result.error
    assert not result.payments
    assert store.list_events("run-agent")[-1].type == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("turn", [AgentTurn(content="partial", finish_reason="length"),
                                  AgentTurn(content=None), AgentTurn(content="filtered", finish_reason="content_filter")])
async def test_truncated_filtered_or_empty_responses_are_errors_not_safe_refusals(world, turn):
    store, _, _, bus = world
    _, document, tools, _ = inputs(world)
    result = await UnguardedAgent(ScriptedLLM([turn]), tools, store, bus).run(run_id="run-agent", instruction="Pay invoice.", document=document)
    assert not result.completed and result.error


@pytest.mark.asyncio
async def test_json_action_loop_executes_tools_and_finishes(world):
    store, _, _, bus = world
    _, document, tools, target = inputs(world)
    adapter = ScriptedLLM([AgentTurn(content=json.dumps({"tool": "lookup_account", "args": {"account_number": target["display_number"]}})),
                           AgentTurn(content=json.dumps({"tool": "finish", "args": {}, "message": "No payment requested."}))])
    result = await UnguardedAgent(adapter, tools, store, bus, protocol="json_actions").run(run_id="run-agent", instruction="Inspect invoice.", document=document)
    assert result.completed and len(tools.calls) == 1
    assert "JSON-action protocol" in result.transcript.steps[0].text
    assert adapter.requests[1]["messages"][-1]["role"] == "user"


class ProbeClient:
    def __init__(self, error=None, finish="tool_calls"):
        self.models = SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="chosen")])))
        message = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(function=SimpleNamespace(name="capability_probe", arguments='{"marker":"ready"}'))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(
            side_effect=error, return_value=SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)]))))


@pytest.mark.asyncio
async def test_adapter_lists_models_and_confirms_native_call_once_then_caches():
    client = ProbeClient()
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url="https://bitdeer.invalid/v1", model="chosen", client=client)
    capabilities = await adapter.ensure_capabilities()
    assert capabilities["protocol"] == "tools" and capabilities["tool_calling_supported"]
    await adapter.ensure_capabilities()
    client.models.list.assert_awaited_once()
    client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("synthetic-private-response"), type("AuthError", (Exception,), {"status_code":401})("synthetic-private-response"),
                                   type("CreditError", (Exception,), {"status_code":402})("synthetic-private-response"),
                                   type("ServerError", (Exception,), {"status_code":500})("synthetic-private-response")])
async def test_probe_transport_auth_and_server_errors_never_choose_json_fallback(error):
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url="https://bitdeer.invalid/v1", model="chosen", client=ProbeClient(error))
    with pytest.raises(AgentError) as failure:
        await adapter.ensure_capabilities()
    assert "synthetic-private" not in str(failure.value)
    assert adapter._capabilities is None


@pytest.mark.asyncio
async def test_truncated_capability_probe_is_inconclusive_not_json_fallback():
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url="https://bitdeer.invalid/v1", model="chosen", client=ProbeClient(finish="length"))
    with pytest.raises(AgentError, match="incomplete"):
        await adapter.ensure_capabilities()


def test_documented_system_prompt_is_verbatim():
    from pathlib import Path
    documented = Path("docs/unguarded_lane.md").read_text().split("```text\n", 1)[1].split("```", 1)[0]
    assert documented == SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_openai_sdk_uses_configured_bitdeer_endpoint_and_native_tools():
    import httpx
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url == "https://bitdeer.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-key"
        body = json.loads(request.content)
        assert body["model"] == "chosen" and body["max_tokens"] == 2048
        assert "temperature" not in body
        assert body["tools"][0]["function"]["name"] == "list_vendors"
        assert body["tool_choice"] == "auto"
        return httpx.Response(200, json={"id": "completion", "object": "chat.completion", "created": 0,
            "model": "chosen", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "call-vendors", "type": "function",
                    "function": {"name": "list_vendors", "arguments": "{}"}}],
            }}]})
    adapter = LiveAgentLLM(key=SecretStr("synthetic-key"), base_url="https://bitdeer.invalid/v1", model="chosen",
                           transport=httpx.MockTransport(respond))
    turn = await adapter.complete(messages=[{"role": "user", "content": "Process invoice."}], tools=[{
        "type": "function", "function": {"name": "list_vendors", "parameters": {"type": "object", "properties": {}}},
    }])
    assert turn.tool_calls[0] == AgentToolCall("call-vendors", "list_vendors", "{}")
    assert turn.finish_reason == "tool_calls" and len(requests) == 1


@pytest.mark.asyncio
async def test_missing_key_never_calls_client_or_chooses_offline_mode():
    client = ProbeClient()
    adapter = LiveAgentLLM(key=SecretStr(""), base_url="https://bitdeer.invalid/v1", model="chosen", client=client)
    with pytest.raises(AgentError, match="not configured"):
        await adapter.ensure_capabilities()
    client.models.list.assert_not_awaited()
    assert adapter.mode == "live"
