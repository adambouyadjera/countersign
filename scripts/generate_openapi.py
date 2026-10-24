"""Build the v1 OpenAPI contract from its Pydantic schemas, without network access.

Run with ``backend/.venv/bin/python scripts/generate_openapi.py`` from the
repository root. Endpoint metadata is transcribed from contracts/CONTRACT.md;
component schemas are generated from backend/app/contract_models.py.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
from typing import Any

import yaml
from pydantic import BaseModel, TypeAdapter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import contract_models as models  # noqa: E402


REF_TEMPLATE = "#/components/schemas/{model}"
DEFAULT_INSTRUCTION = (
    "Pay every invoice in this batch that is due within 30 days and matches an "
    "open purchase order. Pay only to accounts on file; anything else needs "
    "human approval."
)


class ContractDumper(yaml.SafeDumper):
    """Keep repeated parameters readable without YAML anchors and aliases."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def ref(name: str) -> dict[str, str]:
    return {"$ref": REF_TEMPLATE.format(model=name)}


def collect_schemas() -> dict[str, Any]:
    """Use the same definitions for OpenAPI validation and backend validation."""
    schemas: dict[str, Any] = {}

    def add(name: str, schema: dict[str, Any]) -> None:
        definitions = schema.pop("$defs", {})
        for definition_name, definition in definitions.items():
            previous = schemas.get(definition_name)
            if previous is not None and previous != definition:
                raise ValueError(f"Conflicting schema definitions: {definition_name}")
            schemas[definition_name] = definition
        previous = schemas.get(name)
        if previous is not None and previous != schema:
            raise ValueError(f"Conflicting schema definitions: {name}")
        schemas[name] = schema

    for name, model in inspect.getmembers(models, inspect.isclass):
        if (
            model.__module__ == models.__name__
            and issubclass(model, BaseModel)
            and name not in {"ContractModel", "EventBase"}
        ):
            add(name, model.model_json_schema(ref_template=REF_TEMPLATE))

    for name in ("Lane", "Mode", "CheckCode", "ReasonCode", "RunEvent"):
        alias = getattr(models, name)
        schema = TypeAdapter(alias).json_schema(ref_template=REF_TEMPLATE)
        schema.setdefault("title", name)
        add(name, schema)

    return dict(sorted(schemas.items()))


def json_response(schema: dict[str, Any], description: str = "Success") -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": schema}}}


def error_response(status: int) -> dict[str, str]:
    return {"$ref": f"#/components/responses/Error{status}"}


def parameter(name: str, location: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {
        "name": name,
        "in": location,
        "required": location == "path",
        "schema": schema,
        **kwargs,
    }


def operation(
    operation_id: str,
    summary: str,
    response_schema: str | dict[str, Any],
    *,
    body: str | None = None,
    parameters: list[dict[str, Any]] | None = None,
    errors: tuple[int, ...] = (),
    description: str | None = None,
) -> dict[str, Any]:
    schema = ref(response_schema) if isinstance(response_schema, str) else response_schema
    result: dict[str, Any] = {
        "operationId": operation_id,
        "summary": summary,
        "responses": {
            "200": json_response(schema),
            **{
                str(status): error_response(status)
                for status in sorted({422, 500, 503, *errors})
            },
        },
    }
    if body is not None:
        result["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": ref(body)}},
        }
    if parameters:
        result["parameters"] = parameters
    if description:
        result["description"] = description
    return result


def array(name: str) -> dict[str, Any]:
    return {"type": "array", "items": ref(name)}


