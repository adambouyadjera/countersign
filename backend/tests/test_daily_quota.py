"""Explicit daily Google quota failures stop retries without switching models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr
import pytest

from app.adapters.bank import OfflineBankAdapter
from app.adapters.unguarded import AgentError, LiveAgentLLM
from app.config import GEMINI_OPENAI_BASE_URL
from app.contract_models import DocumentText
from app.events import EventBus
from app.pipeline.unguarded_agent import UnguardedAgent
from app.pipeline.unguarded_tools import UnguardedTools
from app.store import Store


def quota_error(quota_id, *, wrapped=False):
    failure = RuntimeError('synthetic-private-provider-message')
    failure.status_code = 429
    failure.body = {'error': {'message': 'synthetic-private-provider-message', 'details': [
        {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{
            'quotaId': quota_id, 'quotaValue': '500',
            'description': 'synthetic-private-provider-message',
        }]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '4s'},
    ]}}
    if wrapped:
        failure.body = [failure.body]
    failure.response = SimpleNamespace(headers=httpx.Headers())
    return failure


def response():
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content='Done.', tool_calls=[], reasoning_content='Synthetic reasoning is ignored.'))])


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def service(side_effect):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=side_effect))))
    adapter = LiveAgentLLM(key=SecretStr('synthetic-key'), base_url=GEMINI_OPENAI_BASE_URL,
                          provider='gemini', model='configured-model', client=client)
    clock = Clock()
    adapter._clock, adapter._sleep = clock, clock.sleep
    return adapter, client, clock


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
async def test_daily_quota_does_not_retry_or_cache_or_change_provider(wrapped):
    adapter, client, clock = service([
        quota_error('GenerateRequestsPerDayPerProjectPerModel-FreeTier', wrapped=wrapped), response(),
    ])
    with pytest.raises(AgentError) as failed:
        await adapter.complete(messages=[], tools=[])
    assert failed.value.code == 'provider_daily_quota_exhausted'
    assert failed.value.http_status == 429 and failed.value.attempts == 1
    assert failed.value.quota_limit == 500
    assert str(failed.value) == 'unguarded agent provider daily quota exhausted (429)'
    assert 'synthetic-private' not in str(failed.value)
    assert client.chat.completions.create.await_count == 1 and clock.sleeps == []
    assert adapter.request_stats == {'attempts': 1, 'retries': 0, 'transient_http_statuses': {'429': 1},
                                     'quota_limits': [500], 'last_retry_after_seconds': 4.0}
    # An explicit later request can succeed; no daily-failure cache blocks it.
    assert (await adapter.complete(messages=[], tools=[])).content == 'Done.'
    assert client.chat.completions.create.await_count == 2
    assert (adapter.provider, adapter.model, adapter.mode) == ('gemini', 'configured-model', 'live')
    assert all(call.kwargs['model'] == 'configured-model' for call in client.chat.completions.create.await_args_list)


@pytest.mark.asyncio
async def test_minute_quota_still_receives_three_bounded_attempts():
    adapter, client, clock = service(quota_error('GenerateRequestsPerMinutePerProjectPerModel-FreeTier'))
    with pytest.raises(AgentError) as failed:
        await adapter.complete(messages=[], tools=[])
    assert failed.value.code == 'inference_request_failed'
    assert failed.value.attempts == client.chat.completions.create.await_count == 3
    assert clock.sleeps == [4.0, 4.0]
    assert adapter.request_stats['attempts'] == 3 and adapter.request_stats['retries'] == 2


@pytest.mark.asyncio
async def test_daily_quota_in_preflight_preserves_specific_code_and_never_chooses_fallback():
    adapter, client, clock = service(quota_error('GenerateRequestsPerDayPerProjectPerModel-FreeTier', wrapped=True))
    client.models = SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id='configured-model')])))
    with pytest.raises(AgentError) as failed:
        await adapter.ensure_capabilities()
    assert failed.value.code == 'provider_daily_quota_exhausted'
    assert adapter._capabilities is None
    assert adapter.capability_report['probes'][0]['error_code'] == 'provider_daily_quota_exhausted'
    assert client.chat.completions.create.await_count == 1 and clock.sleeps == []


@pytest.mark.asyncio
async def test_pipeline_reports_daily_quota_clearly_without_payment_or_provider_switch():
    adapter, _, _ = service(quota_error('GenerateRequestsPerDayPerProjectPerModel-FreeTier'))
    store = Store(':memory:')
    store.initialize()
    try:
        bus = EventBus(store)
        tools = UnguardedTools(store, bus, OfflineBankAdapter(store=store), run_id='daily-run',
                               invoice_id='daily-invoice', dry_run=True)
        document = DocumentText(document_id='daily-document', invoice_id='daily-invoice', pages=[])
        result = await UnguardedAgent(adapter, tools, store, bus, protocol='tools').run(
            run_id='daily-run', instruction='Inspect invoice.', document=document)
        assert not result.completed and result.payments == [] and result.pay_calls == []
        assert result.error == 'unguarded agent provider daily quota exhausted (429)'
        events = store.list_events('daily-run')
        assert len(events) == 1 and events[0].type == 'error'
        assert events[0].payload.message == result.error
        assert (events[0].provider, events[0].model) == ('gemini', 'configured-model')
        assert 'Synthetic reasoning' not in result.transcript.model_dump_json()
    finally:
        store.close()
