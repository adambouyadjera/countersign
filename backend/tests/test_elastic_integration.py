"""The production event/ledger path reliably feeds the live Elastic adapter."""

import asyncio
from collections import Counter
import json

import httpx
import pytest
import respx

from app.config import Settings
from app.main import create_app
from app.store import Store


ES = "https://elastic-integration.invalid"


@pytest.fixture(autouse=True)
def no_inherited_credentials(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def settings():
    return Settings(_env_file=None, nessie_mode="offline", elastic_mode="live",
        extractor_mode="offline", planner_mode="offline", agent_mode="offline", mutator_mode="offline",
        nessie_api_key="", elastic_api_key="synthetic-elastic-key", anthropic_api_key="",
        gemini_api_key="", bitdeer_api_key="", naive_api_key="", mutator_api_key="",
        elastic_url=ES, kibana_url="https://kibana-integration.invalid",
        elastic_agent_id="countersign-audit-analyst")


class MockElastic:
    def __init__(self, *, bulk_healthy=True):
        self.bulk_healthy = bulk_healthy
        self.accepted = []
        self.bulk_attempts = []
        self.first_bulk = asyncio.Event()
        self.auth = respx.get(ES + "/_security/_authenticate").mock(
            return_value=httpx.Response(200, json={"username": "synthetic-auditor"}))
        respx.put(ES + "/countersign-events").mock(
            return_value=httpx.Response(201, json={"acknowledged": True}))
        respx.put(ES + "/countersign-events/_mapping").mock(
            return_value=httpx.Response(200, json={"acknowledged": True}))
        self.refresh = respx.post(ES + "/countersign-events/_refresh").mock(
            return_value=httpx.Response(200, json={"_shards": {"failed": 0}}))
        respx.post(ES + "/_bulk").mock(side_effect=self.bulk)

    def bulk(self, request):
        assert request.headers["Authorization"] == "ApiKey synthetic-elastic-key"
        assert request.headers["Content-Type"] == "application/x-ndjson"
        assert request.content.endswith(b"\n")
        lines = [json.loads(line) for line in request.content.decode().splitlines()]
        assert len(lines) % 2 == 0
        documents = [(lines[index]["index"], lines[index + 1]) for index in range(0, len(lines), 2)]
        self.bulk_attempts.append([action["_id"] for action, _ in documents])
        self.first_bulk.set()
        if not self.bulk_healthy:
            return httpx.Response(503, text="upstream body must never become a run error")
        for action, document in documents:
            assert action["_index"] == "countersign-events"
            assert action["_id"] == f"{document['run_id']}:{document['seq']}"
            self.accepted.append((action["_id"], document))
        return httpx.Response(200, json={"errors": False,
            "items": [{"index": {"status": 201}} for _ in documents]})


async def source_run(app, http):
    batch = await http.post("/api/batches", json={"kind": "standard"})
    assert batch.status_code == 200
    started = await http.post("/api/runs", json={"batch_id": batch.json()["batch_id"], "lanes": ["countersign"]})
    assert started.status_code == 200
    return started.json()["run_id"]


async def eventually(predicate, *, timeout=5):
    async def observe():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(observe(), timeout=timeout)


def assert_all_events_indexed_once(app, run_id, remote):
    events = app.state.store.list_events(run_id)
    matching = [(identity, document) for identity, document in remote.accepted if document["run_id"] == run_id]
    assert Counter(identity for identity, _ in matching) == Counter(f"{run_id}:{event.seq}" for event in events)
    assert all(count == 1 for count in Counter(identity for identity, _ in matching).values())
    by_id = dict(matching)
    for event in events:
        document = by_id[f"{run_id}:{event.seq}"]
        assert document["payload"] == event.model_dump(mode="json", exclude_none=True)
        assert document["type"] == event.type and document["lane"] == event.lane
        assert document["mode"] == event.mode
    assert app.state.store.audit_pending_count(run_id) == 0
    return matching


@pytest.mark.asyncio
@respx.mock
async def test_standard_run_and_all_ledger_event_paths_are_indexed_exactly_once(tmp_path):
    remote = MockElastic()
    app = create_app(settings(), db_path=tmp_path / "audit-integration.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            run_id = await source_run(app, http)
            run = await app.state.run_manager.wait(run_id)
            assert run.lanes[0].status == "completed"
            assert (run.tallies.countersign.paid_count, run.tallies.countersign.held_count) == (4, 1)
            # The actual background sink receives notifications without any
            # network work being performed in a payment transaction.
            await asyncio.wait_for(remote.first_bulk.wait(), timeout=3)
            assert await app.state.adapters.audit.flush(run_id=run_id, refresh=True)
            documents = assert_all_events_indexed_once(app, run_id, remote)
            assert sum(document["type"] == "ledger.posted" for _, document in documents) == 8
            assert sum(document["type"] == "payment.executed" for _, document in documents) == 4
            held = next(document for _, document in documents if document["type"] == "payment.held")
            assert held["invoice_number"] == "INV-003"
            assert held["vendor_id"] == "VEN-003" and held["outcome"] == "HOLD"
            assert held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
            assert held["payee_document_derived"] is True
            assert held["attack_placement"] == "hidden_white_text"

            original = next(entry for entry in app.state.ledger.list_entries(run_id=run_id)
                            if entry.source.kind == "invoice")
            args = {"created_by": "person-controller", "memo": "Append a reversal for audit integration"}
            reversal = await app.state.event_bus.reverse_entry(original.entry_id, **args)
            assert await app.state.event_bus.reverse_entry(original.entry_id, **args) == reversal
            assert await app.state.adapters.audit.flush(run_id=run_id, refresh=True)
            documents = assert_all_events_indexed_once(app, run_id, remote)
            ledger = [document for _, document in documents if document["type"] == "ledger.posted"]
            assert len(ledger) == 9
            reversed_documents = [document for document in ledger
                if document["payload"]["payload"]["entry"]["entry_id"] == reversal.entry_id]
            assert len(reversed_documents) == 1
            assert reversed_documents[0]["invoice_id"] == original.source.id
            assert app.state.ledger.trial_balance().balanced is True


@pytest.mark.asyncio
@respx.mock
async def test_bulk_outage_does_not_stop_run_reports_one_error_and_recovers_cached_health(tmp_path):
    remote = MockElastic(bulk_healthy=False)
    app = create_app(settings(), db_path=tmp_path / "audit-outage.db")
    first_extraction, release = asyncio.Event(), asyncio.Event()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            first_health = await http.get("/api/health")
            assert first_health.status_code == 200 and first_health.json()["services"]["elastic"]["status"] == "ok"
            assert remote.auth.call_count == 1  # Cache this success before delivery fails.
            original = app.state.adapters.extractor.extract

            async def gated_extract(document):
                if not first_extraction.is_set():
                    first_extraction.set()
                    await release.wait()
                return await original(document)

            app.state.adapters.extractor.extract = gated_extract
            run_id = await source_run(app, http)
            try:
                await asyncio.wait_for(first_extraction.wait(), timeout=3)
                await eventually(lambda: app.state.adapters.audit.runtime_health() is not None)
                failed_health = await http.get("/api/health")
                assert failed_health.status_code == 200
                assert failed_health.json()["services"]["elastic"]["status"] == "error"
                assert remote.auth.call_count == 1  # The runtime error overrides the cached 200 immediately.
            finally:
                release.set()
            run = await app.state.run_manager.wait(run_id)
            assert run.lanes[0].status == "completed"
            assert (run.tallies.countersign.paid_count, run.tallies.countersign.held_count) == (4, 1)
            assert await app.state.adapters.audit.flush(run_id=run_id) is False
            converse = respx.post("https://kibana-integration.invalid/api/agent_builder/converse").mock(
                return_value=httpx.Response(200, json={"response": {"message": "must not be used"}, "steps": []}))
            unavailable = await http.post("/api/audit/ask", json={"question": "Which invoices were held?", "run_id": run_id})
            assert unavailable.status_code == 503 and converse.call_count == 0
            assert len(remote.bulk_attempts) >= 3
            events = app.state.store.list_events(run_id)
            failures = [event for event in events if event.type == "error" and event.lane == "system"]
            assert len(failures) == 1 and failures[0].payload.recoverable is True
            assert "Elastic" in failures[0].payload.message
            assert "upstream body" not in failures[0].payload.message
            assert app.state.store.audit_pending_count(run_id) > 0
            assert remote.accepted == []

            independent = Store(app.state.store.path)
            independent.initialize()
            try:
                assert independent.audit_pending_count(run_id) > 0
                assert independent.claim_audit_error(run_id) is False
            finally:
                independent.close()

            remote.bulk_healthy = True
            assert await app.state.adapters.audit.flush(run_id=run_id, refresh=True)
            assert_all_events_indexed_once(app, run_id, remote)
            healthy = await http.get("/api/health")
            assert healthy.json()["services"]["elastic"]["status"] == "ok"
            assert remote.auth.call_count == 1
            assert len([event for event in app.state.store.list_events(run_id)
                        if event.type == "error" and event.lane == "system"]) == 1


@pytest.mark.asyncio
@respx.mock
async def test_question_flushes_and_refreshes_before_delegating_to_agent_builder(tmp_path):
    remote = MockElastic()
    app = create_app(settings(), db_path=tmp_path / "audit-converse.db")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            run_id = await source_run(app, http)
            await app.state.run_manager.wait(run_id)
            held = next(row for row in app.state.store.list_records("decisions", run_id=run_id)
                        if row["outcome"] == "HOLD")
            query = 'FROM countersign-events | WHERE run_id == ?run_id AND outcome == "HOLD"'

            def converse(request):
                assert app.state.store.audit_pending_count(run_id) == 0
                assert remote.refresh.call_count >= 1
                body = json.loads(request.content)
                assert body["agent_id"] == "countersign-audit-analyst"
                assert json.loads(body["input"].split("\nAudit context: ", 1)[1]) == {"run_id": run_id}
                assert request.headers["Authorization"] == "ApiKey synthetic-elastic-key"
                assert request.headers["kbn-xsrf"] == "true"
                return httpx.Response(200, json={"conversation_id": "elastic-conversation", "response": {
                    "message": f"Invoice {held['invoice_id']} was held in run {run_id}."},
                    "steps": [{"type": "tool_call", "tool_id": "held_payments_by_reason", "results": [{
                        "type": "esql_results", "data": {"query": query,
                            "columns": [{"name": "invoice_id", "type": "keyword"}],
                            "values": [[held["invoice_id"]]]},
                    }]}]})

            actual = respx.post("https://kibana-integration.invalid/api/agent_builder/converse").mock(side_effect=converse)
            answer = await http.post("/api/audit/ask", json={"question": "Which invoice was held?", "run_id": run_id})
            assert answer.status_code == 200, answer.text
            result = answer.json()
            assert actual.call_count == 1
            assert result["answer"] == f"Invoice {held['invoice_id']} was held in run {run_id}."
            assert result["agent_id"] == "countersign-audit-analyst"
            assert result["conversation_id"] == "elastic-conversation"
            assert result["steps"] == [{"tool_id": "held_payments_by_reason", "esql": query, "row_count": 1}]


def test_source_event_and_outbox_roll_back_together_and_reset_never_reuses_an_elastic_id(tmp_path):
    store = Store(tmp_path / "audit-transaction.db")
    store.initialize()
    payload = {"type": "error", "lane": "system", "mode": "offline",
               "payload": {"message": "Synthetic source event", "recoverable": True}}
    try:
        with pytest.raises(RuntimeError, match="Synthetic rollback"):
            with store.transaction():
                aborted = store.append_event("run-recorded", payload)
                assert aborted.seq == 1 and store.audit_pending_count() == 1
                raise RuntimeError("Synthetic rollback")
        assert store.list_events("run-recorded") == []
        assert store.audit_pending_count() == 0
        first = store.append_event("run-recorded", payload)
        assert first.seq == 1
        original = store.pending_audit()[0]
        assert original["event_id"] == "run-recorded:1"
        store.reset_demo()
        assert store.list_events("run-recorded") == []
        assert store.pending_audit()[0] == original
        later = store.append_event("run-recorded", payload)
        assert later.seq == 2
        assert [row["event_id"] for row in store.pending_audit()] == ["run-recorded:1", "run-recorded:2"]
        store.acknowledge_audit([original["event_id"]])
        store.close()
        store.initialize()
        assert store.audit_pending_count() == 1
        assert store.backfill_audit_outbox() == 0
        assert [row["event_id"] for row in store.pending_audit()] == ["run-recorded:2"]
        assert store.pending_audit()[0]["document"]["seq"] == 2
    finally:
        store.close()
