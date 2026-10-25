"""Converse is genuinely delegated to Kibana; no local LLM answers questions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
import httpx
from pydantic import SecretStr
import pytest
import respx

from app.adapters.elastic_agent import AgentBuilderClient, AuditUnavailableError, parse_converse
from app.api.audit import router
from app.contract_models import AuditAnswer, AuditAskRequest


URL = "https://kibana.invalid/s/audit/api/agent_builder/converse"


def response(**changes):
    return {"conversation_id": "conversation-recorded-by-elastic",
            "response": {"message": "Invoice INV-003 was held in run-real."},
            "steps": [{"type": "tool_call", "tool_id": "held_payments_by_reason",
                       "params": {"run_id": "run-real"}, "results": [{
                           "type": "esql_results", "data": {
                               "query": 'FROM countersign-events | WHERE run_id == ?run_id',
                               "columns": [{"name": "invoice_id", "type": "keyword"}],
                               "values": [["INV-003"]],
                           },
                       }]}], **changes}


def client(**changes):
    return AgentBuilderClient(key=SecretStr("synthetic-elastic-key"),
        kibana_url=changes.pop("kibana_url", "https://kibana.invalid/s/audit/"),
        agent_id="countersign-audit-analyst", **changes)


@pytest.mark.asyncio
@respx.mock
async def test_real_converse_shape_headers_and_json_escaped_context_are_sent_to_kibana_only():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=response()))
    identity = 'run-"quoted"\n| FROM other-index'
    answer = await client().ask(AuditAskRequest(question="Which invoice was held?", run_id=identity))
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == "ApiKey synthetic-elastic-key"
    assert request.headers["kbn-xsrf"] == "true"
    assert request.headers["Content-Type"] == "application/json"
    sent = json.loads(request.content)
    assert set(sent) == {"agent_id", "input"}
    assert sent["agent_id"] == answer.agent_id == "countersign-audit-analyst"
    question, context = sent["input"].split("\nAudit context: ", 1)
    assert question == "Question: Which invoice was held?"
    assert json.loads(context) == {"run_id": identity}
    assert answer.answer == "Invoice INV-003 was held in run-real."
    assert answer.conversation_id == "conversation-recorded-by-elastic"
    assert answer.steps[0].row_count == 1
    assert answer.steps[0].esql.endswith("?run_id")


def test_parser_keeps_repeated_calls_and_ignores_reasoning_as_answer():
    steps = response()["steps"]
    steps.insert(0, {"type": "reasoning", "reasoning": "This is never the answer."})
    steps.append({"type": "tool_call", "tool_id": "held_payments_by_reason", "results": [{
        "type": "esql_results", "data": {"query": "FROM countersign-events | LIMIT 0", "values": []},
    }]})
    answer = parse_converse(response(steps=steps), agent_id="agent")
    assert answer.answer == response()["response"]["message"]
    assert [step.tool_id for step in answer.steps] == ["held_payments_by_reason"] * 2
    assert [step.row_count for step in answer.steps] == [1, 0]
    assert answer.steps[1].esql == "FROM countersign-events | LIMIT 0"


@pytest.mark.parametrize("kind,rows_key", [("esql_results", "values"), ("tabular_data", "values"), ("tabular_data", "rows")])
def test_parser_reads_returned_tables_and_query_results(kind, rows_key):
    body = response(steps=[{"type": "tool_call", "tool_id": "platform.core.execute_esql", "results": [
        {"type": "query", "data": {"esql": "FROM countersign-events | LIMIT 2"}},
        {"type": kind, "data": {"columns": [], rows_key: [["a"], ["b"]]}},
    ]}])
    answer = parse_converse(body, agent_id="agent")
    assert answer.steps[0].esql == "FROM countersign-events | LIMIT 2"
    assert answer.steps[0].row_count == 2


@pytest.mark.parametrize("results", [None, [], [{"type": "esql_results", "data": {"query": "FROM countersign-events"}}]])
def test_missing_result_rows_never_become_a_fabricated_zero(results):
    step = {"type": "tool_call", "tool_id": "tool"}
    if results is not None:
        step["results"] = results
    parsed = parse_converse(response(steps=[step]), agent_id="agent").steps[0]
    assert parsed.row_count is None
    assert "row_count" not in parsed.model_dump(mode="json")


def test_one_step_cannot_misattribute_rows_from_different_queries():
    body = response(steps=[{"type": "tool_call", "tool_id": "tool", "results": [
        {"type": "esql_results", "data": {"query": "FROM first", "values": [[1]]}},
        {"type": "esql_results", "data": {"query": "FROM second", "values": [[2]]}},
    ]}])
    with pytest.raises(AuditUnavailableError, match="ambiguous query evidence"):
        parse_converse(body, agent_id="agent")


@pytest.mark.parametrize("body", [
    {}, {"reasoning_content": "Never use this", "steps": []},
    response(response={"message": ""}), response(response={"message": None}),
    response(steps={}), response(agent_id="different-agent"),
    response(steps=[{"type": "tool_call", "tool_id": ""}]),
])
def test_invalid_answers_fail_without_using_reasoning_or_inventing_steps(body):
    with pytest.raises(AuditUnavailableError):
        parse_converse(body, agent_id="agent")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
@respx.mock
async def test_http_failures_are_safe_and_never_fall_back(status):
    route = respx.post(URL).mock(return_value=httpx.Response(status, text="private-provider-body synthetic-secret"))
    with pytest.raises(AuditUnavailableError) as caught:
        await client().ask(AuditAskRequest(question="Question"))
    assert caught.value.http_status == status
    assert "private-provider-body" not in str(caught.value)
    assert "synthetic-secret" not in str(caught.value)
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_timeout_and_invalid_json_are_safely_reported():
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("private-url-with-credentials"))
    with pytest.raises(AuditUnavailableError, match="converse request failed"):
        await client().ask(AuditAskRequest(question="Question"))
    route.mock(return_value=httpx.Response(200, text="not-json"), side_effect=None)
    with pytest.raises(AuditUnavailableError, match="invalid JSON"):
        await client().ask(AuditAskRequest(question="Question"))


@pytest.mark.asyncio
@pytest.mark.parametrize("base", ["", "https://user:synthetic-secret@kibana.invalid", "https://kibana.invalid?key=synthetic-secret", "file:///tmp/project"])
async def test_invalid_endpoint_fails_before_any_network(base):
    def no_calls(request):
        raise AssertionError("Invalid endpoint must not make a request")
    with pytest.raises(AuditUnavailableError) as caught:
        await client(kibana_url=base, transport=httpx.MockTransport(no_calls)).ask(AuditAskRequest(question="Question"))
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_missing_key_fails_before_any_network():
    calls = []

    def no_calls(request):
        calls.append(request)
        raise AssertionError("Missing key must not make a request")

    adapter = AgentBuilderClient(key=SecretStr(""), kibana_url="https://kibana.invalid",
        agent_id="agent", transport=httpx.MockTransport(no_calls))
    with pytest.raises(AuditUnavailableError, match="ELASTIC_API_KEY is not configured"):
        await adapter.ask(AuditAskRequest(question="Question"))
    assert calls == []


async def post(sink, payload):
    app = FastAPI()
    app.state.adapters = SimpleNamespace(audit=sink)
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        return await http.post("/api/audit/ask", json=payload)


@pytest.mark.asyncio
async def test_offline_route_returns_clear_contract_503_without_asking_anything():
    sink = SimpleNamespace(mode="offline", ask=AsyncMock())
    result = await post(sink, {"question": "Why was this held?"})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "service_unavailable"
    assert "Elastic audit is offline" in result.json()["error"]["message"]
    sink.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_route_delegates_historical_run_context_and_masks_display_canaries():
    answer = AuditAnswer(answer="Real answer CS-CANARY-AB12", agent_id="agent", steps=[])
    sink = SimpleNamespace(mode="live", ask=AsyncMock(return_value=answer))
    result = await post(sink, {"question": "What happened?", "run_id": "historical-not-in-sqlite"})
    assert result.status_code == 200
    assert "CS-CANARY" not in result.text
    sink.ask.assert_awaited_once_with(AuditAskRequest(question="What happened?", run_id="historical-not-in-sqlite"))
    assert answer.answer.endswith("CS-CANARY-AB12")  # Display projection does not rewrite evidence.


@pytest.mark.asyncio
async def test_route_failure_never_leaks_provider_response_and_empty_question_is_422():
    sink = SimpleNamespace(mode="live", ask=AsyncMock(side_effect=RuntimeError("private credential body")))
    result = await post(sink, {"question": "What happened?"})
    assert result.status_code == 503 and "private credential" not in result.text
    empty = await post(sink, {"question": "   "})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "validation_error"
    assert sink.ask.await_count == 1
