"""POST /api/demo/reset gives a clean demo without erasing evidence, the cache or the books."""

import hashlib
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.world.batches import create_standard_batch

DOCS = Path(__file__).resolve().parents[1] / "docs"


def application(tmp_path):
    return create_app(Settings(
        _env_file=None, nessie_mode="offline", elastic_mode="offline", extractor_mode="offline",
        planner_mode="offline", agent_mode="offline", mutator_mode="offline",
        nessie_api_key="", elastic_api_key="", anthropic_api_key="", gemini_api_key="",
        bitdeer_api_key="", naive_api_key="", mutator_api_key="",
    ), db_path=tmp_path / "reset.sqlite")


def evidence_hashes():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in DOCS.glob("bank_change_live*.json")}


async def busy_session(app, client):
    """A two-lane run that leaves a pending hold, some inbox mail and posted books."""
    batch = create_standard_batch(app.state.store)
    started = await client.post("/api/runs", json={"batch_id": batch.batch_id, "lanes": ["unguarded", "countersign"]})
    assert started.status_code == 200, started.text
    run = await app.state.run_manager.wait(started.json()["run_id"])
    assert (await client.get("/api/approvals?status=pending")).json()
    await client.post("/api/inbox", json={"document_id": batch.invoices[0].document_id, "sender": "ap@example.com"})
    assert (await client.get("/api/inbox")).json()
    return run


@pytest.mark.asyncio
@pytest.mark.parametrize("autopilot", [True, False])
async def test_reset_clears_the_session_and_sets_autopilot_from_the_body(tmp_path, monkeypatch, autopilot):
    from app.security import extraction_cache

    def untouched(*args, **kwargs):
        raise AssertionError("demo reset must not touch the extraction cache")

    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            run = await busy_session(app, client)
            journal_before = [entry.entry_id for entry in app.state.ledger.list_entries()]
            assert journal_before
            hashes = evidence_hashes()
            for name in ("__init__", "get", "put"):
                monkeypatch.setattr(extraction_cache.ExtractionCache, name, untouched)

            response = await client.post("/api/demo/reset", json={"autopilot": autopilot})
            assert response.status_code == 200, response.text

            # The queue, inbox and runs are empty; the old run is gone.
            assert (await client.get("/api/approvals")).json() == []
            assert (await client.get("/api/approvals?status=pending")).json() == []
            assert (await client.get("/api/inbox")).json() == []
            assert (await client.get(f"/api/runs/{run.run_id}")).status_code == 404
            assert (await client.get(f"/api/runs/{run.run_id}/decisions")).status_code == 404
            # Reconciliation starts from a new baseline at zero.
            recon = (await client.get("/api/reconciliation")).json()
            assert recon["difference_cents"] == 0 and recon["items"] == []
            # Recorded evidence is still served and byte-identical.
            assert (await client.get("/api/evidence/bank-change")).status_code == 200
            assert (await client.get("/api/evidence/bank-change/default")).status_code == 200
            assert evidence_hashes() == hashes
            # The world is seeded again, with its January-June archive.
            vendor = (await client.get("/api/vendors/VEN-003")).json()
            assert any(p["journal_entry_id"].startswith("example-history-") for p in vendor["payments"])
            # The books are append-only and are kept (journal entries are never deleted).
            assert [entry.entry_id for entry in app.state.ledger.list_entries()] == journal_before
            # Autopilot matches the body.
            state = (await client.get("/api/autopilot")).json()
            assert state["enabled"] is autopilot
            if autopilot:
                assert state["lanes"] == ["countersign", "unguarded"] and state["run_id"]
            else:
                assert state["run_id"] is None


@pytest.mark.asyncio
async def test_reset_without_a_body_keeps_the_previous_behaviour_autopilot_off(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            enabled = await client.post("/api/autopilot", json={"enabled": True, "lanes": ["countersign", "unguarded"]})
            assert enabled.status_code == 200 and enabled.json()["enabled"] is True
            assert (await client.post("/api/demo/reset")).status_code == 200
            assert (await client.get("/api/autopilot")).json()["enabled"] is False


@pytest.mark.asyncio
async def test_reset_body_is_validated(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for body in ({"autopilot": "yes"}, {"autopilot": True, "extra": 1}):
                response = await client.post("/api/demo/reset", json=body)
                assert response.status_code == 422, (body, response.text)
                assert response.json()["error"]["code"] == "validation_error"