def build_openapi() -> dict[str, Any]:
    string = {"type": "string"}
    run_id = parameter("run_id", "path", string)
    invoice_id = parameter("invoice_id", "path", string)
    document_id = parameter("document_id", "path", string)
    vendor_id = parameter("vendor_id", "path", string)
    job_id = parameter("job_id", "path", string)
    paths: dict[str, Any] = {
        "/api/health": {
            "get": operation(
                "get_health", "Get service health", "Health",
                description="Live service checks are cached for 30 seconds.",
            ),
        },
        "/api/demo/reset": {
            "post": operation(
                "reset_demo", "Reset the demo session", "ResetResponse",
                description="Clear runs, events, and approvals, then reseed idempotently.",
            ),
        },
        "/api/batches": {
            "post": operation(
                "create_batch", "Create a standard invoice batch", "Batch", body="BatchRequest",
                description="Create five invoices with one poisoned document. The poisoned invoice is not identified in the response.",
            ),
        },
        "/api/batches/{batch_id}": {
            "get": operation(
                "get_batch", "Get an invoice batch", "Batch",
                parameters=[parameter("batch_id", "path", string)], errors=(404,),
            ),
        },
        "/api/documents/{document_id}": {
            "get": operation(
                "get_document", "Get line-numbered document text", "DocumentText",
                parameters=[document_id], errors=(404,),
            ),
        },
        "/api/documents/{document_id}/pdf": {
            "get": operation(
                "get_document_pdf", "Get the original PDF", {},
                parameters=[document_id], errors=(404,),
            ),
        },
        "/api/attacks": {
            "post": operation(
                "create_attack", "Create an attack batch and inbox item", "Batch", body="AttackRequest",
                errors=(404, 429),
                description="Create an attack batch and an inbox item. Substitute the {{ATTACKER_ACCOUNT}} token in attack_text. The attack text must contain 1 to 600 characters. Rate limit: 10 requests per minute.",
            ),
        },
        "/api/runs": {
            "post": operation(
                "create_run", "Start the requested lanes in the background", "Run", body="RunRequest",
                errors=(404,),
                description=f"When instruction is omitted, use the trusted instruction: {DEFAULT_INSTRUCTION}",
            ),
        },
        "/api/runs/{run_id}": {
            "get": operation(
                "get_run", "Get a run", "Run", parameters=[run_id], errors=(404,),
                description="Includes lane tallies once the run is complete.",
            ),
        },
        "/api/runs/{run_id}/events": {
            "get": operation(
                "get_run_events", "Stream run events", "RunEvent",
                parameters=[
                    run_id,
                    parameter(
                        "Last-Event-ID", "header", {"type": "integer"},
                        description="Resume after this per-run event sequence number.",
                    ),
                ],
                errors=(404,),
                description="Each SSE message uses id = RunEvent.seq, event = RunEvent.type, and data = the JSON RunEvent. Sequence numbers increase by one across both lanes. Honor Last-Event-ID. The connection remains open after run.completed.",
            ),
        },
        "/api/runs/{run_id}/decisions": {
            "get": operation(
                "get_run_decisions", "Get Countersign policy decisions", array("PolicyDecision"),
                parameters=[run_id], errors=(404,),
            ),
        },
        "/api/runs/{run_id}/invoices/{invoice_id}/graph": {
            "get": operation(
                "get_invoice_graph", "Get Countersign invoice provenance", "ProvenanceGraph",
                parameters=[run_id, invoice_id], errors=(404,),
            ),
        },
        "/api/runs/{run_id}/planner_transcript": {
            "get": operation(
                "get_planner_transcript", "Get exactly what the planner received", "PlannerTranscript",
                parameters=[run_id], errors=(404,),
            ),
        },
        "/api/runs/{run_id}/invoices/{invoice_id}/agent_transcript": {
            "get": operation(
                "get_agent_transcript", "Get the unguarded agent transcript", "AgentTranscript",
                parameters=[run_id, invoice_id], errors=(404,),
            ),
        },
        "/api/approvals": {
            "get": operation(
                "list_approvals", "List approvals", array("Approval"),
                parameters=[parameter("status", "query", {"type": "string", "enum": ["pending", "resolved"]})],
            ),
        },
        "/api/approvals/{approval_id}": {
            "post": operation(
                "resolve_approval", "Resolve an approval", "Approval", body="ApprovalDecision",
                parameters=[parameter("approval_id", "path", string)], errors=(403, 404, 409),
                description="Return 422 for an incorrect confirm_last4, 409 if already resolved, or 403 with code sod_violation for a segregation-of-duties violation.",
            ),
        },
        "/api/balances": {
            "get": operation("get_balances", "Get bank balances", "Balances"),
        },
        "/api/redteam": {
            "post": operation(
                "create_redteam_job", "Start a red-team job", "RedTeamJob", body="RedTeamRequest",
                description="Run in the background with a dry-run bank.",
            ),
        },
        "/api/redteam/{job_id}": {
            "get": operation(
                "get_redteam_job", "Get a red-team job", "RedTeamJob", parameters=[job_id], errors=(404,),
                description="Poll every 2 seconds.",
            ),
        },
        "/api/audit/ask": {
            "post": operation(
                "ask_audit", "Ask the audit agent a question", "AuditAnswer", body="AuditAskRequest",
                errors=(404,), description="Return 503 when Elastic is offline.",
            ),
        },
        "/api/people": {
            "get": operation(
                "list_people", "List people and the AP agent", array("Person"),
                description="Includes the agent itself.",
            ),
        },
        "/api/vendors/{vendor_id}": {
            "get": operation(
                "get_vendor", "Get vendor account history and past payments", "VendorDetail",
                parameters=[vendor_id], errors=(404,),
            ),
        },
        "/api/vendors/{vendor_id}/bank_change": {
            "post": operation(
                "change_vendor_bank", "Change the vendor bank account", "VendorDetail", body="BankChangeRequest",
                parameters=[vendor_id], errors=(403, 404),
                description="Only vendor_admin actors may change the bank account. Otherwise return 403 with code sod_violation.",
            ),
        },
        "/api/ledger/entries": {
            "get": operation(
                "list_journal_entries", "List journal entries", array("JournalEntry"),
                parameters=[parameter("run_id", "query", string), parameter("invoice_id", "query", string)],
            ),
        },
        "/api/ledger/trial_balance": {
            "get": operation("get_trial_balance", "Get the trial balance", "TrialBalance"),
        },
        "/api/reconciliation": {
            "get": operation(
                "get_reconciliation", "Reconcile the Nessie company account and ledger cash", "Reconciliation",
            ),
        },
        "/api/inbox": {
            "post": operation(
                "create_inbox_item", "Add a document to the inbox", "InboxItem", body="InboxRequest", errors=(404,),
            ),
            "get": operation("list_inbox_items", "List inbox items", array("InboxItem")),
        },
        "/api/autopilot": {
            "get": operation("get_autopilot", "Get autopilot settings", "Autopilot"),
            "post": operation(
                "set_autopilot", "Set autopilot settings", "Autopilot", body="AutopilotRequest",
            ),
        },
        "/api/eval": {
            "post": operation(
                "create_eval_job", "Start a planted-errors evaluation", "EvalJob", body="EvalRequest",
                description="Use a dry-run bank.",
            ),
        },
        "/api/eval/{job_id}": {
            "get": operation(
                "get_eval_job", "Get an evaluation job", "EvalJob", parameters=[job_id], errors=(404,),
                description="Poll every 2 seconds.",
            ),
        },
    }

    paths["/api/documents/{document_id}/pdf"]["get"]["responses"]["200"] = {
        "description": "Original PDF file",
        "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
    }
    paths["/api/runs/{run_id}/events"]["get"]["responses"]["200"] = {
        "description": "Server-sent events carrying JSON RunEvent objects; remains open after run.completed",
        "content": {"text/event-stream": {"schema": ref("RunEvent")}},
    }

    error_codes = {
        403: "sod_violation",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        500: "internal",
        503: "service_unavailable",
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Countersign API",
            "version": "1.0.0",
            "description": "Countersign provenance firewall API for Fogline Festival, including the Hyphenate track additions. JSON uses snake_case. Timestamps are ISO 8601 UTC. Monetary amounts are integer cents; currency fields use USD. External service IDs in fixtures start with example-.",
        },
        "paths": paths,
        "components": {
            "schemas": collect_schemas(),
            "responses": {
                f"Error{status}": json_response(
                    {
                        "allOf": [
                            ref("ErrorResponse"),
                            {
                                "type": "object",
                                "properties": {
                                    "error": {
                                        "type": "object",
                                        "properties": {"code": {"const": code}},
                                    },
                                },
                            },
                        ],
                    },
                    f"{status}: {code}",
                )
                for status, code in error_codes.items()
            },
        },
    }


def main() -> None:
    target = ROOT / "contracts" / "openapi.yaml"
    document = build_openapi()
    target.write_text(
        yaml.dump(document, Dumper=ContractDumper, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
