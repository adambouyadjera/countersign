"""Only deterministic policy authorization can execute a Countersign payment."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
from pydantic import TypeAdapter
import pytest

from app.adapters.bank import BankError
from app.config import Settings
from app.contract_models import Run, RunEvent, RunRequest
from app.main import create_app
from app.pipeline.extractor import ExtractorError
from app.world.batches import create_standard_batch


@pytest.fixture
def application(tmp_path):
    return create_app(Settings(
        _env_file=None, nessie_mode="offline", extractor_mode="offline", planner_mode="offline", agent_mode="offline", elastic_mode="offline",
        nessie_api_key="", gemini_api_key="", anthropic_api_key="", elastic_api_key="", bitdeer_api_key="",
    ), db_path=tmp_path / "runs.db")


async def request_run(app, batch, lanes=None, **extra):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/runs", json={"batch_id": batch.batch_id, "lanes": lanes or ["countersign"], **extra})
        assert response.status_code == 200, response.text
        initial = Run.model_validate(response.json())
        final = await app.state.run_manager.wait(initial.run_id)
        assert (await client.get(f"/api/runs/{initial.run_id}")).json() == final.model_dump(mode="json")
        return initial, final


@pytest.mark.asyncio
async def test_standard_run_four_allows_one_account_mismatch_hold(application):
    async with application.router.lifespan_context(application):
        store = application.state.store
        batch = create_standard_batch(store)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        original_get, original_list = store.get_record, store.list_records

        def guarded_get(table, *args, **kwargs):
            assert table != "invoice_scoring", "Run execution must never read private scoring metadata"
            return original_get(table, *args, **kwargs)

        def guarded_list(table, *args, **kwargs):
            assert table != "invoice_scoring", "Run execution must never read private scoring metadata"
            return original_list(table, *args, **kwargs)

        store.get_record, store.list_records = guarded_get, guarded_list
        initial, final = await request_run(application, batch, lanes=["countersign"])
        assert {lane.lane: lane.status for lane in initial.lanes} == {"countersign": "running"}
        assert {lane.lane: lane.status for lane in final.lanes} == {"countersign": "completed"}
        tally = final.tallies.countersign
        assert (tally.paid_count, tally.held_count, tally.blocked_count) == (4, 1, 0)
        assert tally.paid_cents == 270000
        assert bank.transfer.await_count == 4
        events = store.list_events(final.run_id)
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        assert events[0].type == "run.started" and events[-1].type == "run.completed"
        assert sum(event.type == "document.loaded" for event in events) == 5
        assert sum(event.type == "fields.extracted" for event in events) == 5
        assert sum(event.type == "payment.executed" for event in events) == 4
        assert sum(event.type == "plan.proposed" for event in events) == 1
        proposed = next(event.payload for event in events if event.type == "plan.proposed")
        assert {step.invoice_id for step in proposed.steps} == {invoice.invoice_id for invoice in batch.invoices}
        held = [event for event in events if event.type == "payment.held"]
        assert len(held) == 1
        assert held[0].payload.reason_codes == ["BANK_DETAILS_MISMATCH"]
        assert held[0].payload.invoice_id == batch.invoices[2].invoice_id
        for event in events:
            assert event.mode == "offline"
            TypeAdapter(RunEvent).validate_json(event.model_dump_json())
        decisions = store.list_records("decisions", run_id=final.run_id)
        approvals = store.list_records("approvals", run_id=final.run_id)
        assert len(decisions) == 5 and len(approvals) == 1
        assert sum(record["outcome"] == "ALLOW" for record in decisions) == 4
        assert sum(record["outcome"] == "HOLD" for record in decisions) == 1
        assert len(store.list_records("extractions")) == 5
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            assert len((await client.get(f"/api/runs/{final.run_id}/decisions")).json()) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["false_span", "fabricated_value"])
async def test_unverified_or_ungrounded_payment_field_never_reaches_bank(application, tamper):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        extractor = application.state.adapters.extractor
        original = extractor.extract
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        if tamper == "fabricated_value":
            po = application.state.store.get_record("purchase_orders", "PO-001")
            po["amount_cents"] = 125100
            application.state.store.save_record("purchase_orders", "PO-001", po)

        async def altered(document):
            payload = await original(document)
            if document.invoice_id == batch.invoices[0].invoice_id:
                amount = next(field for field in payload.fields if field.name == "amount_due_cents")
                if tamper == "false_span":
                    amount.span.span_verified = False
                else:
                    # The source quote stays real, but its amount no longer
                    # supports the model's value even though that matches PO.
                    amount.value = 125100
            return payload

        extractor.extract = altered
        _, final = await request_run(application, batch)
        assert bank.transfer.await_count == 3
        assert final.tallies.countersign.held_count == 2
        held = [event for event in application.state.store.list_events(final.run_id) if event.type == "payment.held"]
        bad = next(event for event in held if event.payload.invoice_id == batch.invoices[0].invoice_id)
        assert "UNVERIFIED_SOURCE" in bad.payload.reason_codes
        bad_decision = next(row for row in application.state.store.list_records("decisions", run_id=final.run_id) if row["invoice_id"] == batch.invoices[0].invoice_id)
        assert not next(check for check in bad_decision["checks"] if check["code"] == "SPAN_VERIFIED")["passed"]
        error = next(event for event in application.state.store.list_events(final.run_id)
                     if event.type == "error" and event.payload.invoice_id == batch.invoices[0].invoice_id)
        assert error.payload.message == "payment source verification failed (span_verification)"


@pytest.mark.asyncio
async def test_unguarded_only_request_respects_requested_lane(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        _, final = await request_run(application, batch, lanes=["unguarded"])
        assert {lane.lane: lane.status for lane in final.lanes} == {"unguarded": "completed"}
        assert final.tallies.countersign is None
        assert final.tallies.unguarded.paid_count == 5


@pytest.mark.asyncio
async def test_custom_instruction_is_held_instead_of_silently_ignored(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        _, final = await request_run(application, batch, instruction="Do not pay any invoice.")
        assert bank.transfer.await_count == 0
        assert final.tallies.countersign.held_count == 5
        assert all("POLICY_NOT_IMPLEMENTED" in record["reason_codes"] for record in application.state.store.list_records("approvals", run_id=final.run_id))


@pytest.mark.asyncio
async def test_bank_failure_holds_without_fabricated_success(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(side_effect=BankError("private-bank-response-must-not-escape"))
        _, final = await request_run(application, batch)
        assert bank.transfer.await_count == 4
        assert final.lanes[0].status == "failed"
        assert final.tallies.countersign.held_count == 5
        events = application.state.store.list_events(final.run_id)
        assert not any(event.type == "payment.executed" for event in events)
        assert "private-bank-response" not in json.dumps([event.model_dump(mode="json") for event in events])
        assert events[-1].type == "run.completed"


def original_approval(application, approval_id):
    return application.state.store.get_record("approvals", approval_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind,expected", [
    ("timeout", "extraction timed out after 45 s (gemini)"),
    ("schema", "invoice response failed schema validation (schema)"),
    ("extractor_schema", "extraction response failed schema validation (schema)"),
    ("document", "invoice response failed schema validation (pdf_text)"),
    ("unknown", "invoice processing failed (gemini: RuntimeError)"),
])
async def test_failed_extraction_emits_stage_error_and_persisted_hold_then_continues(application, failure_kind, expected, caplog):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        extractor = application.state.adapters.extractor
        original = extractor.extract
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        failed_invoice = batch.invoices[0].invoice_id
        if failure_kind == "document":
            record = application.state.store.get_record("documents", batch.invoices[0].document_id)
            record["text"] = {"private": "secret-should-not-leak"}
            application.state.store.save_record("documents", batch.invoices[0].document_id, record)

        async def fail_once(document):
            if document.invoice_id == failed_invoice:
                if failure_kind == "timeout":
                    raise TimeoutError("https://service.invalid?key=secret-should-not-leak")
                if failure_kind == "schema":
                    return {"invoice_id": failed_invoice, "private": "secret-should-not-leak"}
                if failure_kind == "extractor_schema":
                    raise ExtractorError("extraction response failed schema validation (schema)", stage="schema")
                raise RuntimeError("secret-should-not-leak")
            return await original(document)

        extractor.extract = fail_once
        _, final = await request_run(application, batch)
        events = application.state.store.list_events(final.run_id)
        errors = [event for event in events if event.type == "error"]
        assert len(errors) == 1
        assert errors[0].payload.message == expected
        assert errors[0].payload.invoice_id == failed_invoice
        failure_events = [event for event in events if getattr(event.payload, "invoice_id", None) == failed_invoice]
        held = next(event for event in failure_events if event.type == "payment.held")
        assert errors[0].seq < held.seq
        assert held.payload.reason_codes == ["UNVERIFIED_SOURCE"]
        assert original_approval(application, held.payload.approval_id)["invoice_id"] == failed_invoice
        assert not any(event.type == "payment.executed" for event in failure_events)
        assert bank.transfer.await_count == 3
        assert final.lanes[0].status == "failed"
        assert (final.tallies.countersign.paid_count, final.tallies.countersign.held_count,
                final.tallies.countersign.blocked_count) == (3, 2, 0)
        assert events[-1].type == "run.completed"
        serialized = json.dumps([event.model_dump(mode="json") for event in events])
        assert "secret-should-not-leak" not in serialized
        assert "secret-should-not-leak" not in caplog.text


@pytest.mark.asyncio
async def test_safe_extractor_rate_limit_summary_and_stage_reach_error_event(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        extractor = application.state.adapters.extractor
        original = extractor.extract
        failed_invoice = batch.invoices[0].invoice_id

        async def rate_limited(document):
            if document.invoice_id == failed_invoice:
                raise ExtractorError(
                    "extraction rate limited after 3 attempts (gemini, HTTP 429)",
                    stage="gemini", http_status=429, retryable=True, attempts=3,
                )
            return await original(document)

        extractor.extract = rate_limited
        _, final = await request_run(application, batch)
        events = application.state.store.list_events(final.run_id)
        error = next(event for event in events if event.type == "error")
        assert error.payload.message == "extraction rate limited after 3 attempts (gemini, HTTP 429)"
        held = next(event for event in events if event.type == "payment.held" and event.payload.invoice_id == failed_invoice)
        assert held.payload.reason_codes == ["UNVERIFIED_SOURCE"]


@pytest.mark.asyncio
async def test_configured_credential_is_redacted_if_an_extractor_summary_regresses(application, caplog):
    from pydantic import SecretStr

    async with application.router.lifespan_context(application):
        synthetic_key = "synthetic-configured-key-should-not-escape"
        application.state.settings.gemini_api_key = SecretStr(synthetic_key)
        batch = create_standard_batch(application.state.store)
        extractor = application.state.adapters.extractor
        original = extractor.extract
        failed_invoice = batch.invoices[0].invoice_id

        async def regressed_summary(document):
            if document.invoice_id == failed_invoice:
                raise ExtractorError(f"extraction failed for credential {synthetic_key} (gemini)")
            return await original(document)

        extractor.extract = regressed_summary
        _, final = await request_run(application, batch)
        events = application.state.store.list_events(final.run_id)
        error = next(event for event in events if event.type == "error")
        assert "[REDACTED]" in error.payload.message
        assert synthetic_key not in json.dumps([event.model_dump(mode="json") for event in events])
        assert synthetic_key not in caplog.text


@pytest.mark.asyncio
async def test_close_cancels_an_active_run_before_store_shutdown(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        entered = asyncio.Event()

        async def blocked_extractor(document):
            entered.set()
            await asyncio.Future()

        application.state.adapters.extractor.extract = blocked_extractor
        initial = application.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["countersign"]))
        await asyncio.wait_for(entered.wait(), 2)
        await application.state.run_manager.close()
        final = application.state.store.get_run(initial.run_id)
        assert final.lanes[0].status == "failed"
        assert application.state.store.list_events(initial.run_id)[-1].type == "run.completed"


@pytest.mark.asyncio
async def test_close_before_task_starts_does_not_leave_run_running(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        initial = application.state.run_manager.start(RunRequest(batch_id=batch.batch_id, lanes=["countersign"]))
        # No event-loop yield occurs between acceptance and close.
        await application.state.run_manager.close()
        final = application.state.store.get_run(initial.run_id)
        assert final.lanes[0].status == "failed"
        assert final.tallies.countersign.paid_count == 0
        events = application.state.store.list_events(initial.run_id)
        assert events[0].type == "run.started" and events[-1].type == "run.completed"


@pytest.mark.asyncio
async def test_run_endpoint_validation_and_missing_records_use_contract_errors(application):
    async with application.router.lifespan_context(application):
        batch = create_standard_batch(application.state.store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            for body, status in [
                ({"batch_id": "missing", "lanes": ["countersign"]}, 404),
                ({"batch_id": batch.batch_id, "lanes": []}, 422),
                ({"batch_id": batch.batch_id, "lanes": ["countersign", "countersign"]}, 422),
                ({"batch_id": batch.batch_id, "lanes": ["unknown"]}, 422),
            ]:
                response = await client.post("/api/runs", json=body)
                assert response.status_code == status
                assert response.json()["error"]["code"] == ("not_found" if status == 404 else "validation_error")
            assert (await client.get("/api/runs/missing")).status_code == 404
            assert (await client.get("/api/runs/missing/decisions")).status_code == 404
            assert (await client.get("/api/runs/missing/planner_transcript")).status_code == 404
