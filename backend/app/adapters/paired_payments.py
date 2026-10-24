"""Explicit, non-atomic Nessie withdrawal/deposit payments.

The local attempt key is an idempotency handle, never a claimed bank resource
identifier. Only IDs and statuses returned by Nessie become payment results.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from app.adapters.bank import BankError


_SETTLED = {"completed", "executed", "success", "succeeded"}
_REJECTED = {"failed", "rejected", "cancelled", "canceled"}


class PairedPaymentError(BankError):
    """A safe error retaining actual bank IDs for manual reconciliation."""

    def __init__(self, message: str, attempt: dict[str, Any]):
        self.attempt_id = attempt["attempt_id"]
        self.withdrawal_id = attempt.get("withdrawal_id")
        self.deposit_id = attempt.get("deposit_id")
        super().__init__(
            f"{message} Attempt {self.attempt_id}; "
            f"withdrawal_id={self.withdrawal_id or 'not confirmed'}, "
            f"deposit_id={self.deposit_id or 'not confirmed'}. "
            "Do not resubmit with a different payment description before reconciliation."
        )


def _attempt_key(source: str, destination: str, amount_cents: int, description: str, session: str) -> str:
    request = json.dumps([source, destination, amount_cents, description, session], separators=(",", ":"))
    return "paired-attempt-" + hashlib.sha256(request.encode("utf-8")).hexdigest()


def _resource(body: Any) -> dict[str, Any]:
    value = body.get("objectCreated", body) if isinstance(body, dict) else None
    if not isinstance(value, dict):
        raise BankError("Nessie returned an unexpected paired-payment resource.")
    identifier = value.get("_id", value.get("id"))
    if not isinstance(identifier, str) or not identifier:
        raise BankError("Nessie did not return a real paired-payment resource ID.")
    return {**value, "_id": identifier}


def _validate_leg(raw: dict[str, Any], account_id: str, amount_cents: int, *, leg: str) -> str:
    status = raw.get("status")
    if not isinstance(status, str) or not status:
        raise BankError("Nessie did not return a paired-payment resource status.")
    if "amount" in raw:
        try:
            returned_cents = Decimal(str(raw["amount"])) * 100
        except (InvalidOperation, ValueError, TypeError):
            raise BankError("Nessie returned an invalid paired-payment amount.") from None
        if not returned_cents.is_finite() or returned_cents != amount_cents:
            raise BankError("Nessie returned a paired-payment amount differing from the request.")
    if "account_id" in raw and raw["account_id"] != account_id:
        raise BankError("Nessie returned a paired-payment resource for a different account.")
    participant = "payer_id" if leg == "withdrawal" else "payee_id"
    if participant in raw and raw[participant] != account_id:
        raise BankError("Nessie returned a paired-payment resource for a different participant.")
    return status


def _result(attempt: dict[str, Any]) -> dict[str, Any]:
    withdrawal_status = attempt["withdrawal_status"]
    deposit_status = attempt["deposit_status"]
    # Preserve a real returned status. One completed leg never implies that the
    # other leg has completed, and a requested pending status is not a success.
    statuses = (withdrawal_status, deposit_status)
    status = next((value for value in statuses if value.lower() in _REJECTED), None)
    if status is None:
        status = next((value for value in statuses if value.lower() not in _SETTLED), deposit_status)
    return {
        "attempt_id": attempt["attempt_id"],
        "transfer_id": attempt["withdrawal_id"],
        "from_account_id": attempt["from_account_id"],
        "to_account_id": attempt["to_account_id"],
        "amount_cents": attempt["amount_cents"],
        "description": attempt["description"],
        "status": status,
        "ts": attempt["ts"],
        "mode": "live",
        "detail": {
            "method": "paired_withdrawal_deposit",
            "atomic": False,
            "withdrawal_id": attempt["withdrawal_id"],
            "deposit_id": attempt["deposit_id"],
            "withdrawal_status": withdrawal_status,
            "deposit_status": deposit_status,
        },
    }


async def execute_paired_payment(
    bank, source: str, destination: str, amount_cents: int, description: str,
) -> dict[str, Any]:
    """Submit one debit and one credit, persisting each irreversible step.

    Identical source/destination/amount/description calls share an attempt. A
    successful repeat returns the same real IDs. Any interrupted or ambiguous
    attempt fails closed for reconciliation; it never repeats either POST.
    Use a distinct invoice/payment reference for each intentional new payment.
    """
    lock = getattr(bank, "_paired_payment_lock", None)
    if lock is None:
        lock = bank._paired_payment_lock = asyncio.Lock()
    async with lock:
        return await _execute_paired_payment(bank, source, destination, amount_cents, description)


async def _execute_paired_payment(bank, source, destination, amount_cents, description):
    if bank.mode != "live":
        raise ValueError("Paired Nessie operations require live mode")
    if type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")
    if amount_cents % 100:
        raise ValueError(
            "Paired Nessie payments require whole-dollar amounts until fractional-dollar settlement is verified."
        )
    if not source or not destination or source == destination:
        raise ValueError("Two different account IDs are required")
    if bank._store is None:
        raise BankError("Paired Nessie payments require persistent attempt storage.")
    attempt_id = _attempt_key(
        source, destination, amount_cents, description, bank._store.session_started_at.isoformat(),
    )
    existing = bank._store.get_record("bank_payment_attempts", attempt_id)
    if existing:
        if existing.get("state") == "both_created":
            return await refresh_paired_payment(bank, existing)
        raise PairedPaymentError("An earlier paired payment needs reconciliation.", existing)

    balance = await bank.get_balance(source)
    if balance.balance_cents < amount_cents:
        raise BankError("Insufficient live funds for the paired payment.")
    # Verify the destination exists before creating a debit.
    await bank.get_balance(destination)
    timestamp = datetime.now(timezone.utc)
    attempt = {
        "attempt_id": attempt_id,
        "method": "paired_withdrawal_deposit",
        "from_account_id": source,
        "to_account_id": destination,
        "amount_cents": amount_cents,
        "description": description,
        "ts": timestamp.isoformat(),
        "mode": "live",
        "state": "withdrawal_submitting",
    }

    def save():
        bank._store.save_record("bank_payment_attempts", attempt_id, attempt)

    payload = {
        "medium": "balance",
        "transaction_date": timestamp.date().isoformat(),
        "status": "pending",
        "amount": float(Decimal(amount_cents) / 100),
        "description": description,
    }
    if not bank._store.claim_bank_payment_attempt(attempt):
        existing = bank._store.get_record("bank_payment_attempts", attempt_id)
        if existing and existing.get("state") == "both_created":
            return await refresh_paired_payment(bank, existing)
        raise PairedPaymentError(
            "Another worker already claimed this paired payment; no debit was submitted by this worker.",
            existing or attempt,
        )
    try:
        withdrawal = _resource(await bank._request(
            "POST", f"/accounts/{source}/withdrawals", payload=payload,
        ))
        attempt["withdrawal_id"] = withdrawal["_id"]
        attempt["state"] = "withdrawal_created"
        save()
        attempt["withdrawal_status"] = _validate_leg(withdrawal, source, amount_cents, leg="withdrawal")
        save()
        if attempt["withdrawal_status"].lower() in _REJECTED:
            attempt["state"] = "withdrawal_rejected"
            save()
            raise PairedPaymentError("Nessie rejected the withdrawal; no deposit was submitted.", attempt)
    except PairedPaymentError:
        raise
    except Exception:
        attempt["state"] = "withdrawal_unconfirmed"
        save()
        raise PairedPaymentError("Withdrawal submission could not be confirmed.", attempt) from None

    attempt["state"] = "deposit_submitting"
    save()
    try:
        deposit = _resource(await bank._request(
            "POST", f"/accounts/{destination}/deposits", payload=payload,
        ))
        attempt["deposit_id"] = deposit["_id"]
        attempt["state"] = "deposit_created"
        save()
        attempt["deposit_status"] = _validate_leg(deposit, destination, amount_cents, leg="deposit")
        attempt["state"] = "both_created"
        save()
    except Exception:
        attempt["state"] = "deposit_unconfirmed"
        save()
        raise PairedPaymentError(
            "Withdrawal exists but deposit submission could not be confirmed; the pair is non-atomic.",
            attempt,
        ) from None
    return _result(attempt)


async def refresh_paired_payment(bank, attempt: dict[str, Any]) -> dict[str, Any]:
    """Read both real resource IDs back without posting either leg again."""
    updated = dict(attempt)
    for leg, endpoint, account_id in (
        ("withdrawal", "withdrawal", attempt["from_account_id"]),
        ("deposit", "deposits", attempt["to_account_id"]),
    ):
        identifier = attempt[f"{leg}_id"]
        resource = _resource(await bank._request("GET", f"/{endpoint}/{identifier}"))
        if resource["_id"] != identifier:
            raise BankError("Nessie returned a different paired-payment resource ID.")
        updated[f"{leg}_status"] = _validate_leg(
            resource, account_id, attempt["amount_cents"], leg=leg,
        )
    bank._store.save_record("bank_payment_attempts", updated["attempt_id"], updated)
    return _result(updated)


async def refresh_paired_payments(bank, account_id: str) -> list[dict[str, Any]]:
    """List only locally correlated pairs involving the requested account."""
    if bank._store is None:
        return []
    results = []
    for attempt in bank._store.list_records("bank_payment_attempts"):
        if (attempt.get("mode") == "live" and attempt.get("state") == "both_created"
                and account_id in {attempt["from_account_id"], attempt["to_account_id"]}):
            results.append(await refresh_paired_payment(bank, attempt))
    return results
