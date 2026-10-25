"""The seeded January-June vendor archive is labelled wherever it is used."""

from app.world.vendors import ARCHIVE_NOTE, history_check, record_vendor_payment, vendor_detail
from tests.test_vendor_history import change, store  # noqa: F401  (pytest fixture)


def test_vendor_detail_flags_archive_rows_and_real_rows(store):
    record_vendor_payment(store, "VEN-001", entry_id="journal-real-1", date="2026-10-24",
                          amount_cents=125000, account_display="100100001111")
    detail = vendor_detail(store, "VEN-001").model_dump(mode="json")
    archived = [row for row in detail["payments"] if row["journal_entry_id"].startswith("example-history-")]
    real = [row for row in detail["payments"] if row["journal_entry_id"] == "journal-real-1"]
    assert archived and all(row["archive"] is True for row in archived)
    assert real and real[0]["archive"] is False
    assert [row["archive"] for row in detail["account_history"]] == [True]
    # A verified bank change adds a real (non-archive) account row and closes the seeded one.
    change(store)
    rows = vendor_detail(store, "VEN-001").model_dump(mode="json")["account_history"]
    assert [row["archive"] for row in rows] == [True, False]
    # The flag is computed on read, never stored.
    stored = store.get_record("vendor_history", "VEN-001")
    assert all("archive" not in row for row in stored["payments"] + stored["account_history"])


def test_vendor_history_message_says_when_it_includes_the_seeded_archive(store):
    vendor = store.get_record("vendors", "VEN-001")
    passed, message = history_check(store, vendor, "100100001111")
    assert passed
    assert message.startswith("Paid 6 times since January 2026 (includes seeded demo history) to the account ending 1111")


def test_vendor_history_message_has_no_note_without_archive_data(store):
    history = store.get_record("vendor_history", "VEN-001")
    store.save_record("vendor_history", "VEN-001", {
        **history, "source": "verified_master_history",
        "payments": [{"date": "2026-10-24", "amount_cents": 125000, "account_display": "100100001111",
                      "journal_entry_id": "journal-real-1"}],
        "account_history": [{**history["account_history"][0], "verified_at": "2026-10-01T09:00:00Z"}],
    })
    passed, message = history_check(store, store.get_record("vendors", "VEN-001"), "100100001111")
    assert passed and ARCHIVE_NOTE not in message
    assert message.startswith("Paid 1 times since October 2026 to the account ending 1111")
