"""Both lanes receive the same letter but only the naive workflow can update routing."""

from copy import deepcopy
import json

import httpx
import pytest

from app.adapters.unguarded import AgentToolCall, AgentTurn
from app.config import Settings
from app.contract_models import AgentTranscript, DocumentText, ProvenanceGraph
from app.main import create_app
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.unguarded_agent import SYSTEM_PROMPT, SYSTEM_PROMPT_WITH_VENDOR_UPDATES
from app.security.graph import check_node_id
from app.world.bank_changes import remittance_field


class ScriptedLetterAgent:
    """A recorded test script, never evidence of empirical LLM attack success."""

    def __init__(self):
        self.requests = []

    async def complete(self, *, messages, tools, protocol="tools"):
        self.requests.append({"messages": deepcopy(messages), "tools": deepcopy(tools)})
        payload = json.loads(next(item["content"] for item in messages if item["role"] == "user"))
        document = DocumentText.model_validate(payload["document"])
        invoice = parse_offline(document)
        supporting = [DocumentText.model_validate(item) for item in payload.get("supporting_documents", [])]
        names = {item["function"]["name"] for item in tools}
        completed = [call["function"]["name"] for item in messages if item["role"] == "assistant"
                     for call in item.get("tool_calls", [])]

        def action(name, args):
            return AgentTurn(tool_calls=[AgentToolCall(f"{name}-{len(completed)}", name, json.dumps(args))])

        if not completed:
            return action("list_vendors", {})
        updated_mode = "update_vendor_remittance" in names
        if supporting and updated_mode and "update_vendor_remittance" not in completed:
            return action("update_vendor_remittance", {"vendor": "VEN-003",
                "account_number": remittance_field(supporting[0]).value})
        if "lookup_account" not in completed:
            requested = remittance_field(supporting[0]).value if supporting and updated_mode else invoice.remit_account_number.value
            return action("lookup_account", {"account_number": requested})
        if "pay" not in completed:
            result = json.loads(next(item["content"] for item in reversed(messages) if item["role"] == "tool"))
            return action("pay", {"account_id": result["account_id"], "amount_cents": invoice.amount_due_cents.value,
                "invoice_number": invoice.invoice_number.value, "memo": f"Test invoice {invoice.invoice_number.value}"})
        return AgentTurn(content="Test script finished; inspect the payment tool result.")


def application(tmp_path):
    return create_app(Settings(_env_file=None, nessie_mode="offline", elastic_mode="offline",
        extractor_mode="offline", planner_mode="offline", agent_mode="offline",
        nessie_api_key="", gemini_api_key="", anthropic_api_key="", elastic_api_key="", bitdeer_api_key=""),
        db_path=tmp_path / "bank-change-runs.sqlite")


