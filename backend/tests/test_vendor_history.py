"""Trusted vendor history survives bank changes without pretending seeds are bank calls."""

from dataclasses import replace

from fastapi import HTTPException
import pytest

from app.security.labels import Labeled
from app.world.people import seed_people
from app.world.seed import seed_trusted_world
from app.world.vendors import (
    history_check, latest_account_verification, record_bank_change,
    record_vendor_payment, seed_vendor_history, vendor_detail,
)
from app.store import Store
from tests.test_policy import world, evaluate, check


@pytest.fixture
def store():
    store = Store(":memory:")
    store.initialize()
    seed_trusted_world(store)
    seed_people(store)
    seed_vendor_history(store)
    store.save_record("bank_directory", "999900009999", {
        "display_number": "999900009999", "account_id": "example-attacker-account",
        "mode": "offline", "role": "attacker", "label": "Northwind Remittance Services",
    })
    try:
        yield store
    finally:
        store.close()


def change(store, *, actor="person-vendor-admin", account="999900009999", note="Verified by callback to the known vendor contact."):
    return record_bank_change(store, "VEN-001", actor, account, note, bank_mode="offline")


def test_vendor_detail_has_months_of_labelled_archived_history_without_fake_transfers(store):
    detail = vendor_detail(store, "VEN-001")
    assert detail.account_on_file_display == "100100001111"
    assert len(detail.payments) == 6
    assert [item.date.month for item in detail.payments] == [1, 2, 3, 4, 5, 6]
    assert all(item.journal_entry_id.startswith("example-history-") for item in detail.payments)
    assert detail.account_history[0].verified_by == "person-vendor-admin"
    assert detail.account_history[0].verified_at.date().isoformat() == "2026-03-12"
    raw = store.get_record("vendor_history", "VEN-001")
    assert raw["source"] == "demo_historical_seed"
    assert "not live Nessie transfers" in raw["archive_note"]
    assert store.list_records("transfers") == []


def test_history_pass_and_mismatch_cite_actual_account_count_dates_and_verifier(store):
    vendor = store.get_record("vendors", "VEN-001")
    passed, message = history_check(store, vendor, "100100001111")
    assert passed is True
    for text in ("Paid 6 times", "January 2026", "ending 1111", "Morgan Diaz", "2026-03-12"):
        assert text in message
    passed, message = history_check(store, vendor, "999900009999")
    assert passed is False
    assert "Paid 6 times" in message
    assert "human countersign" in message


def test_policy_history_passes_for_the_account_previously_paid(world):
    result = evaluate(world)
    assert check(result, "VENDOR_HISTORY").passed
    assert result.outcome == "ALLOW"


def test_policy_history_fails_for_document_account_and_preserves_history_evidence(world):
    document = world["store"].get_record("documents", "doc-1")
    document["text"]["pages"][0]["lines"][4]["text"] = "Account number: 999900009999"
    world["store"].save_record("documents", "doc-1", document)
    account = Labeled("999900009999", world["intent"].to_account.sources)
    result = evaluate(world, intent=replace(world["intent"], to_account=account))
    assert result.outcome == "HOLD" and result.reason_codes == ["BANK_DETAILS_MISMATCH"]
    history = check(result, "VENDOR_HISTORY")
    assert not history.passed and history.evidence_node_ids
    assert "Paid 6 times since January 2026" in history.message
    assert "ending 1111" in check(result, "PAYEE_ON_FILE").message


def test_admin_bank_change_closes_history_updates_master_and_keeps_an_actual_audit(store):
    before = vendor_detail(store, "VEN-001")
    result = change(store)
    assert result.account_on_file_display == "999900009999"
    assert len(result.account_history) == 2
    assert result.account_history[0].valid_to is not None
    assert result.account_history[1].valid_to is None
    assert result.account_history[1].verified_by == "person-vendor-admin"
    assert result.payments == before.payments
    vendor = store.get_record("vendors", "VEN-001")
    assert vendor["remit_account_number"] == result.account_on_file_display
    audit = latest_account_verification(store, "VEN-001", result.account_on_file_display)
    assert audit["source"] == "vendor_admin_bank_change"
    assert audit["verified_role"] == "vendor_admin"
    assert audit["verified_by"] == "person-vendor-admin"
    assert audit["evidence_note"]
    assert vendor["account_verification_change_id"] == audit["change_id"]
    seed_trusted_world(store)
    seed_vendor_history(store)
    assert vendor_detail(store, "VEN-001") == result


