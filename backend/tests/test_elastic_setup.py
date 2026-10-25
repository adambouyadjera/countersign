"""Provision real Agent Builder definitions without any live service call."""

from copy import deepcopy
import json

import httpx
import pytest

from app.audit.definitions import AGENT_INSTRUCTIONS, INDEX_NAME, TOOL_IDS, agent_definition, tool_definitions
from app.config import Settings
from scripts.setup_elastic_agent import ElasticSetupError, setup_agent


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)


def settings(**changes):
    return Settings(_env_file=None, **{
        "elastic_mode": "live", "elastic_url": "https://elasticsearch.invalid",
        "kibana_url": "https://kibana.invalid/s/audit", "elastic_api_key": "synthetic-secret-value",
        "elastic_agent_id": "countersign-test-analyst", **changes,
    })


class Kibana:
    """A stateful HTTP fake that records the actual setup request shapes."""

    def __init__(self):
        self.tools = {}
        self.agents = {}
        self.calls = []

    def handle(self, request):
        self.calls.append(request)
        path = request.url.path
        assert path.startswith("/s/audit/api/agent_builder/")
        assert request.headers["authorization"] == "ApiKey synthetic-secret-value"
        assert request.headers["kbn-xsrf"] == "true"
        assert "synthetic-secret-value" not in str(request.url)
        resource = path.removeprefix("/s/audit/api/agent_builder/")
        kind, _, identity = resource.partition("/")
        values = self.tools if kind == "tools" else self.agents
        if request.method == "GET":
            return httpx.Response(200, json=deepcopy(values[identity])) if identity in values else httpx.Response(404)
        body = json.loads(request.content)
        if request.method == "POST":
            assert not identity
            if body["id"] in values:
                return httpx.Response(409)
            values[body["id"]] = {**deepcopy(body), "readonly": False} if kind == "tools" else deepcopy(body)
            return httpx.Response(200, json=values[body["id"]])
        if request.method == "PUT":
            assert "id" not in body and "type" not in body
            assert identity in values
            values[identity].update(deepcopy(body))
            return httpx.Response(200, json=values[identity])
        raise AssertionError("Setup must not delete a resource or call another operation")

    @property
    def transport(self):
        return httpx.MockTransport(self.handle)


def test_tools_are_fixed_index_parameterized_and_semantically_scoped():
    definitions = tool_definitions()
    assert tuple(tool["id"] for tool in definitions) == TOOL_IDS
    assert len(TOOL_IDS) == 4
    for definition in definitions:
        assert definition["type"] == "esql"
        assert definition["configuration"]["query"].startswith("FROM " + INDEX_NAME + " |")
        assert "?run_id" in definition["configuration"]["query"]
        assert definition["configuration"]["params"]["run_id"]["type"] == "string"
        assert "LIMIT 1000" in definition["configuration"]["query"]
    held, off_file, summary, attacks = [tool["configuration"]["query"] for tool in definitions]
    assert 'type == "policy.decision"' in held and 'outcome == "HOLD"' in held
    assert "MV_EXPAND" not in held and "payee_document_derived" in held
    assert 'type == "payment.executed"' in off_file and "payee_on_file == false" in off_file
    assert 'SUM(CASE(type == "payment.executed", amount_cents, 0))' in summary
    assert "BY run_id, lane, type, outcome, mode" in summary
    assert "BY batch_id" in attacks and "VALUES(mode)" in attacks
    definitions[0]["configuration"]["params"]["run_id"]["description"] = "mutated"
    assert definitions[1]["configuration"]["params"]["run_id"]["description"] != "mutated"
    assert tool_definitions()[0]["configuration"]["params"]["run_id"]["description"] != "mutated"


def test_agent_instructions_require_real_tools_citations_and_mode_honesty():
    definition = agent_definition("countersign-example")
    assert definition["configuration"]["tools"] == [{"tool_ids": list(TOOL_IDS)}]
    for phrase in ("only", "run IDs", "invoice IDs", "untrusted", "offline", "dry_run", "settlement"):
        assert phrase in AGENT_INSTRUCTIONS
    assert "connector_ids" not in definition["configuration"]
    assert "access_control" not in definition  # No implicit project access expansion.


@pytest.mark.asyncio
async def test_first_setup_creates_and_get_verifies_every_resource_then_rerun_is_read_only():
    kibana = Kibana()
    first = await setup_agent(settings(), transport=kibana.transport)
    assert len(first["operations"]) == 5
    assert {operation["action"] for operation in first["operations"]} == {"created"}
    assert len(kibana.calls) == 15
    assert set(kibana.tools) == set(TOOL_IDS)
    assert set(kibana.agents) == {"countersign-test-analyst"}
    kibana.calls.clear()
    second = await setup_agent(settings(), transport=kibana.transport)
    assert {operation["action"] for operation in second["operations"]} == {"unchanged"}
    assert len(kibana.calls) == 5 and all(call.method == "GET" for call in kibana.calls)
    assert "synthetic-secret-value" not in json.dumps(first)


