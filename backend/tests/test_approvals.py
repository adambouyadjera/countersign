"""Actual approval HTTP routes, actor authorization, and durable once-only claims."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from app.adapters.bank import BankError
from app.config import Settings
from app.contract_models import Approval, PolicyDecision, Run
from app.main import create_app


@pytest_asyncio.fixture
async def approval_app(tmp_path):
    app = create_app(Settings(
        _env_file=None, extractor_mode="offline", planner_mode="offline", nessie_mode="offline", elastic_mode="offline",
        anthropic_api_key="", gemini_api_key="", nessie_api_key="", elastic_api_key="", bitdeer_api_key="",
    ), db_path=tmp_path / "approvals.sqlite")
    async with app.router.lifespan_context(app):
        store = app.state.store
        run = Run(run_id="run-held", batch_id="batch", instruction="trusted instruction",
                  created_at=datetime.now(timezone.utc), lanes=[{"lane": "countersign", "status": "completed"}])
        store.save_run(run)
        approval = Approval(
            approval_id="approval-held", run_id=run.run_id, invoice_id="invoice-held", invoice_number="INV-003",
            vendor_name="Golden Gate Sanitation", amount_cents=64000,
            requested_account_display="999900009999", account_on_file_display="300300003333",
            reason_codes=["BANK_DETAILS_MISMATCH"], status="pending", created_at=datetime.now(timezone.utc),
            requested_by="person-ap-agent", required_role="ap_manager",
        )
        store.save_record("approvals", approval.approval_id, approval)
        store.save_record("approval_contexts", approval.approval_id, {
            "approval_id": approval.approval_id, "run_id": run.run_id, "invoice_id": approval.invoice_id,
            "invoice_number": approval.invoice_number, "vendor_id": "VEN-003", "decision_id": "decision-held",
            "session_started_at": store.session_started_at.isoformat(), "mode": "offline",
        })
        decision = PolicyDecision(decision_id="decision-held", run_id=run.run_id, invoice_id=approval.invoice_id,
                                  outcome="HOLD", checks=[], reason_codes=approval.reason_codes, approval_id=approval.approval_id)
        store.save_record("decisions", decision.decision_id, decision)
        await app.state.event_bus.publish(run.run_id, {"type": "policy.decision", "lane": "countersign", "mode": "offline",
                                                       "payload": {"decision": decision.model_dump(mode="python")}})
        await app.state.event_bus.publish(run.run_id, {"type": "run.completed", "lane": "system", "mode": "offline",
                                                       "payload": {"tallies": {}}})
        app.state.adapters.bank.transfer = AsyncMock(wraps=app.state.adapters.bank.transfer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield app, client


def body(decision="pay_vendor_on_file", **extra):
    return {"decision": decision, "actor_id": "person-ap-manager", **extra}


@pytest.mark.asyncio
async def test_pay_vendor_on_file_resolves_into_original_completed_run_stream(approval_app):
    app, client = approval_app
    response = await client.post("/api/approvals/approval-held", json=body(note="Verified by phone"))
    assert response.status_code == 200, response.text
    approval = Approval.model_validate(response.json())
    assert approval.status == "resolved" and approval.resolution.transfer_id
    payment = app.state.store.get_record("transfers", approval.resolution.transfer_id)
    assert payment["to_display_number"] == "300300003333" and payment["payee_on_file"]
    assert payment["run_id"] == "run-held" and payment["invoice_id"] == "invoice-held"
    events = app.state.store.list_events("run-held")
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert events[1].type == "run.completed"
    assert {event.type for event in events[2:]} == {"approval.resolved", "payment.executed"}
    assert all(event.lane == "countersign" and event.mode == "offline" for event in events[2:])
    run = app.state.store.get_run("run-held")
    assert run.tallies.countersign.paid_count == 1 and run.tallies.countersign.held_count == 0
    assert (await client.get("/api/approvals?status=pending")).json() == []
    assert len((await client.get("/api/approvals?status=resolved")).json()) == 1
    again = await client.post("/api/approvals/approval-held", json=body())
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"
    assert app.state.adapters.bank.transfer.await_count == 1


@pytest.mark.asyncio
async def test_approve_new_account_requires_correct_last4_without_consuming_invalid_attempt(approval_app):
    app, client = approval_app
    for confirm in [None, "3333", "99999", " 9999"]:
        request = body("approve_new_account", **({"confirm_last4": confirm} if confirm is not None else {}))
        response = await client.post("/api/approvals/approval-held", json=request)
        assert response.status_code == 422
        assert app.state.store.get_record("approval_attempts", "approval-held") is None
    app.state.adapters.bank.transfer.assert_not_awaited()
    correct = await client.post("/api/approvals/approval-held", json=body("approve_new_account", confirm_last4="9999"))
    assert correct.status_code == 200, correct.text
    payment = app.state.store.get_record("transfers", correct.json()["resolution"]["transfer_id"])
    assert payment["to_display_number"] == "999900009999" and not payment["payee_on_file"]
    assert app.state.store.get_record("vendors", "VEN-003")["account_on_file_display"] == "300300003333"
    assert app.state.store.get_run("run-held").tallies.countersign.not_on_file_count == 1


@pytest.mark.asyncio
async def test_reject_resolves_without_a_bank_call_and_repeated_resolution_conflicts(approval_app):
    app, client = approval_app
    response = await client.post("/api/approvals/approval-held", json=body("reject", note="Declined"))
    assert response.status_code == 200
    assert "transfer_id" not in response.json()["resolution"]
    app.state.adapters.bank.transfer.assert_not_awaited()
    assert not app.state.store.has_session_payment("INV-003")
    assert app.state.store.get_run("run-held").tallies.countersign.blocked_count == 1
    assert (await client.post("/api/approvals/approval-held", json=body("reject"))).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["person-ap-agent", "person-vendor-admin", "missing"])
async def test_unauthorized_or_self_countersign_is_forbidden_without_claim(approval_app, actor):
    app, client = approval_app
    response = await client.post("/api/approvals/approval-held", json=body(actor_id=actor))
    assert response.status_code == 403 and response.json()["error"]["code"] == "sod_violation"
    app.state.adapters.bank.transfer.assert_not_awaited()
    assert app.state.store.get_record("approval_attempts", "approval-held") is None


@pytest.mark.asyncio
async def test_actor_limit_and_required_controller_role_are_enforced(approval_app):
    app, client = approval_app
    row = app.state.store.get_record("approvals", "approval-held")
    row["amount_cents"], row["required_role"] = 250000, "controller"
    app.state.store.save_record("approvals", "approval-held", row)
    assert (await client.post("/api/approvals/approval-held", json=body("reject"))).status_code == 403
    response = await client.post("/api/approvals/approval-held", json=body("reject", actor_id="person-controller"))
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_concurrent_resolution_submits_only_once(approval_app):
    app, client = approval_app
    first, second = await asyncio.gather(*(
        client.post("/api/approvals/approval-held", json=body()) for _ in range(2)
    ))
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert app.state.adapters.bank.transfer.await_count == 1


@pytest.mark.asyncio
async def test_uncertain_bank_failure_stays_pending_locked_and_sanitized(approval_app):
    app, client = approval_app
    app.state.adapters.bank.transfer = AsyncMock(side_effect=BankError("synthetic-private-provider-text"))
    first = await client.post("/api/approvals/approval-held", json=body())
    assert first.status_code == 503 and "synthetic-private" not in first.text
    assert app.state.store.get_record("approvals", "approval-held")["status"] == "pending"
    assert app.state.store.list_records("session_payment_claims")[0]["state"] == "uncertain"
    assert (await client.post("/api/approvals/approval-held", json=body())).status_code == 409
    assert app.state.adapters.bank.transfer.await_count == 1
    assert not any(event.type == "approval.resolved" for event in app.state.store.list_events("run-held"))


@pytest.mark.asyncio
async def test_stale_context_missing_actor_and_bad_filters_use_contract_errors(approval_app):
    app, client = approval_app
    assert (await client.post("/api/approvals/missing", json=body())).status_code == 404
    assert (await client.post("/api/approvals/approval-held", json={"decision": "reject"})).status_code == 422
    assert (await client.get("/api/approvals?status=invalid")).status_code == 422
    context = app.state.store.get_record("approval_contexts", "approval-held")
    context["session_started_at"] = "earlier-session"
    app.state.store.save_record("approval_contexts", "approval-held", context)
    assert (await client.post("/api/approvals/approval-held", json=body())).status_code == 409
    app.state.adapters.bank.transfer.assert_not_awaited()
    assert {item["person_id"] for item in (await client.get("/api/people")).json()} == {
        "person-ap-agent", "person-ap-manager", "person-controller", "person-vendor-admin",
    }


@pytest.mark.asyncio
async def test_independent_resolver_observes_durable_claim_while_bank_call_is_in_flight(approval_app):
    from fastapi import HTTPException
    from app.contract_models import ApprovalDecision
    from app.pipeline.approvals import ApprovalManager
    from app.store import Store

    app, client = approval_app
    entered, release = asyncio.Event(), asyncio.Event()
    original = app.state.adapters.bank.transfer

    async def blocked(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    app.state.adapters.bank.transfer = blocked
    first = asyncio.create_task(client.post("/api/approvals/approval-held", json=body()))
    await entered.wait()
    connection = Store(app.state.store.path)
    connection.initialize()
    try:
        second_resolver = ApprovalManager(connection, app.state.event_bus, app.state.payment_executor)
        with pytest.raises(HTTPException) as error:
            await second_resolver.resolve("approval-held", ApprovalDecision.model_validate(body()))
        assert error.value.status_code == 409
        release.set()
        assert (await first).status_code == 200
        assert original.await_count == 1
    finally:
        release.set()
        connection.close()
