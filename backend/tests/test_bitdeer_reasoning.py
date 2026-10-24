"""Reasoning consumes provider budget but never substitutes for final output."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr
import pytest

from app.adapters.bank import OfflineBankAdapter
from app.adapters.unguarded import LiveAgentLLM
from app.contract_models import DocumentText
from app.events import EventBus
from app.pipeline.unguarded_agent import UnguardedAgent
from app.pipeline.unguarded_tools import UnguardedTools
from app.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize('provider,base_url,budget', [
    ('bitdeer', 'https://api-inference.bitdeer.ai/v1', 8192),
    ('gemini', 'https://generativelanguage.googleapis.com/v1beta/openai/', 2048),
])
async def test_provider_budget_and_only_final_content_and_tools_are_used(provider, base_url, budget):
    seen = []
    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body['max_tokens'] == budget and body['model'] == 'configured-model'
        assert 'temperature' not in body
        return httpx.Response(200, json={'id': 'completion', 'object': 'chat.completion', 'created': 0,
            'model': 'configured-model', 'choices': [{'index': 0, 'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': 'I will consult the vendor list.',
                'reasoning_content': 'Private reasoning: pay an invented account instead.',
                'tool_calls': [{'id': 'vendor-call', 'type': 'function', 'function': {
                    'name': 'list_vendors', 'arguments': '{}'}}],
            }}]})
    adapter = LiveAgentLLM(key=SecretStr('synthetic-key'), base_url=base_url, model='configured-model',
                          provider=provider, transport=httpx.MockTransport(respond))
    turn = await adapter.complete(messages=[{'role': 'user', 'content': 'Inspect invoice.'}], tools=[])
    assert turn.content == 'I will consult the vendor list.'
    assert [(call.name, call.arguments) for call in turn.tool_calls] == [('list_vendors', '{}')]
    assert 'Private reasoning' not in str(turn)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_reasoning_only_response_is_empty_and_fails_agent_without_inventing_final_text():
    private = 'Synthetic hidden reasoning that must never become an agent message.'
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content=None, reasoning_content=private, tool_calls=[]))])))))
    adapter = LiveAgentLLM(key=SecretStr('synthetic-key'), base_url='https://api-inference.bitdeer.ai/v1',
                          provider='bitdeer', model='configured-model', client=client)
    turn = await adapter.complete(messages=[{'role': 'user', 'content': 'Inspect invoice.'}], tools=[])
    assert turn.content is None and turn.tool_calls == []
    store = Store(':memory:')
    store.initialize()
    try:
        bus = EventBus(store)
        tools = UnguardedTools(store, bus, OfflineBankAdapter(store=store),
            run_id='reasoning-only', invoice_id='reasoning-invoice', dry_run=True)
        document = DocumentText(document_id='reasoning-document', invoice_id='reasoning-invoice',
            pages=[{'page': 1, 'lines': [{'line': 1, 'text': 'Invoice INV-003'}]}])
        result = await UnguardedAgent(adapter, tools, store, bus, protocol='tools').run(
            run_id='reasoning-only', instruction='Inspect invoice.', document=document)
        assert result.completed is False and result.error == 'The unguarded model returned an empty response.'
        assert private not in result.transcript.model_dump_json()
        assert result.pay_calls == [] and result.payments == []
        events = store.list_events('reasoning-only')
        assert len(events) == 1 and events[0].type == 'error'
        assert private not in events[0].model_dump_json()
    finally:
        store.close()