def test_seed_archive_cannot_qualify_as_an_admin_approval_verification(store):
    assert latest_account_verification(store, "VEN-001", "100100001111") is None


def test_new_verified_master_account_has_an_explicit_history_exception(store):
    change(store)
    passed, message = history_check(store, store.get_record("vendors", "VEN-001"), "999900009999")
    assert passed
    assert "independently verified by Morgan Diaz" in message
    assert "vendor-admin audit" in message


@pytest.mark.parametrize("actor", ["person-ap-agent", "person-ap-manager", "person-controller", "missing-actor"])
def test_only_vendor_admin_can_record_changes_and_failure_leaves_history_unchanged(store, actor):
    before = vendor_detail(store, "VEN-001")
    with pytest.raises(HTTPException) as error:
        change(store, actor=actor)
    assert error.value.status_code == 403
    assert "vendor_admin" in error.value.detail
    assert "Morgan Diaz" in error.value.detail
    assert vendor_detail(store, "VEN-001") == before
    assert store.list_records("vendor_bank_changes") == []


@pytest.mark.parametrize("account", ["", "123", "letters", "9" * 35, "123456789012"])
def test_invalid_or_unresolvable_new_account_is_rejected(store, account):
    with pytest.raises(HTTPException) as error:
        change(store, account=account)
    assert error.value.status_code == 422
    assert vendor_detail(store, "VEN-001").account_on_file_display == "100100001111"


def test_bank_change_requires_evidence_and_a_known_vendor(store):
    with pytest.raises(HTTPException) as error:
        change(store, note=" ")
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        record_bank_change(store, "missing", "person-vendor-admin", "999900009999", "Callback verified", bank_mode="offline")
    assert error.value.status_code == 404


def test_active_verification_binds_the_written_audit_not_the_latest_clock_timestamp(store):
    change(store)
    current = latest_account_verification(store, "VEN-001", "999900009999")
    stale = {**current, "change_id": "stale-future-clock", "verified_by": "someone-else",
             "verified_at": "2099-01-01T00:00:00Z"}
    store.save_record("vendor_bank_changes", stale["change_id"], stale)
    assert latest_account_verification(store, "VEN-001", "999900009999")["change_id"] == current["change_id"]


def test_role_at_verification_is_retained_if_a_person_changes_role(store):
    change(store)
    actor = store.get_record("people", "person-vendor-admin")
    store.save_record("people", actor["person_id"], {**actor, "role": "ap_manager", "approval_limit_cents": 5000000})
    audit = latest_account_verification(store, "VEN-001", "999900009999")
    assert audit["verified_by"] == actor["person_id"] and audit["verified_role"] == "vendor_admin"


def test_actual_payment_history_appends_real_journal_reference_once_and_detects_conflicts(store):
    values = dict(entry_id="journal-real-payment-1", date="2026-10-24", amount_cents=125000,
                  account_display="100100001111")
    record_vendor_payment(store, "VEN-001", **values)
    record_vendor_payment(store, "VEN-001", **values)
    detail = vendor_detail(store, "VEN-001")
    assert len(detail.payments) == 7
    assert detail.payments[-1].journal_entry_id == values["entry_id"]
    with pytest.raises(ValueError, match="cannot change"):
        record_vendor_payment(store, "VEN-001", **{**values, "amount_cents": 1})
    assert vendor_detail(store, "VEN-001") == detail


def test_mixed_account_history_counts_only_payments_to_the_named_account(store):
    for month in (7, 8):
        record_vendor_payment(store, "VEN-001", entry_id=f"journal-account-b-{month}",
                              date=f"2026-{month:02d}-15", amount_cents=10000,
                              account_display="200200002222")
    change(store)
    vendor = store.get_record("vendors", "VEN-001")
    passed, message = history_check(store, vendor, "999900009999")
    assert passed
    assert "Paid 2 times since July 2026 to the account ending 2222" in message
    assert "Paid 8 times" not in message
    assert "new on-file account ending 9999" in message
    passed, message = history_check(store, vendor, "100100001111")
    assert passed
    assert "Paid 6 times since January 2026 to the account ending 1111" in message
