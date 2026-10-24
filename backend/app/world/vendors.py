"""Trusted vendor history and independently verified master-account changes.

The historical opening data is explicitly a demo archive, not a claim that
Nessie transfers happened. Actual new payments append their real journal IDs.
No document or agent-remittance tool can write these trusted records.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import re
from uuid import uuid4

from fastapi import HTTPException

from app.contract_models import VendorDetail


_ARCHIVE_DATES = tuple(date(2026, month, 15).isoformat() for month in range(1, 7))
_INITIAL_VALID_FROM = "2026-01-01T00:00:00Z"
_INITIAL_VERIFIED_AT = "2026-03-12T18:00:00Z"


def _account(value) -> str:
    if not isinstance(value, str):
        return ""
    normalized = re.sub(r"[\s-]", "", value)
    return normalized if re.fullmatch(r"[0-9]{4,34}", normalized) else ""


def seed_vendor_history(store) -> None:
    """Insert labelled opening history once; never overwrite verified updates."""
    # Startup in another worker must not overwrite a concurrently committed
    # admin change between the existence check and insertion of opening data.
    with store.transaction():
        for vendor in store.list_records("vendors"):
            vendor_id = vendor["vendor_id"]
            if store.get_record("vendor_history", vendor_id) is not None:
                continue
            account = _account(vendor.get("account_on_file_display", vendor.get("remit_account_number")))
            if not account:
                continue
            orders = [row for row in store.list_records("purchase_orders") if row.get("vendor_id") == vendor_id]
            amount = next((row.get("amount_cents") for row in orders
                           if type(row.get("amount_cents")) is int and row["amount_cents"] > 0), 0)
            payments = [{
                "date": day, "amount_cents": amount, "account_display": account,
                "journal_entry_id": f"example-history-{vendor_id}-{day[:7]}",
            } for day in _ARCHIVE_DATES] if amount else []
            store.save_record("vendor_history", vendor_id, {
                "vendor_id": vendor_id,
                "source": "demo_historical_seed",
                "archive_note": "Seeded demonstration of imported January-June payment history; not live Nessie transfers or current-period cash postings.",
                "account_history": [{
                    "account_display": account, "valid_from": _INITIAL_VALID_FROM,
                    "valid_to": None, "verified_by": "person-vendor-admin",
                    "verified_at": _INITIAL_VERIFIED_AT,
                }],
                "payments": payments,
            })


def vendor_detail(store, vendor_id: str) -> VendorDetail:
    # Read the account master and history from one snapshot, even while a
    # different process records a verified banking update.
    with store.transaction():
        vendor = store.get_record("vendors", vendor_id)
        if vendor is None:
            raise HTTPException(404, "Vendor not found")
        history = store.get_record("vendor_history", vendor_id) or {}
    return VendorDetail(
        vendor_id=vendor_id, name=vendor["name"],
        account_on_file_display=vendor.get("account_on_file_display", vendor.get("remit_account_number", "")),
        account_history=history.get("account_history", []),
        payments=sorted(history.get("payments", []), key=lambda item: (item["date"], item["journal_entry_id"])),
    )


def latest_account_verification(store, vendor_id: str, account_display: str) -> dict | None:
    """Return only an actual vendor-admin audit, never seeded archive claims."""
    number = _account(account_display)
    candidates = [row for row in store.list_records("vendor_bank_changes")
                  if row.get("vendor_id") == vendor_id
                  and _account(row.get("account_display")) == number
                  and row.get("source") == "vendor_admin_bank_change"
                  and row.get("verified_role") == "vendor_admin"]
    vendor = store.get_record("vendors", vendor_id) or {}
    if _account(vendor.get("account_on_file_display", vendor.get("remit_account_number"))) == number:
        # The master points to its actual active verification. Wall-clock
        # ordering alone cannot identify it after clock skew or re-verification.
        active = vendor.get("account_verification_change_id")
        candidates = [row for row in candidates if row["change_id"] == active]
    return max(candidates, key=lambda row: (row["verified_at"], row["change_id"])) if candidates else None


def _approver_hint(store) -> str:
    people = [row for row in store.list_records("people") if row.get("role") == "vendor_admin"]
    names = ", ".join(row["name"] for row in people)
    return f"Ask {names} (vendor_admin) to verify the change." if names else "A vendor_admin must verify the change."


def record_bank_change(store, vendor_id: str, actor_id: str, new_account_display: str,
                       evidence_note: str, *, bank_mode: str | None = None) -> VendorDetail:
    """A vendor-admin-only, atomic master update with a durable verifier audit."""
    number = _account(new_account_display)
    if not number:
        raise HTTPException(422, "new_account_display must be a 4 to 34 digit account number.")
    if not isinstance(evidence_note, str) or not evidence_note.strip():
        raise HTTPException(422, "evidence_note is required to record independent vendor verification.")
    with store._lock:
        connection = store._db()
        connection.execute("BEGIN IMMEDIATE")
        try:
            vendor = store.get_record("vendors", vendor_id)
            if vendor is None:
                raise HTTPException(404, "Vendor not found")
            actor = store.get_record("people", actor_id)
            if actor is None or actor.get("role") != "vendor_admin":
                raise HTTPException(403, "Only a vendor_admin may change the trusted vendor master. " + _approver_hint(store))
            directory = [row for row in store.list_records("bank_directory")
                         if _account(row.get("display_number")) == number
                         and (bank_mode is None or row.get("mode") == bank_mode)]
            if bank_mode is None:
                live = [row for row in directory if row.get("mode") == "live"]
                directory = live or directory
            if len(directory) != 1 or not directory[0].get("account_id"):
                raise HTTPException(422, "The new account must resolve uniquely in the bank directory before it can be verified.")
            history = deepcopy(store.get_record("vendor_history", vendor_id) or {
                "vendor_id": vendor_id, "source": "verified_master_history",
                "account_history": [], "payments": [],
            })
            now = datetime.now(timezone.utc).isoformat()
            for item in history["account_history"]:
                if item["valid_to"] is None:
                    item["valid_to"] = now
            history["account_history"].append({
                "account_display": number, "valid_from": now, "valid_to": None,
                "verified_by": actor_id, "verified_at": now,
            })
            change_id = f"bank-change-{uuid4().hex}"
            change = {
                "change_id": change_id, "vendor_id": vendor_id,
                "account_display": number,
                "previous_account_display": vendor.get("account_on_file_display", vendor.get("remit_account_number", "")),
                "verified_by": actor_id, "verified_role": "vendor_admin", "verified_at": now,
                "evidence_note": evidence_note.strip(), "source": "vendor_admin_bank_change",
            }
            store.save_record("vendor_history", vendor_id, history)
            store.save_record("vendor_bank_changes", change_id, change)
            store.save_record("vendors", vendor_id, {
                **vendor, "account_on_file_display": number, "remit_account_number": number,
                "account_verification_change_id": change_id,
            })
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return vendor_detail(store, vendor_id)


def record_vendor_payment(store, vendor_id: str, *, entry_id: str, date: str,
                          amount_cents: int, account_display: str) -> None:
    """Append a posted payment's real journal reference exactly once."""
    number = _account(account_display)
    if not number or not entry_id or type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("Vendor payment history requires a real journal reference and valid payment values")
    # Validate wire date and payment fields using the same contract transport.
    from app.contract_models import VendorPayment
    payment = VendorPayment.model_validate({"date": date, "amount_cents": amount_cents,
                                           "account_display": number, "journal_entry_id": entry_id}).model_dump(mode="json")
    with store.transaction():
        if store.get_record("vendors", vendor_id) is None:
            raise ValueError("Vendor payment history requires a known vendor")
        history = deepcopy(store.get_record("vendor_history", vendor_id) or {
            "vendor_id": vendor_id, "source": "verified_master_history",
            "account_history": [], "payments": [],
        })
        previous = [row for row in history["payments"] if row["journal_entry_id"] == entry_id]
        if previous and previous != [payment]:
            raise ValueError("A vendor payment journal reference cannot change its values")
        if not previous:
            history["payments"].append(payment)
            store.save_record("vendor_history", vendor_id, history)


