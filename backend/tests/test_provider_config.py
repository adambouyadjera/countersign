"""Provider routing never borrows a credential from another service."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr
import pytest

from app.adapters.factory import build_adapters
from app.adapters.unguarded import AgentError, LiveAgentLLM
from app.config import GEMINI_OPENAI_BASE_URL, Settings
from app.store import Store


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def settings(**overrides):
    return Settings(_env_file=None, bitdeer_api_key="synthetic-bitdeer", gemini_api_key="synthetic-gemini", **overrides)


def test_default_agent_and_mutator_retain_independent_bitdeer_models():
    config = settings()
    agent, mutator = config.naive_provider_config, config.mutator_provider_config
    assert agent.provider == mutator.provider == "bitdeer"
    assert agent.base_url == mutator.base_url == config.bitdeer_base_url
    assert agent.key.get_secret_value() == mutator.key.get_secret_value() == "synthetic-bitdeer"
    assert agent.model == "Qwen/Qwen3.8-27B"
    assert mutator.model == "zai-org/GLM-5.3"
    assert "synthetic-bitdeer" not in repr(agent)


def test_blank_model_names_preserve_defaults_for_names_only_example():
    config = settings(naive_model="", mutator_model=" ")
    assert config.naive_model == "Qwen/Qwen3.8-27B"
    assert config.mutator_model == "zai-org/GLM-5.3"


def test_explicit_gemini_agent_reuses_existing_gemini_key_without_changing_mutator():
    config = settings(naive_base_url=GEMINI_OPENAI_BASE_URL, naive_model="gemini-3.6-flash")
    assert config.naive_provider_config.provider == "gemini"
    assert config.naive_provider_config.key.get_secret_value() == "synthetic-gemini"
    assert config.naive_provider_config.model == "gemini-3.6-flash"
    assert config.mutator_provider_config.provider == "bitdeer"
    assert config.mutator_provider_config.key.get_secret_value() == "synthetic-bitdeer"


def test_mutator_is_independently_configurable_and_explicit_keys_take_precedence():
    config = settings(naive_base_url=GEMINI_OPENAI_BASE_URL, naive_api_key="synthetic-agent-override",
                      naive_model="gemini-3.6-flash", mutator_base_url="https://mutator.invalid/v1",
                      mutator_api_key="synthetic-mutator", mutator_model="mutation-model")
    assert config.naive_provider_config.key.get_secret_value() == "synthetic-agent-override"
    assert config.mutator_provider_config.provider == "custom"
    assert config.mutator_provider_config.key.get_secret_value() == "synthetic-mutator"
    assert config.mutator_provider_config.model == "mutation-model"


def test_mutator_gemini_reuses_gemini_key_without_rerouting_agent():
    config = settings(mutator_base_url=GEMINI_OPENAI_BASE_URL, mutator_model="gemini-3.6-flash")
    assert config.mutator_provider_config.provider == "gemini"
    assert config.mutator_provider_config.key.get_secret_value() == "synthetic-gemini"
    assert config.naive_provider_config.provider == "bitdeer"


@pytest.mark.parametrize("base_url", ["https://custom.invalid/v1", "https://generativelanguage.googleapis.com.evil.invalid/v1"])
def test_unknown_provider_never_inherits_bitdeer_or_gemini_key(base_url):
    config = settings(naive_base_url=base_url, mutator_base_url=base_url)
    assert config.naive_provider_config.provider == "custom"
    assert config.naive_provider_config.key.get_secret_value() == ""
    assert config.mutator_provider_config.key.get_secret_value() == ""


def test_factory_uses_both_independent_provider_routes():
    store = Store(":memory:")
    store.initialize()
    try:
        config = settings(naive_base_url=GEMINI_OPENAI_BASE_URL, naive_model="gemini-3.6-flash",
                          mutator_base_url="https://mutator.invalid/v1", mutator_api_key="synthetic-mutation",
                          mutator_model="mutation-model")
        bundle = build_adapters(config, store)
        assert bundle.agent.provider == "gemini" and bundle.agent.model == "gemini-3.6-flash"
        assert bundle.agent.base_url == GEMINI_OPENAI_BASE_URL
        assert bundle.agent._key.get_secret_value() == "synthetic-gemini"
        assert bundle.mutator.provider == "custom" and bundle.mutator.model == "mutation-model"
        assert bundle.mutator.base_url == "https://mutator.invalid/v1"
        assert bundle.mutator._key.get_secret_value() == "synthetic-mutation"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_http_402_is_exact_credit_error_without_switching_endpoint_or_model():
    error = type("CreditsExhausted", (Exception,), {"status_code": 402})("synthetic-private-body")
    client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(id="chosen")]))),
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=error))))
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url="https://api-inference.bitdeer.ai/v1", model="chosen", client=client)
    with pytest.raises(AgentError, match=r"^unguarded agent provider out of credits \(402\)$") as failure:
        await adapter.ensure_capabilities()
    assert failure.value.http_status == 402 and failure.value.code == "provider_out_of_credits"
    assert adapter.provider == "bitdeer" and adapter.model == "chosen"
    assert adapter.base_url == "https://api-inference.bitdeer.ai/v1"
    client.chat.completions.create.assert_awaited_once()
    assert adapter._capabilities is None


@pytest.mark.asyncio
async def test_gemini_compatibility_preserves_opaque_tool_continuation_metadata():
    signature = {"google": {"thought_signature": "opaque-continuation-not-reasoning"}}

    def respond(request):
        assert request.url == GEMINI_OPENAI_BASE_URL + "chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-gemini"
        assert json.loads(request.content)["tools"][0]["function"]["name"] == "list_vendors"
        return httpx.Response(200, json={"id": "completion", "object": "chat.completion", "created": 0,
            "model": "gemini-3.6-flash", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "vendors", "type": "function",
                    "function": {"name": "list_vendors", "arguments": "{}"}, "extra_content": signature}],
            }}]})

    adapter = LiveAgentLLM(key=SecretStr("synthetic-gemini"), base_url=GEMINI_OPENAI_BASE_URL,
                           model="gemini-3.6-flash", transport=httpx.MockTransport(respond))
    turn = await adapter.complete(messages=[{"role": "user", "content": "Read invoice"}], tools=[{
        "type": "function", "function": {"name": "list_vendors", "parameters": {"type": "object", "properties": {}}},
    }])
    assert adapter.provider == "gemini"
    assert turn.tool_calls[0].extra_content == signature


@pytest.mark.asyncio
async def test_gemini_catalog_model_prefix_is_an_alias_not_a_different_model():
    tool_call = SimpleNamespace(function=SimpleNamespace(name="capability_probe", arguments='{"marker":"ready"}'))
    client = SimpleNamespace(models=SimpleNamespace(list=AsyncMock(return_value=SimpleNamespace(data=[
        SimpleNamespace(id="models/gemini-3.6-flash")]))), chat=SimpleNamespace(completions=SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls",
            message=SimpleNamespace(tool_calls=[tool_call]))])))))
    adapter = LiveAgentLLM(key=SecretStr("synthetic-gemini"), base_url=GEMINI_OPENAI_BASE_URL,
                           model="gemini-3.6-flash", client=client)
    capability = await adapter.ensure_capabilities()
    assert capability["provider"] == "gemini" and capability["protocol"] == "tools"
    assert capability["chosen_model"] == adapter.model == "gemini-3.6-flash"
    assert client.chat.completions.create.await_args.kwargs["model"] == "gemini-3.6-flash"


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def quota_error(status, *, retry_after=None, retry_delay=None, limit=None):
    details = []
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    if limit is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaValue": limit, "description": "synthetic-private-provider-message"}]})
    error = RuntimeError("synthetic-private-provider-message")
    error.status_code = status
    error.body = {"error": {"details": details, "message": "synthetic-private-provider-message"}}
    error.response = SimpleNamespace(headers=httpx.Headers({"Retry-After": retry_after} if retry_after is not None else {}))
    return error


def completion_response():
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="Done", tool_calls=[]))])


@pytest.mark.asyncio
async def test_transient_retry_preserves_prompt_and_uses_safe_retry_info():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=[
        quota_error(429, retry_after="1", retry_delay="3s", limit="15"), completion_response()]))))
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url=GEMINI_OPENAI_BASE_URL,
                          model="gemini-3.5-flash-lite", client=client)
    clock = FakeClock()
    adapter._clock, adapter._sleep = clock, clock.sleep
    messages = [{"role": "user", "content": "Fixed AP prompt and invoice"}]
    result = await adapter.complete(messages=messages, tools=[])
    assert result.content == "Done"
    assert clock.sleeps == [3.0]
    assert client.chat.completions.create.await_args_list[0] == client.chat.completions.create.await_args_list[1]
    assert adapter.request_stats == {"attempts": 2, "retries": 1, "transient_http_statuses": {"429": 1},
                                     "quota_limits": [15], "last_retry_after_seconds": 3.0}
    assert adapter.provider == "gemini" and adapter.model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_request_gate_spaces_calls_without_real_sleep():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=completion_response()))))
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url=GEMINI_OPENAI_BASE_URL,
                          model="gemini-3.5-flash-lite", client=client, request_interval_seconds=5)
    clock = FakeClock()
    adapter._clock, adapter._sleep = clock, clock.sleep
    await adapter.complete(messages=[], tools=[])
    await adapter.complete(messages=[], tools=[])
    await adapter.complete(messages=[], tools=[])
    assert clock.sleeps == [5.0, 5.0]
    assert adapter.request_stats["attempts"] == 3 and adapter.request_stats["retries"] == 0


@pytest.mark.asyncio
async def test_retries_are_bounded_and_expose_quota_metadata_without_provider_body():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(
        side_effect=quota_error(429, retry_after="120", retry_delay="500s", limit="15")))))
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url=GEMINI_OPENAI_BASE_URL,
                          model="gemini-3.5-flash-lite", client=client)
    clock = FakeClock()
    adapter._clock, adapter._sleep = clock, clock.sleep
    with pytest.raises(AgentError) as failure:
        await adapter.complete(messages=[], tools=[])
    assert failure.value.http_status == 429 and failure.value.attempts == 3
    assert failure.value.retry_after_seconds == 60 and failure.value.quota_limit == 15
    assert clock.sleeps == [60.0, 60.0]
    assert client.chat.completions.create.await_count == 3
    assert "synthetic-private" not in str(failure.value)


@pytest.mark.asyncio
async def test_503_uses_bounded_backoff_and_402_never_retries():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=[
        quota_error(503), quota_error(503), completion_response()]))))
    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url=GEMINI_OPENAI_BASE_URL,
                          model="gemini-3.5-flash-lite", client=client)
    clock = FakeClock()
    adapter._clock, adapter._sleep = clock, clock.sleep
    await adapter.complete(messages=[], tools=[])
    assert clock.sleeps == [1.0, 2.0]
    client.chat.completions.create = AsyncMock(side_effect=quota_error(402))
    with pytest.raises(AgentError, match=r"^unguarded agent provider out of credits \(402\)$"):
        await adapter.complete(messages=[], tools=[])
    assert client.chat.completions.create.await_count == 1
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.parametrize("interval", [-1, float("inf"), float("nan"), True])
def test_request_interval_rejects_invalid_configuration(interval):
    with pytest.raises(ValueError):
        LiveAgentLLM(key=SecretStr("synthetic"), base_url=GEMINI_OPENAI_BASE_URL,
                     model="gemini-3.5-flash-lite", request_interval_seconds=interval)


@pytest.mark.asyncio
async def test_waiting_request_rechecks_a_new_shared_provider_cooldown():
    import asyncio

    adapter = LiveAgentLLM(key=SecretStr("synthetic"), base_url=GEMINI_OPENAI_BASE_URL,
                          model="gemini-3.5-flash-lite", request_interval_seconds=5)
    clock = FakeClock()
    entered, release = asyncio.Event(), asyncio.Event()
    adapter._clock = clock
    adapter._next_request_at = 5

    async def controlled_sleep(delay):
        clock.sleeps.append(delay)
        if len(clock.sleeps) == 1:
            entered.set()
            await release.wait()
        clock.now += delay

    adapter._sleep = controlled_sleep
    pending = asyncio.create_task(adapter._request_turn())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not adapter._request_lock.locked()
        async with adapter._request_lock:
            adapter._next_request_at = 10
        release.set()
        await pending
        assert clock.sleeps == [5, 5]
        assert clock.now == 10 and adapter._next_request_at == 15
    finally:
        release.set()
        await pending


def test_names_only_pacing_entry_preserves_default():
    assert settings(naive_request_interval_seconds="").naive_request_interval_seconds == 0


def test_google_list_wrapped_quota_error_is_parsed_safely():
    from app.adapters.unguarded import quota_metadata

    error = quota_error(429, retry_delay="32s", limit="15")
    error.body = [error.body]
    assert quota_metadata(error) == {"retry_after_seconds": 32.0, "quota_limit": 15}
