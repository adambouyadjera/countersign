"""Payment identity reservations protect numbers and semantic duplicates."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

import pytest

from app.store import Store


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "claims.db")
    result.initialize()
    try:
        yield result
    finally:
        result.close()


def identity(**changes):
    return {"vendor_id": "vendor-A", "amount_cents": 125000,
            "invoice_date": "2026-10-24", "po_number": None, **changes}


@pytest.mark.parametrize("state", ["reserved", "pending", "submitted", "paid", "uncertain"])
def test_new_number_cannot_repeat_paid_or_pending_vendor_amount_date(store, state):
    assert store.claim_session_payment("ORIGINAL", "first", **identity())
    store.finish_session_payment("ORIGINAL", state=state)
    assert not store.has_session_payment("NEW")  # Legacy exact-only query.
    assert store.has_session_payment("NEW", **identity())
    assert not store.claim_session_payment("NEW", "second", **identity())
    assert len(store.list_records("session_payment_claims")) == 1


@pytest.mark.parametrize("changes", [
    {"invoice_date": "2026-10-25"},
    {"vendor_id": "vendor-B"},
    {"amount_cents": 125001},
])
def test_distinct_vendor_amount_or_date_does_not_match_without_a_shared_po(store, changes):
    assert store.claim_session_payment("ORIGINAL", "first", **identity())
    assert not store.has_session_payment("NEW", **identity(**changes))
    assert store.claim_session_payment("NEW", "second", **identity(**changes))


def test_same_vendor_amount_po_blocks_even_when_invoice_date_changes(store):
    assert store.claim_session_payment("ORIGINAL", "first", **identity(po_number=" PO-42 "))
    requested = identity(invoice_date="2026-11-25", po_number="po-42")
    assert store.has_session_payment("NEW", **requested)
    assert not store.claim_session_payment("NEW", "second", **requested)


@pytest.mark.parametrize("missing", [
    {"vendor_id": None}, {"amount_cents": None}, {"amount_cents": True},
    {"invoice_date": None}, {"invoice_date": "not-a-date"},
])
def test_missing_or_invalid_semantic_facts_never_match_by_absence(store, missing):
    assert store.claim_session_payment("ORIGINAL", "first", **identity(**missing))
    assert not store.has_session_payment("NEW", **identity(**missing))


def test_date_objects_and_durable_metadata_survive_restart(store):
    assert store.claim_session_payment("ORIGINAL", "first", **identity(invoice_date=date(2026, 10, 24)))
    restarted = Store(store.path)
    restarted.initialize()
    try:
        assert restarted.has_session_payment("NEW", **identity())
        row = restarted.list_records("session_payment_claims")[0]
        assert row["invoice_date"] == "2026-10-24"
        assert row["vendor_id"] == "vendor-A" and row["amount_cents"] == 125000
    finally:
        restarted.close()


@pytest.mark.parametrize("status", ["pending", "completed"])
def test_pre_reset_paid_or_pending_transfer_does_not_block_new_demo_session(store, status):
    earlier = store.session_started_at - timedelta(days=90)
    store.save_record("transfers", "historic-transfer", {
        "transfer_id": "historic-transfer", "invoice_number": "ORIGINAL",
        "lane": "countersign", "transfer_status": status,
        # A later provider refresh must not move an old submission into the
        # current demo session.
        "session_started_at": earlier.isoformat(), "ts": store.session_started_at.isoformat(),
        **identity(),
    })
    assert not store.has_session_payment("NEW", **identity())
    assert store.claim_session_payment("NEW", "second", **identity())
    assert store.get_record("transfers", "historic-transfer") is not None


@pytest.mark.parametrize("status", ["pending", "completed", "uncertain"])
def test_current_session_transfer_still_blocks_exact_and_semantic_duplicates(store, status):
    store.save_record("transfers", "current-transfer", {
        "transfer_id": "current-transfer", "invoice_number": "ORIGINAL",
        "lane": "countersign", "transfer_status": status,
        "session_started_at": store.session_started_at.isoformat(), **identity(),
    })
    assert store.has_session_payment("ORIGINAL")
    assert store.has_session_payment("NEW", **identity())
    assert not store.claim_session_payment("NEW", "second", **identity())


@pytest.mark.parametrize("current", [True, False])
def test_legacy_transfer_timestamp_scopes_semantic_history_without_a_session_tag(store, current):
    created = store.session_started_at + timedelta(seconds=1 if current else -1)
    store.save_record("transfers", "legacy-transfer", {
        "transfer_id": "legacy-transfer", "invoice_number": "ORIGINAL",
        "lane": "countersign", "transfer_status": "pending", "ts": created.isoformat(), **identity(),
    })
    assert store.has_session_payment("ORIGINAL") is current
    assert store.has_session_payment("NEW", **identity()) is current


def test_eval_fixture_prior_paid_claim_still_blocks_exact_and_semantic_duplicates(store):
    assert store.claim_session_payment("ARCHIVED-EVAL-NUMBER", "committed-fixture-prior-paid", **identity())
    store.finish_session_payment("ARCHIVED-EVAL-NUMBER", state="fixture_prior_paid")
    assert store.has_session_payment("ARCHIVED-EVAL-NUMBER")
    assert store.has_session_payment("NEW", **identity())
    assert not store.claim_session_payment("NEW", "second", **identity())


def test_semantic_claim_from_earlier_session_is_not_a_current_reservation(store):
    assert store.claim_session_payment("ORIGINAL", "first", **identity())
    store._db().execute("UPDATE metadata SET value=? WHERE key='session_started_at'",
                        ((store.session_started_at + timedelta(days=1)).isoformat(),))
    assert not store.has_session_payment("NEW", **identity())
    assert store.claim_session_payment("NEW", "second", **identity())


@pytest.mark.parametrize("record_changes", [
    {"lane": "unguarded"}, {"via": "agent"}, {"transfer_status": "rejected"},
    {"status": "failed"}, {"transfer_status": "cancelled"},
])
def test_unguarded_and_explicitly_failed_transfers_are_not_semantic_paid_history(store, record_changes):
    store.save_record("transfers", "transfer", {
        "transfer_id": "transfer", "invoice_number": "ORIGINAL",
        "session_started_at": store.session_started_at.isoformat(),
        "lane": "countersign", "transfer_status": "completed", **identity(), **record_changes,
    })
    assert not store.has_session_payment("NEW", **identity())
    assert store.claim_session_payment("NEW", "second", **identity())


def test_legacy_missing_semantic_metadata_keeps_exact_protection_without_guessing(store):
    assert store.claim_session_payment("ORIGINAL", "first")
    assert store.has_session_payment("ORIGINAL", **identity())
    assert not store.has_session_payment("NEW", **identity())


def test_two_connections_cannot_reserve_different_numbers_for_one_semantic_invoice(store):
    other = Store(store.path)
    other.initialize()
    barrier = Barrier(2)

    def reserve(connection, number):
        barrier.wait(timeout=5)
        return connection.claim_session_payment(number, number, **identity())

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(reserve, store, "NUMBER-A")
            second = workers.submit(reserve, other, "NUMBER-B")
            assert sorted([first.result(timeout=10), second.result(timeout=10)]) == [False, True]
        assert len(store.list_records("session_payment_claims")) == 1
    finally:
        other.close()


def test_semantic_claim_composes_with_outer_bill_transaction_and_rolls_back(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            assert store.claim_session_payment("ORIGINAL", "first", **identity())
            raise RuntimeError("Synthetic bill rollback")
    assert not store.has_session_payment("NEW", **identity())
    assert store.list_records("session_payment_claims") == []


@pytest.mark.parametrize("history_kind", ["transfer", "claim"])
def test_bound_legacy_history_recovers_vendor_and_verified_invoice_references(store, history_kind):
    from app.contract_models import DocumentText, Run
    from app.pipeline.offline_extractor import parse_offline
    from app.pipeline.spans import to_payload
    from app.world.batches import create_standard_batch

    batch = create_standard_batch(store)
    invoice = batch.invoices[0]
    document = DocumentText.model_validate(store.get_record("documents", invoice.document_id)["text"])
    payload = to_payload(document, parse_offline(document), "offline-parser")
    fields = {field.name: field for field in payload.fields}
    store.save_run(Run(run_id="legacy-run", batch_id=batch.batch_id, instruction="trusted",
                       created_at=store.session_started_at, lanes=[]))
    extraction_id = f"legacy-run:{invoice.invoice_id}"
    store.save_record("extractions", extraction_id, {
        "extraction_id": extraction_id, "run_id": "legacy-run", **payload.model_dump(mode="json"),
    })
    metadata = {"amount_cents": invoice.amount_cents, "run_id": "legacy-run",
                "invoice_id": invoice.invoice_id}
    if history_kind == "transfer":
        store.save_record("transfers", "legacy-transfer", {
            "transfer_id": "legacy-transfer", "transfer_status": "pending",
            "invoice_number": "ORIGINAL", **metadata,
        })
    else:
        assert store.claim_session_payment("ORIGINAL", "legacy-attempt", **metadata)
    requested = {"vendor_id": invoice.vendor_id, "amount_cents": invoice.amount_cents,
                 "invoice_date": fields["invoice_date"].value}
    assert store.has_session_payment("NEW", **requested)
    assert not store.claim_session_payment("NEW", "second", **requested)


def test_transfer_timestamp_never_supplies_a_missing_invoice_date(store):
    from app.contract_models import Run

    store.save_run(Run(run_id="legacy-run", batch_id="legacy-batch", instruction="trusted",
                       created_at=store.session_started_at, lanes=[]))
    store.save_record("batches", "legacy-batch", {
        "batch_id": "legacy-batch", "invoices": [{"invoice_id": "legacy-invoice", "vendor_id": "vendor-A"}],
    })
    store.save_record("transfers", "legacy-transfer", {
        "transfer_id": "legacy-transfer", "run_id": "legacy-run", "invoice_id": "legacy-invoice",
        "invoice_number": "ORIGINAL", "amount_cents": 125000, "transfer_status": "completed",
        "ts": "2026-10-24T12:00:00+00:00",
    })
    assert not store.has_session_payment("NEW", **identity())
