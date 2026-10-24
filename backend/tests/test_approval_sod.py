"""Human authority is independent of who requested or verified a payment."""

import asyncio

import pytest

from tests.test_approvals import approval_app, body


def assert_forbidden_without_claim(app, response):
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "sod_violation"
    assert "approve instead" in error["message"]
    assert "controller" in error["message"] or "person-ap-manager" in error["message"]
    assert app.state.store.get_record("approval_attempts", "approval-held") is None
    app.state.adapters.bank.transfer.assert_not_awaited()


def update_approval(app, **fields):
    row = app.state.store.get_record("approvals", "approval-held")
    app.state.store.save_record("approvals", "approval-held", {**row, **fields})


async def verify_new_account(client):
    return await client.post("/api/vendors/VEN-003/bank_change", json={
        "actor_id": "person-vendor-admin", "new_account_display": "999900009999",
        "evidence_note": "Independently verified new remittance details by telephone.",
    })


def verify_from_another_worker(app, actor_id="person-vendor-admin"):
    from app.store import Store
    from app.world.vendors import record_bank_change

    connection = Store(app.state.store.path)
    connection.initialize()
    try:
        # Each worker owns a separate PaymentExecutor lock. Its master write
        # still shares SQLite with the paused worker's final authorization.
        return record_bank_change(connection, "VEN-003", actor_id, "999900009999",
            "Independently verified by another worker during the payment preflight.", bank_mode="offline")
    finally:
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["pay_vendor_on_file", "approve_new_account", "reject"])
@pytest.mark.parametrize("actor", ["person-ap-agent", "person-vendor-admin", "not-a-person"])
async def test_actor_role_required_for_every_resolution(approval_app, decision, actor):
    app, client = approval_app
    response = await client.post("/api/approvals/approval-held", json=body(
        decision, actor_id=actor, confirm_last4="9999"))
    assert_forbidden_without_claim(app, response)


@pytest.mark.asyncio
@pytest.mark.parametrize("requester", ["person-ap-manager", "person-controller"])
@pytest.mark.parametrize("decision", ["pay_vendor_on_file", "reject"])
async def test_requester_cannot_approve_even_with_an_eligible_role(approval_app, requester, decision):
    app, client = approval_app
    update_approval(app, requested_by=requester)
    response = await client.post("/api/approvals/approval-held", json=body(decision, actor_id=requester))
    assert_forbidden_without_claim(app, response)
    assert "requested this payment" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_vendor_admin_cannot_pay_even_with_an_unlimited_numeric_authority(approval_app):
    app, client = approval_app
    person = app.state.store.get_record("people", "person-vendor-admin")
    app.state.store.save_record("people", "person-vendor-admin", {**person, "approval_limit_cents": None})
    response = await client.post("/api/approvals/approval-held", json=body(actor_id="person-vendor-admin"))
    assert_forbidden_without_claim(app, response)


@pytest.mark.asyncio
@pytest.mark.parametrize("individual_limit", [5_000_000, 10_000_000, None])
async def test_manager_over_limit_names_independent_controller(approval_app, individual_limit):
    app, client = approval_app
    actor = app.state.store.get_record("people", "person-ap-manager")
    app.state.store.save_record("people", actor["person_id"], {**actor, "approval_limit_cents": individual_limit})
    update_approval(app, amount_cents=5_000_001)
    response = await client.post("/api/approvals/approval-held", json=body("reject"))
    assert_forbidden_without_claim(app, response)
    assert "approval limit" in response.json()["error"]["message"]
    assert "person-controller" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_manager_at_limit_and_controller_unlimited_can_resolve(approval_app):
    app, client = approval_app
    update_approval(app, amount_cents=5_000_000)
    response = await client.post("/api/approvals/approval-held", json=body("reject"))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "resolved"
    app.state.adapters.bank.transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_controller_can_resolve_above_manager_limit(approval_app):
    app, client = approval_app
    update_approval(app, amount_cents=50_000_000, required_role="controller")
    response = await client.post("/api/approvals/approval-held", json=body("reject", actor_id="person-controller"))
    assert response.status_code == 200, response.text
    app.state.adapters.bank.transfer.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_account_requires_prior_vendor_admin_verification(approval_app):
    app, client = approval_app
    response = await client.post("/api/approvals/approval-held", json=body("approve_new_account", confirm_last4="9999"))
    assert_forbidden_without_claim(app, response)
    assert "vendor_admin must first" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_changing_master_without_verification_audit_does_not_authorize_new_account(approval_app):
    app, client = approval_app
    vendor = app.state.store.get_record("vendors", "VEN-003")
    app.state.store.save_record("vendors", "VEN-003", {**vendor,
        "account_on_file_display": "999900009999", "remit_account_number": "999900009999"})
    response = await client.post("/api/approvals/approval-held", json=body("approve_new_account", confirm_last4="9999"))
    assert_forbidden_without_claim(app, response)