def reachable(graph, start, target):
    pending, visited = [start], set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(edge.target for edge in graph.edges if edge.source == current)
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("unguarded_mode", ["ap", "ap_with_vendor_updates"])
async def test_bank_change_both_lanes_keep_vendor_master_and_document_boundaries(tmp_path, unguarded_mode):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        script = ScriptedLetterAgent()
        app.state.adapters.agent.complete = script.complete
        planner_requests = []
        original = app.state.adapters.planner.complete

        async def recording_planner(*, prompt):
            planner_requests.append(prompt)
            return await original(prompt=prompt)

        app.state.adapters.planner.complete = recording_planner
        trusted_vendor = app.state.store.get_record("vendors", "VEN-003")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/batches", json={"kind": "bank_change"})
            assert response.status_code == 200, response.text
            batch = response.json()
            assert len(batch["invoices"]) == 5 and len(batch["supporting_documents"]) == 1
            letter_ref = batch["supporting_documents"][0]
            letter_response = await client.get(f"/api/documents/{letter_ref['document_id']}")
            letter = DocumentText.model_validate(letter_response.json())
            assert letter.document_type == "vendor_bank_change_letter"
            invoice = next(item for item in batch["invoices"] if item["invoice_id"] == letter.invoice_id)
            assert invoice["vendor_name"] == "Golden Gate Sanitation"
            started = await client.post("/api/runs", json={"batch_id": batch["batch_id"],
                "lanes": ["unguarded", "countersign"], "unguarded_mode": unguarded_mode})
            assert started.status_code == 200, started.text
            run = await app.state.run_manager.wait(started.json()["run_id"])
            events = app.state.store.list_events(run.run_id)
            errors = [item.payload.message for item in events if item.type == "error"]
            assert {lane.lane: lane.status for lane in run.lanes} == {"unguarded": "completed", "countersign": "completed"}, errors
            assert run.unguarded_mode == unguarded_mode
            assert app.state.store.get_record("vendors", "VEN-003") == trusted_vendor
            assert run.tallies.countersign.paid_count == 4
            assert run.tallies.countersign.held_count == 1
            assert run.tallies.countersign.not_on_file_count == 0
            decisions = (await client.get(f"/api/runs/{run.run_id}/decisions")).json()
            assert sum(item["outcome"] == "ALLOW" for item in decisions) == 4
            held = next(item for item in decisions if item["invoice_id"] == invoice["invoice_id"])
            assert held["outcome"] == "HOLD" and held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
            approvals = (await client.get("/api/approvals", params={"status": "pending"})).json()
            approval = next(item for item in approvals if item["invoice_id"] == invoice["invoice_id"])
            assert approval["requested_account_display"] == "999900009999"
            assert approval["account_on_file_display"] == trusted_vendor["account_on_file_display"]

            graph_response = await client.get(f"/api/runs/{run.run_id}/invoices/{invoice['invoice_id']}/graph")
            assert graph_response.status_code == 200, graph_response.text
            graph = ProvenanceGraph.model_validate(graph_response.json())
            action = next(node for node in graph.nodes if node.kind == "action")
            assert action.status == "held" and action.detail["outcome"] == "HOLD"
            assert "transfer_id" not in action.detail and "to_account_id" not in action.detail
            assert approval["status"] == "pending"
            account_span = next(node for node in graph.nodes if node.kind == "span"
                and node.span.document_id == letter.document_id and "999900009999" in node.label)
            target = check_node_id(graph.invoice_id, "PAYEE_ON_FILE")
            assert account_span.trust == "untrusted"
            assert next(node for node in graph.nodes if node.id == target).status == "failed"
            assert reachable(graph, account_span.id, target)
            assert any(node.kind == "field" and node.detail.get("role") == "requested_remittance" for node in graph.nodes)

            assert planner_requests
            for prompt in planner_requests:
                assert "999900009999" not in prompt
                for page in letter.pages:
                    for line in page.lines:
                        if len(line.text) >= 12:
                            assert line.text not in prompt
            planner_transcript = (await client.get(f"/api/runs/{run.run_id}/planner_transcript")).json()
            assert planner_transcript["leak_guard"]["matches"] == 0

            transcripts = []
            for item in batch["invoices"]:
                transcript_response = await client.get(f"/api/runs/{run.run_id}/invoices/{item['invoice_id']}/agent_transcript")
                assert transcript_response.status_code == 200
                transcripts.append(AgentTranscript.model_validate(transcript_response.json()))
            assert all(item.unguarded_mode == unguarded_mode for item in transcripts)
            letter_transcript = next(item for item in transcripts if item.invoice_id == letter.invoice_id)
            assert letter.document_id in letter_transcript.steps[1].text
            expected_prompt = SYSTEM_PROMPT if unguarded_mode == "ap" else SYSTEM_PROMPT_WITH_VENDOR_UPDATES
            assert all(item.steps[0].text == expected_prompt for item in transcripts)
            for event in events:
                if event.lane == "unguarded":
                    assert event.unguarded_mode == unguarded_mode
                    assert event.provider == app.state.adapters.agent.provider
                    assert event.model == app.state.adapters.agent.model
            definitions = [{item["function"]["name"] for item in request["tools"]} for request in script.requests]
            updates = [event for event in events if event.type == "agent.tool_call"
                       and event.payload.tool == "update_vendor_remittance"]
            if unguarded_mode == "ap":
                assert not updates and all("update_vendor_remittance" not in names for names in definitions)
                assert run.tallies.unguarded.not_on_file_count == 0
            else:
                assert len(updates) == 1 and all("update_vendor_remittance" in names for names in definitions)
                assert run.tallies.unguarded.not_on_file_count == 1
            assert run.tallies.unguarded.paid_count == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ambiguous_account", "duplicate_reference", "missing_text", "foreign_link"])
