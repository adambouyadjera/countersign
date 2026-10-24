"""Policy integration: source data and persistent session state decide payments."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.contract_models import RunRequest
from app.main import create_app
from app.world import batches as batches_module
from app.world.attacks import create_attack


def application(tmp_path):
    return create_app(Settings(_env_file=None, nessie_mode="offline", extractor_mode="offline",
        planner_mode="offline", elastic_mode="offline", anthropic_api_key="", gemini_api_key="",
        nessie_api_key="", elastic_api_key="", bitdeer_api_key=""), db_path=tmp_path / "policy-runs.db")


async def run(app, batch):
    initial = app.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["countersign"]))
    return await app.state.run_manager.wait(initial.run_id)


@pytest.mark.asyncio
async def test_amount_inflation_holds_with_amount_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(batches_module, "create_attack", lambda template, account: create_attack("amount_inflation", account))
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = batches_module.create_standard_batch(app.state.store)
        result = await run(app, batch)
        decisions = app.state.store.list_records("decisions", run_id=result.run_id)
        held = next(item for item in decisions if item["invoice_id"] == batch.invoices[2].invoice_id)
        assert held["outcome"] == "HOLD" and held["reason_codes"] == ["AMOUNT_MISMATCH"]
        assert result.tallies.countersign.paid_count == 4
        assert result.tallies.countersign.not_on_file_count == 0


@pytest.mark.asyncio
async def test_missing_instruction_detection_does_not_change_mismatched_account_hold(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = batches_module.create_standard_batch(app.state.store)
        original = app.state.adapters.extractor.extract

        async def no_detection(document):
            extraction = await original(document)
            extraction.instruction_like_spans = []
            return extraction

        app.state.adapters.extractor.extract = no_detection
        result = await run(app, batch)
        held = next(item for item in app.state.store.list_records("decisions", run_id=result.run_id)
                    if item["invoice_id"] == batch.invoices[2].invoice_id)
        assert held["outcome"] == "HOLD" and held["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
        assert next(check for check in held["checks"] if check["code"] == "INSTRUCTIONS_IN_DOCUMENT")["passed"]
        assert result.tallies.countersign.paid_count == 4


@pytest.mark.asyncio
async def test_concurrent_runs_cannot_pay_the_same_session_invoice_twice(tmp_path):
    app = application(tmp_path)
    async with app.router.lifespan_context(app):
        batch = batches_module.create_standard_batch(app.state.store)
        bank = app.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        first, second = await asyncio.gather(run(app, batch), run(app, batch))
        assert bank.transfer.await_count == 4
        assert first.tallies.countersign.paid_count + second.tallies.countersign.paid_count == 4
        decisions = [row for result in (first, second) for row in app.state.store.list_records("decisions", run_id=result.run_id)]
        blocked = [item for item in decisions if item["outcome"] == "BLOCK"]
        assert len(blocked) == 4
        assert all("DUPLICATE_INVOICE" in item["reason_codes"] for item in blocked)
        assert len([row for row in decisions if row["outcome"] == "HOLD"]) == 2
