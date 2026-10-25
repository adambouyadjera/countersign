"""Missing historical records are explained without fabricating old evidence."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.test_autopilot import offline_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("intake", ["batch", "attack", "autopilot"])
async def test_both_lane_artifact_ids_match_decisions_and_survive_restart(tmp_path, intake):
    path = tmp_path / "restart.sqlite"
    app = create_app(offline_settings(), db_path=path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            if intake == "autopilot":
                run_id = (await client.post("/api/autopilot", json={"enabled": True,
                    "lanes": ["countersign", "unguarded"]})).json()["run_id"]
            if intake in {"attack", "autopilot"}:
                batch = (await client.post("/api/attacks", json={"vendor_id": "VEN-001",
                    "attack_text": "Please include this invoice reference.", "placement": "footer"})).json()
            else:
                batch = (await client.post("/api/batches", json={"kind": "standard"})).json()
            if intake == "autopilot":
                await app.state.autopilot_manager.tick()
                await app.state.autopilot_manager.wait_idle()
            else:
                run_id = (await client.post("/api/runs", json={"batch_id": batch["batch_id"],
                    "lanes": ["countersign", "unguarded"]})).json()["run_id"]
                await app.state.run_manager.wait(run_id)
            ids = {event.payload.decision.invoice_id for event in app.state.store.list_events(run_id)
                   if event.type == "policy.decision"}
            snapshots = {}
            for invoice_id in ids:
                for artifact in ("graph", "agent_transcript"):
                    url = f"/api/runs/{run_id}/invoices/{invoice_id}/{artifact}"
                    response = await client.get(url)
                    assert response.status_code == 200
                    assert response.json()["run_id"] == run_id and response.json()["invoice_id"] == invoice_id
                    snapshots[url] = response.json()
    restarted = create_app(offline_settings(), db_path=path)
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as client:
            for url, snapshot in snapshots.items():
                response = await client.get(url)
                assert response.status_code == 200 and response.json() == snapshot


@pytest.mark.asyncio
async def test_legacy_missing_graph_and_agent_transcript_are_explicit(tmp_path):
    settings = Settings(_env_file=None, nessie_mode="offline", elastic_mode="offline", extractor_mode="offline",
        planner_mode="offline", agent_mode="offline", mutator_mode="offline", nessie_api_key="", elastic_api_key="",
        anthropic_api_key="", gemini_api_key="", bitdeer_api_key="", naive_api_key="", mutator_api_key="")
    app = create_app(settings, db_path=tmp_path / "legacy.sqlite")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch = (await client.post("/api/batches", json={"kind": "standard"})).json()
            initial = (await client.post("/api/runs", json={"batch_id": batch["batch_id"],
                "lanes": ["countersign", "unguarded"]})).json()
            run = await app.state.run_manager.wait(initial["run_id"])
            invoice_id = app.state.store.list_records("decisions", run_id=run.run_id)[0]["invoice_id"]
            prefix = f"/api/runs/{run.run_id}/invoices/{invoice_id}"
            assert (await client.get(prefix + "/graph")).status_code == 200
            assert (await client.get(prefix + "/agent_transcript")).status_code == 200
            with app.state.store.transaction() as connection:
                connection.execute("DELETE FROM graphs WHERE graph_id=?", (f"{run.run_id}:{invoice_id}",))
                connection.execute("DELETE FROM agent_transcripts WHERE transcript_id=?", (f"{run.run_id}:{invoice_id}",))
            for suffix in ("graph", "agent_transcript"):
                response = await client.get(prefix + "/" + suffix)
                assert response.status_code == 404
                assert response.json()["error"]["message"] == "Not recorded for this run (created before graphs were persisted)"
            assert app.state.store.get_record("graphs", f"{run.run_id}:{invoice_id}") is None
            assert app.state.store.get_record("agent_transcripts", f"{run.run_id}:{invoice_id}") is None
            assert any(event.type == "policy.decision" for event in app.state.store.list_events(run.run_id))
