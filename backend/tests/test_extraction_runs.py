"""Extraction origin and cached status survive the real run/event/graph path."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.contract_models import DocumentText, ProvenanceGraph
from app.main import create_app
from app.pipeline.extractor import ExtractorError
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload
from app.security.extraction_cache import ExtractionCache


def application(tmp_path):
    return create_app(Settings(_env_file=None, nessie_mode="offline", elastic_mode="offline",
        extractor_mode="live", extractor_model="gemini-run-test", extractor_fallback_model="haiku-run-test",
        extractor_cache="off", planner_mode="offline", agent_mode="offline", mutator_mode="offline",
        nessie_api_key="", gemini_api_key="synthetic-gemini", anthropic_api_key="synthetic-anthropic",
        elastic_api_key="", bitdeer_api_key=""), db_path=tmp_path / "extraction-runs.db")


def fake_chain(app, *, all_fail=False):
    """Replace only external extraction calls; keep the production wrapper."""
    service = app.state.adapters.extractor
    order = []

    async def primary(document):
        order.append((document.invoice_id, "gemini"))
        raise ExtractorError("Synthetic exhausted Gemini quota", http_status=429,
                             cause="rate_limit", attempts=3)

    async def fallback(document):
        order.append((document.invoice_id, "anthropic"))
        if all_fail:
            raise ExtractorError("Synthetic unavailable Haiku", stage="haiku", http_status=503,
                                 cause="request", attempts=3)
        return to_payload(document, parse_offline(document), service.fallback.model)

    service.primary.extract = AsyncMock(side_effect=primary)
    service.fallback.extract = AsyncMock(side_effect=fallback)
    return service, order


async def start_and_wait(app, client, batch):
    response = await client.post("/api/runs", json={"batch_id": batch["batch_id"], "lanes": ["countersign"]})
    assert response.status_code == 200, response.text
    return await app.state.run_manager.wait(response.json()["run_id"])


async def assert_letter_hold_and_graph(client, run_id, batch):
    invoice_id = batch["supporting_documents"][0]["invoice_id"]
    decisions = (await client.get(f"/api/runs/{run_id}/decisions")).json()
    assert sum(item["outcome"] == "ALLOW" for item in decisions) == 4
    held = next(item for item in decisions if item["invoice_id"] == invoice_id)
    assert held["outcome"] == "HOLD" and held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
    response = await client.get(f"/api/runs/{run_id}/invoices/{invoice_id}/graph")
    assert response.status_code == 200, response.text
    graph = ProvenanceGraph.model_validate(response.json())
    action = next(node for node in graph.nodes if node.kind == "action")
    assert action.status == "held" and "transfer_id" not in action.detail
    check = next(node for node in graph.nodes if node.kind == "policy_check" and node.label == "PAYEE_ON_FILE")
    assert check.status == "failed"
    return graph


@pytest.mark.asyncio
async def test_fallback_origin_reaches_events_graph_and_real_policy_gate(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        service, order = fake_chain(app)
        bank = app.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "bank_change"})).json()
            run = await start_and_wait(app, client, batch)
            assert [(lane.lane, lane.status) for lane in run.lanes] == [("countersign", "completed")]
            assert run.tallies.countersign.paid_count == 4 and run.tallies.countersign.held_count == 1
            assert bank.transfer.await_count == 4
            assert order == [(invoice["invoice_id"], provider) for invoice in batch["invoices"]
                             for provider in ("gemini", "anthropic")]
            extracted = [event for event in app.state.store.list_events(run.run_id) if event.type == "fields.extracted"]
            assert len(extracted) == 5
            for event in extracted:
                assert event.lane == "countersign" and event.provider == "anthropic"
                assert event.model == service.fallback.model == event.payload.extractor_model
                origin = event.payload.extraction_provenance
                assert origin.provider == "anthropic" and origin.model == service.fallback.model
                assert not origin.cached
                assert [(attempt.provider, attempt.outcome) for attempt in origin.attempts] == [
                    ("gemini", "failed"), ("anthropic", "success")]
            graph = await assert_letter_hold_and_graph(client, run.run_id, batch)
            operation = next(node for node in graph.nodes if node.detail
                             and node.detail.get("operation") == "quarantined_extraction")
            assert operation.detail["provider"] == "anthropic" and operation.detail["model"] == service.fallback.model
            assert operation.detail["cached"] is False and "fresh" in operation.label
            assert any(node.kind == "field" and node.detail.get("extraction_provenance", {}).get("model")
                       == service.fallback.model for node in graph.nodes)


@pytest.mark.asyncio
async def test_warmed_cache_is_labelled_cached_in_run_events_and_graph(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        service, order = fake_chain(app)
        service.cache = ExtractionCache(tmp_path / "test-extraction-cache.db")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "bank_change"})).json()
            original_times = {}
            for invoice in batch["invoices"]:
                record = app.state.store.get_record("documents", invoice["document_id"])
                document = DocumentText.model_validate(record["text"])
                warmed = await service.extract(document)
                assert not warmed.extraction_provenance.cached
                original_times[invoice["invoice_id"]] = warmed.extraction_provenance.extracted_at
            recorded_order = list(order)
            assert len(recorded_order) == 10
            assert app.state.store.list_records("transfers") == []
            run = await start_and_wait(app, client, batch)
            assert order == recorded_order, "Cached run must not call either extractor model"
            assert service.primary.extract.await_count == service.fallback.extract.await_count == 5
            assert run.lanes[0].status == "completed"
            events = [event for event in app.state.store.list_events(run.run_id) if event.type == "fields.extracted"]
            assert len(events) == 5
            for event in events:
                origin = event.payload.extraction_provenance
                assert event.provider == origin.provider == "anthropic"
                assert event.model == origin.model == service.fallback.model
                assert origin.cached is True and origin.extracted_at == original_times[event.payload.invoice_id]
                assert origin.attempts in (None, []), "A hit must not claim a new model attempt"
                response = await client.get(f"/api/runs/{run.run_id}/invoices/{event.payload.invoice_id}/graph")
                assert response.status_code == 200, response.text
                graph = ProvenanceGraph.model_validate(response.json())
                operation = next(node for node in graph.nodes if node.detail
                                 and node.detail.get("operation") == "quarantined_extraction")
                assert operation.detail["cached"] is True and "cached" in operation.label
                assert operation.detail["provider"] == "anthropic" and operation.detail["model"] == service.fallback.model
                assert operation.detail["extracted_at"] == origin.model_dump(mode="json")["extracted_at"]
            await assert_letter_hold_and_graph(client, run.run_id, batch)


@pytest.mark.asyncio
async def test_all_extraction_providers_fail_holds_every_invoice_without_transfers(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        service, order = fake_chain(app, all_fail=True)
        bank = app.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "bank_change"})).json()
            run = await start_and_wait(app, client, batch)
            assert run.lanes[0].status == "failed"
            assert run.tallies.countersign.held_count == 5 and run.tallies.countersign.paid_count == 0
            bank.transfer.assert_not_awaited()
            assert app.state.store.list_records("transfers") == []
            decisions = (await client.get(f"/api/runs/{run.run_id}/decisions")).json()
            assert len(decisions) == 5
            assert all(item["outcome"] == "HOLD" and item["reason_codes"] == ["UNVERIFIED_SOURCE"] for item in decisions)
            assert order == [(invoice["invoice_id"], provider) for invoice in batch["invoices"]
                             for provider in ("gemini", "anthropic")]
            events = app.state.store.list_events(run.run_id)
            assert not any(event.type in ("fields.extracted", "payment.executed") for event in events)
            for invoice in batch["invoices"]:
                response = await client.get(f"/api/runs/{run.run_id}/invoices/{invoice['invoice_id']}/graph")
                assert response.status_code == 200, response.text
                graph = ProvenanceGraph.model_validate(response.json())
                assert next(node.status for node in graph.nodes if node.kind == "action") == "held"
                assert next(node.status for node in graph.nodes if node.kind == "policy_check"
                            and node.label == "SPAN_VERIFIED") == "failed"
