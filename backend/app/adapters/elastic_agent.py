"""A direct client for Elastic Agent Builder, with no local answer generator."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.contract_models import AuditAnswer, AuditAskRequest, AuditStep


class AuditUnavailableError(RuntimeError):
    """A safe transport/schema failure; never retain upstream response bodies."""

    def __init__(self, message="Elastic Agent Builder audit is unavailable.", *, http_status=None):
        self.http_status = http_status
        super().__init__(message)


def _tool_step(step: dict) -> AuditStep:
    tool_id = step.get("tool_id")
    if not isinstance(tool_id, str) or not tool_id.strip():
        raise AuditUnavailableError("Elastic Agent Builder returned an invalid tool step.")
    query, fallback_query = None, None
    row_count = None
    results = step.get("results", [])
    if not isinstance(results, list):
        raise AuditUnavailableError("Elastic Agent Builder returned invalid tool results.")
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            continue
        data = result["data"]
        if result.get("type") == "query":
            candidate = data.get("esql")
            if isinstance(candidate, str) and candidate.strip():
                fallback_query = fallback_query or candidate
        elif result.get("type") in {"esql_results", "tabular_data"}:
            candidate = data.get("query")
            if isinstance(candidate, str) and candidate.strip():
                if query is not None and query != candidate:
                    raise AuditUnavailableError("Elastic Agent Builder returned ambiguous query evidence for one tool call.")
                query = query or candidate
            # Older tabular_data responses use rows rather than values. A
            # missing table is unknown, not a fabricated zero-row result.
            rows = data.get("values", data.get("rows"))
            if isinstance(rows, list) and all(isinstance(row, (list, dict)) for row in rows):
                row_count = (row_count or 0) + len(rows)
    values = {"tool_id": tool_id}
    if query is not None or fallback_query is not None:
        values["esql"] = query or fallback_query
    if row_count is not None:
        values["row_count"] = row_count
    return AuditStep.model_validate(values)


def parse_converse(body, *, agent_id: str) -> AuditAnswer:
    """Project the real final message and tool observations onto the contract."""
    if not isinstance(body, dict) or not isinstance(body.get("response"), dict):
        raise AuditUnavailableError("Elastic Agent Builder returned an invalid answer.")
    message = body["response"].get("message")
    if not isinstance(message, str) or not message.strip():
        raise AuditUnavailableError("Elastic Agent Builder returned no final answer.")
    if body.get("agent_id", agent_id) != agent_id:
        raise AuditUnavailableError("Elastic Agent Builder returned a different agent identity.")
    raw_steps = body.get("steps")
    if not isinstance(raw_steps, list):
        raise AuditUnavailableError("Elastic Agent Builder returned no execution steps.")
    steps = [_tool_step(step) for step in raw_steps
             if isinstance(step, dict) and step.get("type") == "tool_call"]
    values = {"answer": message, "agent_id": agent_id, "steps": steps}
    conversation_id = body.get("conversation_id")
    if isinstance(conversation_id, str) and conversation_id.strip():
        values["conversation_id"] = conversation_id
    return AuditAnswer.model_validate(values)


class AgentBuilderClient:
    def __init__(self, *, key: SecretStr, kibana_url: str, agent_id: str,
                 transport: httpx.AsyncBaseTransport | None = None, timeout=180):
        self._key = key
        self._base_url = kibana_url.rstrip("/")
        self.agent_id = agent_id
        self._transport = transport
        self._timeout = timeout

    def _url(self):
        try:
            parsed = urlsplit(self._base_url)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and parsed.username is None and parsed.password is None
                     and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid or not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise AuditUnavailableError("Elastic Agent Builder endpoint or agent is not configured.")
        return self._base_url + "/api/agent_builder/converse"

    async def ask(self, request: AuditAskRequest) -> AuditAnswer:
        if not self._key.get_secret_value():
            raise AuditUnavailableError("ELASTIC_API_KEY is not configured for Agent Builder.")
        url = self._url()
        context = {"run_id": request.run_id} if request.run_id is not None else {}
        payload = {"agent_id": self.agent_id,
                   "input": "Question: " + request.question + "\nAudit context: "
                            + json.dumps(context, ensure_ascii=True, separators=(",", ":"))}
        headers = {"Authorization": "ApiKey " + self._key.get_secret_value(),
                   "kbn-xsrf": "true", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout,
                                        follow_redirects=False) as client:
                response = await client.post(url, headers=headers, json=payload)
        except Exception:
            # Provider exception text can contain URLs, headers or document
            # data. Neither logs nor callers receive it.
            raise AuditUnavailableError("Elastic Agent Builder converse request failed.") from None
        if not response.is_success:
            raise AuditUnavailableError("Elastic Agent Builder converse request failed.",
                                        http_status=response.status_code)
        if len(response.content) > 20_000_000:
            raise AuditUnavailableError("Elastic Agent Builder response exceeded the audit limit.")
        try:
            body = response.json()
        except ValueError:
            raise AuditUnavailableError("Elastic Agent Builder returned invalid JSON.") from None
        return parse_converse(body, agent_id=self.agent_id)
