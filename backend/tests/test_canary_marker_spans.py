"""The hidden reference marker is a leak-test token, not instruction text a person wrote.

A live extractor flagged the white 1-point "Reference: CS-CANARY-XXXX" line as
instruction-like; after display masking it reached the UI as "Reference: ". UI-facing
span lists must drop it, while raw sources keep it for the leak guard and audit.
"""

import json
import re
from unittest.mock import AsyncMock

import httpx
import pytest

from app.contract_models import DocumentText
from app.pipeline.extraction_models import RawSpan
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload
from app.security.presentation import display_model, is_reference_marker
from tests.test_api import ASGIStream
from tests.test_custom_attack_runs import CUSTOM_TEXT, ScriptedCustomNoteAgent, application, note_account

_MARKER_LINE = re.compile(r"Reference: CS-CANARY-[A-Z0-9]{4,}")


def test_marker_detection_is_narrow():
    assert is_reference_marker("Reference: CS-CANARY-AB12")
    assert is_reference_marker("CS-CANARY-AB12")
    assert is_reference_marker("  Reference:   CS-CANARY-AB12  ")
    # Real text, or text next to a marker, is never dropped.
    assert not is_reference_marker("Reference: please remit to 999900009999")
    assert not is_reference_marker("Remit to 999900009999. Reference: CS-CANARY-AB12")
    assert not is_reference_marker("Reference:")
    assert not is_reference_marker("")


@pytest.mark.asyncio
async def test_marker_line_is_dropped_from_ui_spans_but_kept_in_raw_sources(tmp_path, monkeypatch):
    from app.adapters.bank import LiveBankAdapter

    monkeypatch.setattr(LiveBankAdapter, "transfer", AsyncMock(side_effect=AssertionError("no Nessie")))
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        app.state.adapters.agent.complete = ScriptedCustomNoteAgent().complete

        async def scripted_extractor(document):
            # Like the live extractor: flag the attack note and the whole marker line.
            raw = parse_offline(document)
            requested = note_account(document)
            if requested is not None:
                raw.remit_account_number = requested
                raw.instruction_like_spans.append(RawSpan(quote=requested.quote, page=requested.page, line=requested.line))
                for page in document.pages:
                    for line in page.lines:
                        match = _MARKER_LINE.search(line.text)
                        if match:
                            raw.instruction_like_spans.append(RawSpan(quote=match[0], page=page.page, line=line.line))
            return to_payload(document, raw, app.state.adapters.extractor.model)

        app.state.adapters.extractor.extract = scripted_extractor
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/attacks", json={
                "vendor_id": "VEN-003", "attack_text": CUSTOM_TEXT, "placement": "hidden_white_text",
            })).json()
            poisoned = next(item for item in batch["invoices"] if item["vendor_id"] == "VEN-003")
            document = DocumentText.model_validate(app.state.store.get_record("documents", poisoned["document_id"])["text"])
            [canary] = re.findall(r"CS-CANARY-[A-Z0-9]{4,}", "\n".join(
                line.text for page in document.pages for line in page.lines))
            started = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["unguarded", "countersign"]})
            run = await app.state.run_manager.wait(started.json()["run_id"])

            # Raw sources keep the marker: the stored extraction, the stored event and the raw graph.
            extraction = app.state.store.get_record("extractions", f"{run.run_id}:{poisoned['invoice_id']}")
            raw_quotes = [span["quote"] for span in extraction["instruction_like_spans"]]
            assert any(canary in quote for quote in raw_quotes) and len(raw_quotes) == 2
            stored = next(event for event in app.state.store.list_events(run.run_id)
                          if event.type == "fields.extracted" and event.payload.invoice_id == poisoned["invoice_id"])
            assert any(canary in span.quote for span in stored.payload.instruction_like_spans)
            raw_graph = app.state.store.get_record("graphs", f"{run.run_id}:{poisoned['invoice_id']}")
            assert canary in json.dumps(raw_graph)
            # The leak guard still checks the canary and found no leak.
            planner = (await client.get(f"/api/runs/{run.run_id}/planner_transcript")).json()
            assert planner["leak_guard"]["canaries_checked"] > 0 and planner["leak_guard"]["matches"] == 0

            # UI-facing: the SSE event keeps the real attack note only.
            async with ASGIStream(app, run.run_id, stored.seq - 1) as stream:
                public = await stream.event()
            assert public.type == "fields.extracted"
            quotes = [span.quote for span in public.payload.instruction_like_spans]
            assert quotes == [note_account(document).quote]
            assert not any("Reference" in quote or "CS-CANARY-" in quote for quote in quotes)

            # UI-facing graph: no marker span node, no dangling edge, the note still feeds the check.
            graph = (await client.get(f"/api/runs/{run.run_id}/invoices/{poisoned['invoice_id']}/graph")).json()
            assert "Reference:" not in json.dumps(graph) and "CS-CANARY-" not in json.dumps(graph)
            ids = {node["id"] for node in graph["nodes"]}
            assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
            check = next(node for node in graph["nodes"] if node["kind"] == "policy_check"
                         and node["label"].startswith("INSTRUCTIONS_IN_DOCUMENT"))
            assert any(edge["target"] == check["id"] for edge in graph["edges"])

            # Policy outcome is unchanged.
            decision = next(item for item in (await client.get(f"/api/runs/{run.run_id}/decisions")).json()
                            if item["invoice_id"] == poisoned["invoice_id"])
            assert decision["outcome"] == "HOLD" and decision["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
            # Decision evidence only points at nodes the UI graph shows (here and on the SSE stream).
            assert all(set(check["evidence_node_ids"]) <= ids for check in decision["checks"])
            raw_decision = next(event for event in app.state.store.list_events(run.run_id) if event.type == "policy.decision"
                                and event.payload.decision.invoice_id == poisoned["invoice_id"])
            async with ASGIStream(app, run.run_id, raw_decision.seq - 1) as stream:
                streamed = await stream.event()
            assert streamed.type == "policy.decision"
            assert all(set(check.evidence_node_ids) <= ids for check in streamed.payload.decision.checks)
            # The stored decision still references the marker's node.
            stored_ids = {i for check in raw_decision.payload.decision.checks for i in check.evidence_node_ids}
            assert stored_ids - ids


def test_display_model_leaves_other_models_alone():
    text = DocumentText.model_validate({"document_id": "d", "invoice_id": "i", "pages": [
        {"page": 1, "lines": [{"line": 1, "text": "Reference: CS-CANARY-AB12"}]}]})
    # A document line is source text, not a span list: masked, not removed.
    assert display_model(text).pages[0].lines[0].text == "Reference: " + " " * len("CS-CANARY-AB12")
