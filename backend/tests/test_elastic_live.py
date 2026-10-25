"""Fresh Nessie payments audited by the real Elastic Agent Builder agent.

Every accepted run, including failed assertions or unavailable audit answers,
leaves a separate evidence file. This test never substitutes an application LLM
or an offline answer for Agent Builder.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.audit.events import INDEX_NAME
from app.config import Settings
from app.contract_models import AuditAnswer
from app.main import create_app
from app.store import Store
from app.world.seed import seed_world


DOCS = Path(__file__).resolve().parents[1] / "docs"
QUESTION = "Which payments were held because the payee came from an untrusted document?"


def _snapshot(store, run_id):
    """Record application evidence, never settings or HTTP authorization headers."""
    run = store.get_run(run_id)
    events = store.list_events(run_id)
    return {
        "run": run.model_dump(mode="json") if run is not None else None,
        "events": [event.model_dump(mode="json") for event in events],
        "decisions": store.list_records("decisions", run_id=run_id),
        "approvals": store.list_records("approvals", run_id=run_id),
        "transfers": store.list_records("transfers", run_id=run_id),
        "planner_transcript": store.get_record("planner_transcripts", run_id),
        "extraction_records": [
            event.payload.model_dump(mode="json")
            for event in events if event.type == "fields.extracted"
        ],
        "audit_pending_count": store.audit_pending_count(run_id=run_id),
    }


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_held_payments_answer_uses_elastic_agent_builder(tmp_path):
    settings = Settings()
    required_keys = ("nessie_api_key", "anthropic_api_key", "gemini_api_key", "elastic_api_key")
    if not all(getattr(settings, name).get_secret_value() for name in required_keys):
        pytest.skip("Live Nessie, Anthropic, extractor and Elastic credentials are required")
    if not settings.elastic_url or not settings.kibana_url:
        pytest.skip("ELASTIC_URL and KIBANA_URL are required for the live Agent Builder test")
    settings = settings.model_copy(update={
        "nessie_mode": "live", "extractor_mode": "live", "planner_mode": "live",
        "elastic_mode": "live", "agent_mode": "offline", "mutator_mode": "offline",
    })
    proof = {
        "recorded": True,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "attempt_id": uuid4().hex,
        "status": "failed",
        "execution": "fresh_live_countersign_with_elastic_agent_builder",
        "modes": {"bank": "live", "extractor": "live", "planner": "live",
                  "elastic": "live", "unguarded_agent": "offline", "mutator": "offline"},
        "providers": {
            "planner": {"provider": "anthropic", "model": settings.planner_model},
            "extractor_primary": {"provider": "gemini", "model": settings.extractor_model},
            "extractor_fallback": {"provider": "anthropic", "model": settings.extractor_fallback_model},
            "audit": {"provider": "elastic_agent_builder", "agent_id": settings.elastic_agent_id},
        },
        "extractor_cache": settings.extractor_cache,
        "question": QUESTION,
    }
    run_id = None
    app = create_app(settings, db_path=tmp_path / "elastic-live.sqlite")
    try:
        async with app.router.lifespan_context(app):
            # Reuse the genuine seeded Nessie accounts, but no earlier paid
            # invoice claims or ledger entries: this is a fresh payment run.
            existing = Store()
            existing.initialize()
            try:
                for table, key in (("bank_directory", "display_number"), ("bank_seeds", "seed_id")):
                    for row in existing.list_records(table):
                        if row.get("mode") == "live":
                            app.state.store.save_record(table, row[key], row)
            finally:
                existing.close()
            await seed_world(app.state.store, app.state.adapters.bank)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                try:
                    batch_response = await client.post("/api/batches", json={"kind": "standard"})
                    proof["batch_http_status"] = batch_response.status_code
                    assert batch_response.status_code == 200
                    batch = batch_response.json()
                    proof["batch"] = batch
                    initial = await client.post("/api/runs", json={
                        "batch_id": batch["batch_id"], "lanes": ["countersign"],
                    })
                    proof["run_http_status"] = initial.status_code
                    assert initial.status_code == 200
                    run_id = initial.json()["run_id"]
                    proof["run_id"] = run_id
                    run = await app.state.run_manager.wait(run_id)
                    proof.update(_snapshot(app.state.store, run_id))
                    payments = [event.payload.payment for event in app.state.store.list_events(run_id)
                                if event.type == "payment.executed" and event.lane == "countersign"]
                    assert len(run.lanes) == 1 and run.lanes[0].status == "completed"
                    assert run.tallies.countersign.paid_count == 4
                    assert run.tallies.countersign.held_count == 1
                    assert len(payments) == 4
                    assert all(payment.bank_mode == "live" and payment.payee_on_file
                               and payment.transfer_id for payment in payments)
                    assert len({payment.transfer_id for payment in payments}) == 4
                    held = [decision for decision in proof["decisions"] if decision["outcome"] == "HOLD"]
                    assert len(held) == 1
                    assert held[0]["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
                    held_invoice = next(invoice for invoice in batch["invoices"]
                                        if invoice["invoice_id"] == held[0]["invoice_id"])
                    proof["held_invoice"] = held_invoice
                    graph_response = await client.get(
                        f"/api/runs/{run_id}/invoices/{held_invoice['invoice_id']}/graph")
                    proof["held_invoice_graph_http_status"] = graph_response.status_code
                    assert graph_response.status_code == 200
                    proof["held_invoice_graph"] = graph_response.json()
                    assert proof["planner_transcript"]["leak_guard"]["matches"] == 0

                    audit = app.state.adapters.audit
                    proof["audit_flush_succeeded"] = await audit.flush(run_id=run_id, refresh=True)
                    assert proof["audit_flush_succeeded"] is True
                    assert app.state.store.audit_pending_count(run_id=run_id) == 0
                    count_query = {"query": {"term": {"run_id": run_id}}}
                    count_response = await audit._request("POST", f"/{INDEX_NAME}/_count", json=count_query)
                    proof["elastic_count"] = {"index": INDEX_NAME, "query": count_query,
                                              "http_status": count_response.status_code}
                    assert count_response.status_code == 200
                    count = count_response.json()["count"]
                    proof["elastic_count"]["count"] = count
                    proof["elastic_count"]["source_event_count"] = len(app.state.store.list_events(run_id))
                    assert count == proof["elastic_count"]["source_event_count"]

                    response = await client.post("/api/audit/ask", json={"question": QUESTION, "run_id": run_id})
                    proof["audit_ask_http_status"] = response.status_code
                    proof["audit_ask_response"] = response.json()
                    assert response.status_code == 200
                    answer = AuditAnswer.model_validate(response.json())
                    assert answer.agent_id == settings.elastic_agent_id
                    assert run_id in answer.answer
                    assert any(identifier.casefold() in answer.answer.casefold()
                               for identifier in (held_invoice["invoice_id"], held_invoice["invoice_number"]))
                    steps = [step for step in answer.steps if step.esql and step.esql.strip()
                             and step.row_count is not None and step.row_count >= 1]
                    assert steps, "Agent Builder must return an actual ES|QL tool step with at least one row"
                    assert any(INDEX_NAME in step.esql for step in steps)
                    proof["status"] = "passed"
                finally:
                    if run_id is not None:
                        proof.update(_snapshot(app.state.store, run_id))
    except Exception as error:
        # Upstream exception text may contain request information. Persist only
        # the type; application error events and contract responses hold detail.
        proof["failure_type"] = type(error).__name__
        raise
    finally:
        identity = run_id if run_id is not None else "unaccepted_attempt_" + proof["attempt_id"]
        destination = DOCS / f"elastic_live_{identity}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as output:
            json.dump(proof, output, indent=2)
            output.write("\n")
        print(json.dumps({"run_id": run_id, "status": proof["status"], "evidence": str(destination),
                          "indexed_events": proof.get("elastic_count", {}).get("count"),
                          "audit_ask_http_status": proof.get("audit_ask_http_status"),
                          "tool_steps": len(proof.get("audit_ask_response", {}).get("steps", []))}))