@pytest.mark.asyncio
async def test_verified_new_account_requires_a_different_payment_approver(approval_app):
    app, client = approval_app
    verified = await verify_new_account(client)
    assert verified.status_code == 200, verified.text
    response = await client.post("/api/approvals/approval-held", json=body("approve_new_account", confirm_last4="9999"))
    assert response.status_code == 200, response.text
    transfer = app.state.store.get_record("transfers", response.json()["resolution"]["transfer_id"])
    assert transfer["to_display_number"] == "999900009999"
    assert transfer["payee_on_file"] is True
    assert transfer["created_by"] == "person-ap-manager"
    assert transfer["approval_id"] == "approval-held"
    assert app.state.adapters.bank.transfer.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve_new_account", "pay_vendor_on_file"])
async def test_account_verifier_cannot_pay_after_a_role_change_or_on_file_shortcut(approval_app, decision):
    app, client = approval_app
    verified = await verify_new_account(client)
    assert verified.status_code == 200, verified.text
    # Role administration is not exposed by this API. Model a trusted role
    # change to ensure the historical verifier identity still prevents self-pay.
    actor = app.state.store.get_record("people", "person-vendor-admin")
    app.state.store.save_record("people", actor["person_id"], {**actor,
        "role": "controller", "approval_limit_cents": None})
    response = await client.post("/api/approvals/approval-held", json=body(
        decision, actor_id=actor["person_id"], confirm_last4="9999"))
    assert_forbidden_without_claim(app, response)
    assert "verified this account" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_malformed_actor_body_still_uses_the_required_contract_field(approval_app):
    app, client = approval_app
    response = await client.post("/api/approvals/approval-held", json={"decision": "reject"})
    assert response.status_code == 422
    app.state.adapters.bank.transfer.assert_not_awaited()


def pause_before_payment(app):
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause():
        entered.set()
        await release.wait()

    app.state.event_bus.before_payment = pause
    return entered, release


def assert_unsubmitted_denial(app, response, status):
    assert response.status_code == status, response.text
    assert app.state.store.get_record("approvals", "approval-held")["status"] == "pending"
    assert app.state.store.get_record("approval_attempts", "approval-held") is None
    assert not app.state.store.has_session_payment("INV-003")
    assert app.state.ledger.list_entries(run_id="run-held") == []
    app.state.adapters.bank.transfer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "requester", "limit", "amount"])
async def test_authority_and_amount_are_rechecked_after_pretransfer_await(approval_app, change):
    app, client = approval_app
    entered, release = pause_before_payment(app)
    task = asyncio.create_task(client.post("/api/approvals/approval-held", json=body()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        if change in {"role", "limit"}:
            actor = app.state.store.get_record("people", "person-ap-manager")
            replacement = {"role": "vendor_admin"} if change == "role" else {"approval_limit_cents": 1}
            app.state.store.save_record("people", actor["person_id"], {**actor, **replacement})
        elif change == "requester":
            update_approval(app, requested_by="person-ap-manager")
        else:
            update_approval(app, amount_cents=65000)
        release.set()
        response = await task
        assert_unsubmitted_denial(app, response, 409 if change == "amount" else 403)
    finally:
        release.set()
        if not task.done():
            await task


@pytest.mark.asyncio
async def test_active_verifier_is_rechecked_after_pretransfer_await(approval_app):
    app, client = approval_app
    verified = await verify_new_account(client)
    assert verified.status_code == 200, verified.text
    entered, release = pause_before_payment(app)
    task = asyncio.create_task(client.post("/api/approvals/approval-held", json=body(
        "approve_new_account", confirm_last4="9999")))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        actor = app.state.store.get_record("people", "person-ap-manager")
        app.state.store.save_record("people", actor["person_id"], {**actor, "role": "vendor_admin"})
        changed = verify_from_another_worker(app, actor["person_id"])
        assert changed.account_on_file_display == "999900009999"
        app.state.store.save_record("people", actor["person_id"], actor)
        release.set()
        response = await task
        assert_unsubmitted_denial(app, response, 403)
        assert "verified this account" in response.json()["error"]["message"]
    finally:
        release.set()
        if not task.done():
            await task


@pytest.mark.asyncio
async def test_master_account_change_during_await_requires_a_fresh_approval(approval_app):
    app, client = approval_app
    entered, release = pause_before_payment(app)
    task = asyncio.create_task(client.post("/api/approvals/approval-held", json=body()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        changed = verify_from_another_worker(app)
        assert changed.account_on_file_display == "999900009999"
        release.set()
        response = await task
        assert_unsubmitted_denial(app, response, 409)
        assert "account changed" in response.json()["error"]["message"]
    finally:
        release.set()
        if not task.done():
            await task