def history_check(store, vendor: dict | None, requested_account: str) -> tuple[bool, str]:
    """Compare against trusted payment history, never document instructions."""
    if vendor is None:
        return False, "Vendor history is unavailable because the vendor is unknown."
    history = store.get_record("vendor_history", vendor["vendor_id"]) or {}
    payments = history.get("payments", [])
    requested = _account(requested_account)
    on_file = _account(vendor.get("account_on_file_display", vendor.get("remit_account_number")))
    prior_accounts = {_account(item["account_display"]) for item in payments}
    matches = bool(requested and requested in prior_accounts)
    # Count one actual account's payments. Combining multiple former accounts
    # would falsely claim that their total was paid to the last one's ending.
    reference_account = requested if matches else on_file if on_file in prior_accounts else (
        _account(max(payments, key=lambda item: (item["date"], item["journal_entry_id"]))["account_display"])
        if payments else ""
    )
    reference = [item for item in payments if _account(item["account_display"]) == reference_account]
    if reference:
        first = min(item["date"] for item in reference)
        since = datetime.fromisoformat(first).strftime("%B %Y")
        verified = next((item for item in reversed(history.get("account_history", []))
                         if _account(item["account_display"]) == reference_account), None)
        actor = store.get_record("people", verified["verified_by"]) if verified else None
        verifier = actor["name"] if actor else verified["verified_by"] if verified else "unrecorded verifier"
        verified_on = verified["verified_at"][:10] if verified else "an unrecorded date"
        message = (f"Paid {len(reference)} times since {since} to the account ending {reference_account[-4:]}, "
                   f"verified by {verifier} on {verified_on}.")
    else:
        message = "No prior vendor payments are recorded; independent review is required."
    verification = latest_account_verification(store, vendor["vendor_id"], requested)
    if not matches and requested == on_file and verification is not None:
        actor = store.get_record("people", verification["verified_by"])
        verifier = actor["name"] if actor else verification["verified_by"]
        return True, (message + f" The new on-file account ending {requested[-4:]} was independently verified by "
                      f"{verifier} on {verification['verified_at'][:10]}; the vendor-admin audit is retained.")
    return matches, message + (" Requested payee matches trusted payment history." if matches else
                               " Requested account differs from trusted payment history; human countersign is required.")
