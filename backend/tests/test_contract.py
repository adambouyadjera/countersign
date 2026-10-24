"""Offline checks for the shared API contract and its synthetic replay data."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import runpy

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import TypeAdapter, ValidationError
import pytest
import yaml

from app import contract_models as models


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts" / "fixtures"
SPEC = ROOT / "contracts" / "openapi.yaml"
EVENTS_FILE = "run_both.events.jsonl"
FIXTURE_MODELS = {
    "health.json": models.Health,
    "batch.json": models.Batch,
    "document_INV-003.json": models.DocumentText,
    "graph_INV-003.json": models.ProvenanceGraph,
    "planner_transcript.json": models.PlannerTranscript,
    "agent_transcript_INV-003.json": models.AgentTranscript,
    "balances.json": models.Balances,
    "approvals.json": list[models.Approval],
    "redteam_job.json": models.RedTeamJob,
    "audit_answer.json": models.AuditAnswer,
    "people.json": list[models.Person],
    "vendor_detail.json": models.VendorDetail,
    "trial_balance.json": models.TrialBalance,
    "reconciliation.json": models.Reconciliation,
    "eval_job.json": models.EvalJob,
    "autopilot.json": models.Autopilot,
    "run.json": models.Run,
    "policy_decisions.json": list[models.PolicyDecision],
    "ledger_entries.json": list[models.JournalEntry],
    "inbox.json": list[models.InboxItem],
    "attack.json": models.Attack,
    "approval_decision.json": models.ApprovalDecision,
    "run_request.json": models.RunRequest,
    "redteam_request.json": models.RedTeamRequest,
    "audit_question.json": models.AuditAskRequest,
    "autopilot_request.json": models.AutopilotRequest,
    "eval_request.json": models.EvalRequest,
    "bank_change_request.json": models.BankChangeRequest,
    "batch_request.json": models.BatchRequest,
    "inbox_request.json": models.InboxRequest,
    "demo_reset.json": models.ResetResponse,
    "error.json": models.ErrorResponse,
}


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(SPEC.read_text())


@pytest.fixture(scope="module")
def events():
    lines = (FIXTURES / EVENTS_FILE).read_text().splitlines()
    assert lines and all(lines), "JSONL must contain one event on each line"
    return [json.loads(line) for line in lines]


def schema_validator(spec, schema):
    return Draft202012Validator(
        {**schema, "components": spec["components"]},
        format_checker=FormatChecker(),
    )


def test_openapi_is_valid(spec):
    from openapi_spec_validator import validate_spec

    assert spec["openapi"] == "3.1.0"
    validate_spec(spec)


def number_type_contract_request(spec):
    """Apply only the user's requested additive type change to read-only v1.

    The contract owner must add ``number`` at the two enum locations and the
    optional provider identity and mode metadata requested for unguarded execution,
    plus the separately selectable bank-change-letter batch.
    All other endpoint and component schemas remain subject to exact parity.
    """
    expected = deepcopy(spec)
    schemas = expected["components"]["schemas"]
    schemas["ServicesHealth"]["properties"]["mutator"] = {"$ref": "#/components/schemas/ServiceHealth"}
    for name in ("ExtractedField", "Handle"):
        enum = expected["components"]["schemas"][name]["properties"]["type"]["enum"]
        assert enum == ["money", "date", "string", "account_number"]
        enum.append("number")
    for event in expected["components"]["schemas"]["RunEvent"]["oneOf"]:
        name = event["$ref"].rsplit("/", 1)[-1]
        properties = expected["components"]["schemas"][name]["properties"]
        properties["provider"] = {"title": "Provider", "type": "string"}
        properties["model"] = {"title": "Model", "type": "string"}
        properties["unguarded_mode"] = {
            "enum": ["ap", "ap_with_vendor_updates"], "title": "Unguarded Mode", "type": "string",
        }
    expected["components"]["schemas"]["AgentTranscript"]["properties"]["provider"] = {
        "title": "Provider", "type": "string",
    }
    for name in ("RunRequest", "Run", "AgentTranscript"):
        schemas[name]["properties"]["unguarded_mode"] = {
            "enum": ["ap", "ap_with_vendor_updates"], "title": "Unguarded Mode", "type": "string",
        }
    assert schemas["BatchRequest"]["properties"]["kind"] == {
        "const": "standard", "title": "Kind", "type": "string",
    }
    schemas["BatchRequest"]["properties"]["kind"] = {
        "enum": ["standard", "bank_change"], "title": "Kind", "type": "string",
    }
    assert schemas["Batch"]["properties"]["kind"]["enum"] == ["standard", "attack"]
    schemas["Batch"]["properties"]["kind"]["enum"].append("bank_change")
    schemas["Batch"]["properties"]["supporting_documents"] = {
        "items": {"$ref": "#/components/schemas/SupportingDocument"},
        "title": "Supporting Documents", "type": "array",
    }
    schemas["DocumentText"]["properties"]["document_type"] = {
        "enum": ["invoice", "vendor_bank_change_letter"], "title": "Document Type", "type": "string",
    }
    schemas["SupportingDocument"] = {
        "additionalProperties": False,
        "properties": {
            "document_id": {"title": "Document Id", "type": "string"},
            "invoice_id": {"title": "Invoice Id", "type": "string"},
            "vendor_id": {"title": "Vendor Id", "type": "string"},
            "document_type": {"const": "vendor_bank_change_letter", "title": "Document Type", "type": "string"},
        },
        "required": ["document_id", "invoice_id", "vendor_id", "document_type"],
        "title": "SupportingDocument", "type": "object",
    }
    return expected


def test_openapi_matches_models_except_requested_additive_extensions(spec):
    generator = runpy.run_path(str(ROOT / "scripts" / "generate_openapi.py"))
    assert number_type_contract_request(spec) == generator["build_openapi"]()


def test_numeric_quantity_wire_type_matches_explicit_contract_change_request(spec):
    quantity = models.ExtractedField(
        name="line_items.0.quantity", value=1.5, type="number", span=None,
        trust="untrusted",
    )
    handle = models.Handle(
        name="$INV-001.line_items.0.quantity", type="number", trust="untrusted",
        span_verified=False,
    )
    assert type(quantity.model_dump(mode="json")["value"]) is float
    proposed = number_type_contract_request(spec)
    for name, record in (("ExtractedField", quantity), ("Handle", handle)):
        schema = {"$ref": f"#/components/schemas/{name}"}
        wire = record.model_dump(mode="json")
        assert not schema_validator(spec, schema).is_valid(wire)
        schema_validator(proposed, schema).validate(wire)


def test_every_specified_endpoint_and_only_those_endpoints_exist(spec):
    contract = (ROOT / "contracts" / "CONTRACT.md").read_text()
    expected = {
        (method.lower(), path.split("?", 1)[0])
        for method, path in re.findall(r"^(GET|POST) (/api/\S+)", contract, re.MULTILINE)
    }
    actual = {(method, path) for path, operations in spec["paths"].items() for method in operations}
    assert len(expected) == 32
    assert actual == expected


def test_sse_pdf_and_error_response_contracts(spec):
    stream = spec["paths"]["/api/runs/{run_id}/events"]["get"]
    assert stream["responses"]["200"]["content"]["text/event-stream"]["schema"] == {
        "$ref": "#/components/schemas/RunEvent",
    }
    assert any(parameter["name"] == "Last-Event-ID" and parameter["in"] == "header" for parameter in stream["parameters"])
    pdf = spec["paths"]["/api/documents/{document_id}/pdf"]["get"]
    assert "application/pdf" in pdf["responses"]["200"]["content"]
    codes = {
        403: "sod_violation", 404: "not_found", 409: "conflict",
        422: "validation_error", 429: "rate_limited", 500: "internal",
        503: "service_unavailable",
    }
    for status, code in codes.items():
        response = spec["components"]["responses"][f"Error{status}"]
        validator = schema_validator(spec, response["content"]["application/json"]["schema"])
        validator.validate({"error": {"code": code, "message": "Example error"}})
        assert not validator.is_valid({"error": {"code": "internal" if status != 500 else "not_found", "message": "Wrong code"}})


def test_all_fixtures_have_validation_models():
    assert {p.name for p in FIXTURES.iterdir() if p.is_file()} == {
        *FIXTURE_MODELS, EVENTS_FILE,
    }


@pytest.mark.parametrize("filename,model", FIXTURE_MODELS.items())
def test_fixture_validates_against_pydantic(filename, model):
    TypeAdapter(model).validate_json((FIXTURES / filename).read_text())


@pytest.mark.parametrize("filename,model", FIXTURE_MODELS.items())
def test_fixture_validates_against_openapi(filename, model, spec):
    if isinstance(model, type):
        schema = {"$ref": f"#/components/schemas/{model.__name__}"}
    else:
        item_model = model.__args__[0]
        schema = {"type": "array", "items": {"$ref": f"#/components/schemas/{item_model.__name__}"}}
    schema_validator(spec, schema).validate(load_json(filename))


def test_all_events_validate_against_both_contracts(events, spec):
    adapter = TypeAdapter(models.RunEvent)
    validator = schema_validator(spec, {"$ref": "#/components/schemas/RunEvent"})
    for event in events:
        adapter.validate_json(json.dumps(event))
        validator.validate(event)


def test_every_discriminated_event_has_a_fixture(events, spec):
    union = spec["components"]["schemas"]["RunEvent"]
    assert union["discriminator"]["propertyName"] == "type"
    assert len(union["oneOf"]) == 15
    assert set(union["discriminator"]["mapping"]) == {event["type"] for event in events}
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["run_id"] for event in events}) == 1
    assert [event["ts"] for event in events] == sorted(event["ts"] for event in events)
    assert all(event["mode"] == "offline" for event in events)


def test_replay_outcomes_and_finances_are_consistent(events):
    batch = load_json("batch.json")
    assert len(batch["invoices"]) == 5
    assert {invoice["invoice_id"] for invoice in batch["invoices"]} == {
        f"INV-{i:03d}" for i in range(1, 6)
    }
    payments = [event for event in events if event["type"] == "payment.executed"]
    assert Counter(event["lane"] for event in payments) == {"unguarded": 5, "countersign": 4}
    poisoned = [event for event in payments if event["payload"]["invoice_id"] == "INV-003"]
    assert len(poisoned) == 1
    assert poisoned[0]["lane"] == "unguarded"
    assert poisoned[0]["payload"]["payment"]["payee_on_file"] is False
    assert all(event["payload"]["payment"]["payee_on_file"] for event in payments if event["lane"] == "countersign")
    held = [event for event in events if event["type"] == "payment.held"]
    assert len(held) == 1
    assert held[0]["payload"]["invoice_id"] == "INV-003"
    assert held[0]["payload"]["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
    completed = next(event for event in events if event["type"] == "run.completed")
    for lane in ("unguarded", "countersign"):
        lane_payments = [event["payload"]["payment"] for event in payments if event["lane"] == lane]
        tally = completed["payload"]["tallies"][lane]
        assert tally["paid_count"] == len(lane_payments)
        assert tally["paid_cents"] == sum(payment["amount_cents"] for payment in lane_payments)
        assert tally["held_count"] == (1 if lane == "countersign" else 0)
        assert tally["blocked_count"] == 0
        assert tally["not_on_file_count"] == sum(not payment["payee_on_file"] for payment in lane_payments)
        assert tally["not_on_file_cents"] == sum(payment["amount_cents"] for payment in lane_payments if not payment["payee_on_file"])
    transfers = {
        payment["payload"]["payment"]["transfer_id"]: payment["payload"]["payment"]
        for payment in payments
    }
    assert len(transfers) == 9
    balances = load_json("balances.json")
    received = 0
    for account in balances["accounts"]:
        assert account["account_id"].startswith("example-")
        for transfer in account["transfers_this_session"]:
            assert transfer["transfer_id"] in transfers
            assert transfer["amount_cents"] == transfers[transfer["transfer_id"]]["amount_cents"]
        if account["role"] != "company":
            assert account["received_this_session_cents"] == sum(t["amount_cents"] for t in account["transfers_this_session"])
            received += account["received_this_session_cents"]
    assert received == sum(payment["amount_cents"] for payment in transfers.values())
    approvals = load_json("approvals.json")
    assert approvals[0]["approval_id"] == held[0]["payload"]["approval_id"]
    assert approvals[0]["resolution"]["decision"] == "reject"


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_source_spans_and_graph_references(events):
    document = load_json("document_INV-003.json")
    lines = {(page["page"], line["line"]): line["text"] for page in document["pages"] for line in page["lines"]}
    graph = load_json("graph_INV-003.json")
    for value in [graph, *events]:
        for item in walk(value):
            if "char_start" in item and item["document_id"] == document["document_id"]:
                text = lines[(item["page"], item["line"])]
                assert 0 <= item["char_start"] < item["char_end"] <= len(text)
                assert text[item["char_start"]:item["char_end"]] == item["quote"]
                assert item["span_verified"] is True
    ids = {node["id"] for node in graph["nodes"]}
    assert len(ids) == len(graph["nodes"])
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
    for event in events:
        if event["type"] == "policy.decision" and event["payload"]["decision"]["invoice_id"] == "INV-003":
            for check in event["payload"]["decision"]["checks"]:
                assert set(check["evidence_node_ids"]) <= ids


def test_transcript_prompt_hashes_and_attack_quarantine(events):
    transcript = load_json("planner_transcript.json")
    extracted = next(event for event in events if event["type"] == "fields.extracted" and event["payload"]["invoice_id"] == "INV-003")
    attack_quotes = [span["quote"] for span in extracted["payload"]["instruction_like_spans"]]
    attacker_account = next(field["value"] for field in extracted["payload"]["fields"] if field["type"] == "account_number")
    for call in transcript["calls"]:
        assert call["call_id"].startswith("example-")
        assert hashlib.sha256(call["prompt"].encode()).hexdigest() == call["prompt_hash"]
        assert all(quote not in call["prompt"] for quote in attack_quotes)
        assert attacker_account not in call["prompt"]
    assert transcript["leak_guard"]["matches"] == 0


def test_journal_and_reconciliation_examples_balance():
    entries = load_json("ledger_entries.json")
    for entry in entries:
        assert sum(line["debit_cents"] for line in entry["lines"]) == sum(line["credit_cents"] for line in entry["lines"])
    trial_balance = load_json("trial_balance.json")
    assert trial_balance["balanced"] is True
    assert sum(account["debit_cents"] for account in trial_balance["accounts"]) == sum(account["credit_cents"] for account in trial_balance["accounts"])
    reconciliation = load_json("reconciliation.json")
    assert reconciliation["difference_cents"] == reconciliation["bank_balance_change_cents"] - reconciliation["ledger_cash_change_cents"] == 0


@pytest.mark.parametrize("filename,field,value", [
    ("batch_request.json", "kind", "attack"),
    ("attack.json", "attack_text", ""),
    ("attack.json", "attack_text", "x" * 601),
    ("run_request.json", "lanes", ["system"]),
    ("run_request.json", "instruction", None),
    ("redteam_request.json", "variants", 9),
    ("redteam_request.json", "variants", 61),
    ("eval_request.json", "suite", "unknown"),
])
def test_requests_reject_out_of_contract_values(filename, field, value):
    data = load_json(filename)
    data[field] = value
    with pytest.raises(ValidationError):
        TypeAdapter(FIXTURE_MODELS[filename]).validate_json(json.dumps(data))


def test_required_actor_integer_money_and_event_lane_are_enforced(events):
    decision = load_json("approval_decision.json")
    decision.pop("actor_id")
    with pytest.raises(ValidationError):
        models.ApprovalDecision.model_validate(decision)
    invoice = load_json("batch.json")["invoices"][0]
    invoice["amount_cents"] = 12.34
    with pytest.raises(ValidationError):
        models.InvoiceSummary.model_validate(invoice)
    event = deepcopy(next(event for event in events if event["type"] == "policy.decision"))
    event["lane"] = "unguarded"
    with pytest.raises(ValidationError):
        TypeAdapter(models.RunEvent).validate_json(json.dumps(event))
    event["type"] = "unknown.event"
    with pytest.raises(ValidationError):
        TypeAdapter(models.RunEvent).validate_json(json.dumps(event))


def test_dates_and_timestamps_reject_non_string_wire_values():
    invoice = load_json("batch.json")["invoices"][0]
    for invalid in (1792713600, "2026-10-23T00:00:00Z"):
        invoice["due_date"] = invalid
        with pytest.raises(ValidationError):
            models.InvoiceSummary.model_validate_json(json.dumps(invoice))
    health = load_json("health.json")
    for invalid in (1792713600, "2026-10-23T00:00:00", "2026-10-23T01:00:00+01:00"):
        health["session_started_at"] = invalid
        with pytest.raises(ValidationError):
            models.Health.model_validate_json(json.dumps(health))