@pytest.mark.asyncio
async def test_update_preserves_unrelated_agent_settings_but_replaces_the_tool_allowlist():
    kibana = Kibana()
    await setup_agent(settings(), transport=kibana.transport)
    current = kibana.agents["countersign-test-analyst"]
    current["configuration"].update({"connector_ids": ["existing-connector"],
        "skill_ids": ["existing-skill"], "instructions": "old instructions",
        "tools": [{"tool_ids": ["platform.core.execute_esql", "unrelated-tool"]}]})
    current["avatar_color"] = "#112233"
    current["labels"] = ["finance-team"]
    current["name"] = "Existing finance analyst name"
    kibana.tools[TOOL_IDS[0]]["configuration"]["query"] = "FROM wrong-index"
    kibana.calls.clear()
    report = await setup_agent(settings(), transport=kibana.transport)
    assert sum(operation["action"] == "updated" for operation in report["operations"]) == 2
    updated = kibana.agents["countersign-test-analyst"]
    assert updated["configuration"]["connector_ids"] == ["existing-connector"]
    assert updated["configuration"]["skill_ids"] == ["existing-skill"]
    assert updated["configuration"]["instructions"] == AGENT_INSTRUCTIONS
    assert updated["configuration"]["tools"] == [{"tool_ids": list(TOOL_IDS)}]
    assert updated["avatar_color"] == "#112233"
    assert updated["labels"] == ["finance-team"]
    assert updated["name"] == "Existing finance analyst name"
    writes = [json.loads(call.content) for call in kibana.calls if call.method == "PUT"]
    assert all("id" not in body and "type" not in body for body in writes)
    assert all(set(json.loads(call.content)) == {"description", "configuration"}
               for call in kibana.calls if call.method == "PUT" and "/agents/" in call.url.path)
    assert all(call.method != "DELETE" for call in kibana.calls)


@pytest.mark.asyncio
async def test_concurrent_creator_conflict_is_verified_without_duplicate_mutation():
    kibana = Kibana()
    first_get = True

    def race(request):
        nonlocal first_get
        if request.method == "POST" and request.url.path.endswith("/tools") and first_get:
            first_get = False
            body = json.loads(request.content)
            kibana.tools[body["id"]] = {**body, "readonly": False}
            return httpx.Response(409)
        return kibana.handle(request)

    report = await setup_agent(settings(), transport=httpx.MockTransport(race))
    assert report["operations"][0]["action"] == "unchanged"
    assert len(kibana.tools) == 4
    assert all(call.method != "PUT" for call in kibana.calls)


@pytest.mark.asyncio
async def test_incompatible_existing_tool_is_not_overwritten():
    kibana = Kibana()
    kibana.tools[TOOL_IDS[0]] = {"id": TOOL_IDS[0], "type": "mcp", "configuration": {}}
    with pytest.raises(ElasticSetupError, match="incompatible"):
        await setup_agent(settings(), transport=kibana.transport)
    assert all(call.method == "GET" for call in kibana.calls)
    assert kibana.tools[TOOL_IDS[0]]["type"] == "mcp"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(403, json={"error": "synthetic-secret-value"}),
    httpx.Response(200, text="<html>synthetic-secret-value</html>"),
    httpx.Response(200, json=["synthetic-secret-value"]),
])
async def test_provider_error_bodies_are_never_reflected(response):
    with pytest.raises(ElasticSetupError) as caught:
        await setup_agent(settings(), transport=httpx.MockTransport(lambda _: response))
    assert "synthetic-secret-value" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"elastic_mode": "offline"}, {"elastic_api_key": ""},
    {"elastic_api_key": "a-secret\nheader-injection"},
    {"kibana_url": "https://user:secret@kibana.invalid"},
    {"kibana_url": "https://kibana.invalid?key=secret"},
    {"kibana_url": "https://kibana.invalid/app/agent_builder"},
    {"kibana_url": "http://kibana.invalid"}, {"elastic_agent_id": "../another-agent"},
])
async def test_invalid_configuration_fails_before_network(changes):
    def forbidden(request):
        raise AssertionError("Invalid setup configuration must not send a request")

    with pytest.raises(ElasticSetupError):
        await setup_agent(settings(**changes), transport=httpx.MockTransport(forbidden))