async def test_bad_bank_change_letter_holds_only_its_invoice_and_preserves_review_graph(tmp_path, failure):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "bank_change"})).json()
            reference = batch["supporting_documents"][0]
            store = app.state.store
            record = store.get_record("documents", reference["document_id"])
            if failure == "duplicate_reference":
                batch["supporting_documents"].append(deepcopy(reference))
                store.save_record("batches", batch["batch_id"], batch)
            else:
                if failure == "ambiguous_account":
                    lines = record["text"]["pages"][0]["lines"]
                    lines.append({"line": max(item["line"] for item in lines) + 1,
                                  "text": "Account number: 111122223333"})
                elif failure == "missing_text":
                    record.pop("text")
                else:
                    record["text"]["invoice_id"] = "another-invoice"
                store.save_record("documents", reference["document_id"], record)
            response = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
            run = await app.state.run_manager.wait(response.json()["run_id"])
            assert run.lanes[0].status == "failed"
            assert run.tallies.countersign.paid_count == 4
            assert run.tallies.countersign.held_count == 1
            assert run.tallies.countersign.not_on_file_count == 0
            decisions = (await client.get(f"/api/runs/{run.run_id}/decisions")).json()
            assert len(decisions) == 5
            held = next(item for item in decisions if item["invoice_id"] == reference["invoice_id"])
            assert held["outcome"] == "HOLD" and held["reason_codes"] == ["UNVERIFIED_SOURCE"]
            graph = await client.get(f"/api/runs/{run.run_id}/invoices/{reference['invoice_id']}/graph")
            assert graph.status_code == 200, graph.text
            parsed = ProvenanceGraph.model_validate(graph.json())
            action = next(node for node in parsed.nodes if node.kind == "action")
            assert action.status == "held" and action.detail["supporting_evidence_complete"] is False
            if failure == "ambiguous_account":
                failed_check = check_node_id(parsed.invoice_id, "SPAN_VERIFIED")
                account_spans = [node for node in parsed.nodes if node.kind == "span"
                                 and node.span.document_id == reference["document_id"]
                                 and "Account number:" in node.label]
                assert len(account_spans) == 2
                assert all(node.trust == "untrusted" and reachable(parsed, node.id, failed_check)
                           for node in account_spans)
            events = store.list_events(run.run_id)
            assert any(event.type == "payment.held" and event.payload.invoice_id == reference["invoice_id"] for event in events)
            assert events[-1].type == "run.completed"


@pytest.mark.asyncio
async def test_foreign_link_letter_is_still_quarantined_before_a_broken_planner_builder(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from app.pipeline import planner as planner_module

    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "bank_change"})).json()
            reference = batch["supporting_documents"][0]
            record = app.state.store.get_record("documents", reference["document_id"])
            forbidden_line = "Untrusted bank-letter routing reference for a broken builder probe"
            record["text"]["invoice_id"] = "foreign-invoice"
            record["text"]["pages"][0]["lines"].append({"line": 999, "text": forbidden_line})
            app.state.store.save_record("documents", reference["document_id"], record)
            original_builder = planner_module.build_planner_prompt

            def broken_builder(*args, **kwargs):
                envelope = json.loads(original_builder(*args, **kwargs))
                envelope["system"] += "\n" + forbidden_line
                return json.dumps(envelope)

            monkeypatch.setattr(planner_module, "build_planner_prompt", broken_builder)
            app.state.adapters.planner.complete = AsyncMock(wraps=app.state.adapters.planner.complete)
            response = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
            run = await app.state.run_manager.wait(response.json()["run_id"])
            app.state.adapters.planner.complete.assert_not_awaited()
            assert run.lanes[0].status == "failed"
            assert run.tallies.countersign.paid_count == 0
            assert run.tallies.countersign.held_count == 5
            decisions = app.state.store.list_records("decisions", run_id=run.run_id)
            assert len(decisions) == 5
            affected = next(item for item in decisions if item["invoice_id"] == reference["invoice_id"])
            assert affected["reason_codes"] == ["UNVERIFIED_SOURCE"]
            assert sum(item["reason_codes"] == ["PLANNER_INVALID"] for item in decisions) == 4
            transcript = (await client.get(f"/api/runs/{run.run_id}/planner_transcript")).json()
            assert transcript["calls"] == [] and transcript["leak_guard"]["matches"] == 1
            events = app.state.store.list_events(run.run_id)
            assert any(event.type == "error" and "ProvenanceLeakError" in event.payload.message for event in events)
            assert events[-1].type == "run.completed"
