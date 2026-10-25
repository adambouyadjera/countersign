"""Idempotently provision genuine Kibana Agent Builder audit tools and agent."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit

import httpx


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.audit.definitions import INDEX_NAME, TOOL_IDS, agent_definition, tool_definitions
from app.config import Settings


class ElasticSetupError(RuntimeError):
    """A credential-safe setup failure; never retain an upstream body."""


def _configuration(settings):
    if settings.elastic_mode != "live":
        raise ElasticSetupError("ELASTIC_MODE must be live for Agent Builder setup.")
    if not settings.elastic_api_key.get_secret_value():
        raise ElasticSetupError("ELASTIC_API_KEY is not configured.")
    if not re.fullmatch(r"[A-Za-z0-9_+/=-]+", settings.elastic_api_key.get_secret_value()):
        raise ElasticSetupError("ELASTIC_API_KEY must be an encoded API-key value without whitespace.")
    parsed = urlsplit(settings.kibana_url)
    path = parsed.path.rstrip("/")
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or (path and not re.fullmatch(r"/s/[A-Za-z0-9_-]+", path))):
        raise ElasticSetupError("KIBANA_URL must be a Kibana root URL, optionally followed by /s/<space>.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", settings.elastic_agent_id):
        raise ElasticSetupError("ELASTIC_AGENT_ID must be a valid Agent Builder identifier.")
    return settings.kibana_url.rstrip("/")


async def _request(client, method, path, *, payload=None, missing=False, conflict=False):
    try:
        response = await client.request(method, path, json=payload)
    except httpx.HTTPError:
        raise ElasticSetupError("Agent Builder setup request failed before a valid response.") from None
    if missing and response.status_code == 404:
        return None
    if conflict and response.status_code == 409:
        return False
    if not response.is_success:
        raise ElasticSetupError(f"Agent Builder setup returned HTTP {response.status_code}.")
    try:
        value = response.json()
    except ValueError:
        raise ElasticSetupError("Agent Builder setup returned invalid JSON.") from None
    if not isinstance(value, dict):
        raise ElasticSetupError("Agent Builder setup returned an unexpected response shape.")
    return value


def _tool_matches(existing, desired):
    return all(existing.get(name) == desired[name] for name in (
        "id", "type", "description", "configuration", "tags"))


def _agent_update(existing, desired):
    # Agent tool selection is an allowlist: retain unrelated settings, but
    # never union a previous broad tool selection with the curated audit set.
    configuration = deepcopy(existing.get("configuration", {}))
    if not isinstance(configuration, dict):
        raise ElasticSetupError("The existing agent has an invalid configuration.")
    configuration.update(deepcopy(desired["configuration"]))
    return {"description": desired["description"], "configuration": configuration}


async def setup_agent(settings, *, transport=None):
    """Create or update only the named resources; never delete unrelated data."""
    base = _configuration(settings)
    headers = {"Authorization": "ApiKey " + settings.elastic_api_key.get_secret_value(),
               "kbn-xsrf": "true", "Content-Type": "application/json"}
    report = {"agent_id": settings.elastic_agent_id, "index": INDEX_NAME,
              "tool_ids": list(TOOL_IDS), "operations": []}
    async with httpx.AsyncClient(base_url=base + "/", headers=headers, transport=transport,
                                 timeout=30, follow_redirects=False) as client:
        for desired in tool_definitions():
            # Relative paths preserve a configured /s/<space> prefix.
            path = "api/agent_builder/tools/" + quote(desired["id"], safe="")
            existing = await _request(client, "GET", path, missing=True)
            action = "unchanged"
            if existing is None:
                created = await _request(client, "POST", "api/agent_builder/tools",
                                         payload=desired, conflict=True)
                action = "created"
                if created is False:
                    existing = await _request(client, "GET", path)
                    action = "unchanged"
            if existing is not None and not _tool_matches(existing, desired):
                if existing.get("type") != "esql" or existing.get("readonly") is True:
                    raise ElasticSetupError("The requested tool ID belongs to an incompatible or immutable tool.")
                mutable = {name: deepcopy(desired[name]) for name in ("description", "tags", "configuration")}
                await _request(client, "PUT", path, payload=mutable)
                action = "updated"
            verified = existing if action == "unchanged" else await _request(client, "GET", path)
            if not _tool_matches(verified, desired):
                raise ElasticSetupError("Agent Builder tool verification did not match the requested definition.")
            report["operations"].append({"resource": "tool", "id": desired["id"], "action": action})

        desired = agent_definition(settings.elastic_agent_id)
        path = "api/agent_builder/agents/" + quote(settings.elastic_agent_id, safe="")
        existing = await _request(client, "GET", path, missing=True)
        action = "unchanged"
        if existing is None:
            created = await _request(client, "POST", "api/agent_builder/agents",
                                     payload=desired, conflict=True)
            action = "created"
            if created is False:
                existing = await _request(client, "GET", path)
                action = "unchanged"
        if existing is not None:
            update = _agent_update(existing, desired)
            if any(existing.get(name) != value for name, value in update.items()):
                await _request(client, "PUT", path, payload=update)
                action = "updated"
        verified = existing if action == "unchanged" else await _request(client, "GET", path)
        if (verified is None or verified.get("id") != settings.elastic_agent_id
                or any(verified.get(name) != value for name, value in _agent_update(verified, desired).items())):
            raise ElasticSetupError("Agent Builder agent verification did not match the requested definition.")
        report["operations"].append({"resource": "agent", "id": settings.elastic_agent_id, "action": action})
    return report


async def _run(args):
    settings = Settings()
    _configuration(settings)
    # The setup command has one live service boundary. No bank or application
    # LLM client is probed while creating the Elasticsearch index.
    from app.adapters.factory import build_adapters
    from app.store import Store

    selected = settings.model_copy(update={"nessie_mode": "offline", "elastic_mode": "live",
        "extractor_mode": "offline", "planner_mode": "offline", "agent_mode": "offline",
        "mutator_mode": "offline"})
    store = Store()
    store.initialize()
    sink = None
    try:
        sink = build_adapters(selected, store).audit
        await sink.ensure_index()
    finally:
        if sink is not None:
            await sink.aclose()
        store.close()
    report = await setup_agent(settings)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save safe setup metadata (never credentials).")
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args))
    except ElasticSetupError as error:
        print(json.dumps({"success": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        # Providers may echo headers or credentials in arbitrary exception
        # messages. Unexpected failures expose only their classification.
        print(json.dumps({"success": False, "error": "Elastic setup failed.",
                          "error_type": type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
