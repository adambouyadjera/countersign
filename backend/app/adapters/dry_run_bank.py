"""Capability-denied bank for evaluation: no network, money, or transfer IDs.

The ordinary decision path records an authorized *intent* here. It must never
turn that record into a Payment or payment.executed event. Any attempt to use a
bank I/O method fails explicitly rather than selecting another bank adapter.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from threading import RLock

from app.adapters.bank import BankError


class DryRunViolation(BankError):
    """A caller attempted a capability unavailable to the dry-run evaluation."""


class DryRunBank:
    mode = "dry_run"

    def __init__(self, store=None):
        # There is deliberately no upstream client, URL, credential, transport,
        # or live/offline bank fallback on this adapter.
        self.store = store
        self._intents = {}
        self._denied_calls = []
        self._lock = RLock()

    @property
    def intents(self):
        with self._lock:
            return deepcopy(list(self._intents.values()))

    @property
    def denied_calls(self):
        with self._lock:
            return list(self._denied_calls)

    def record_intent(self, **metadata):
        """Persist an actual would-pay decision without a bank identity.

        The stable internal identity is an audit-record key only. Repeated
        identical intents are idempotent; no transfer ID, settlement result, or
        financial ledger entry is created.
        """
        if not isinstance(metadata.get("run_id"), str) or not metadata["run_id"].strip():
            raise DryRunViolation("A dry-run intent requires its actual run identity.")
        if not isinstance(metadata.get("invoice_id"), str) or not metadata["invoice_id"].strip():
            raise DryRunViolation("A dry-run intent requires its actual invoice identity.")
        if type(metadata.get("amount_cents")) is not int or metadata["amount_cents"] <= 0:
            raise DryRunViolation("A dry-run intent requires a positive integer-cent amount.")
        metadata.setdefault("lane", "countersign")
        if metadata["lane"] not in {"countersign", "unguarded"}:
            raise DryRunViolation("A dry-run intent requires a known evaluation lane.")
        if {"intent_id", "transfer_id", "transfer_status", "recorded_at", "execution", "submitted", "mode"} & metadata.keys():
            raise DryRunViolation("Dry-run intent metadata cannot assert a bank result or replace its audit envelope.")
        try:
            canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)
            values = json.loads(canonical)
        except (TypeError, ValueError):
            raise DryRunViolation("Dry-run intent metadata must contain finite JSON values.") from None
        identifier = "dry-run-intent-" + hashlib.sha256(canonical.encode()).hexdigest()
        with self._lock:
            existing = self._intents.get(identifier)
            if existing is None and self.store is not None:
                existing = self.store.get_record("dry_run_intents", identifier)
            if existing is not None:
                self._intents[identifier] = deepcopy(existing)
                return deepcopy(existing)
            record = {**values, "intent_id": identifier, "execution": "dry_run", "mode": self.mode,
                      "submitted": False, "recorded_at": datetime.now(timezone.utc).isoformat()}
            if self.store is not None:
                self.store.save_record("dry_run_intents", identifier, record)
            self._intents[identifier] = record
            return deepcopy(record)

    def _deny(self, operation):
        with self._lock:
            self._denied_calls.append(operation)
        raise DryRunViolation(f"Dry-run evaluation cannot call bank {operation}; no bank request was made.")

    async def health_check(self, *args, **kwargs):
        self._deny("health_check")

    async def get_balance(self, *args, **kwargs):
        self._deny("get_balance")

    async def get_balances(self, *args, **kwargs):
        self._deny("get_balances")

    async def list_transfers(self, *args, **kwargs):
        self._deny("list_transfers")

    async def account_activity(self, *args, **kwargs):
        self._deny("account_activity")

    async def list_customers(self, *args, **kwargs):
        self._deny("list_customers")

    async def create_customer(self, *args, **kwargs):
        self._deny("create_customer")

    async def list_accounts(self, *args, **kwargs):
        self._deny("list_accounts")

    async def create_account(self, *args, **kwargs):
        self._deny("create_account")

    async def transfer(self, *args, **kwargs):
        self._deny("transfer")
