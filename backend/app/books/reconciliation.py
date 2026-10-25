"""Independently compare the bank's submitted-payment register with book cash."""

import asyncio
from datetime import datetime, timezone
import hashlib

from app.adapters.bank import BankError
from app.contract_models import Reconciliation, ReconciliationExclusion, ReconciliationItem


_REJECTED = {"failed", "rejected", "cancelled", "canceled", "error"}

_LABELS = {
    "unguarded_lane": "unguarded lane (comparison agent, not Countersign's payments)",
    "before_books_started": "before books started",
}


class Reconciler:
    def __init__(self, store, ledger, bank):
        self.store, self.ledger, self.bank = store, ledger, bank
        self._lock = asyncio.Lock()

    def _company(self):
        companies = [row for row in self.store.list_records("bank_directory")
                     if row.get("role") == "company" and row.get("mode") == self.bank.mode]
        if len(companies) != 1:
            raise BankError("Reconciliation requires one seeded company account for the configured bank mode.")
        return companies[0]["account_id"]

    def _key(self, account_id):
        identity = "|".join((self.store.session_started_at.isoformat(), self.bank.mode, account_id))
        return "reconciliation-" + hashlib.sha256(identity.encode()).hexdigest()

    async def ensure_baseline(self):
        """Snapshot remote IDs before any run payment, not after money moves.

        Nessie's date-only rows cannot distinguish two demo sessions on one day.
        Existing current-session transfer IDs are retained if upgrading a store
        that already executed payments before ledger support was installed.
        """
        async with self._lock:
            account = self._company()
            key = self._key(account)
            existing = self.store.get_record("reconciliation_sessions", key)
            if existing is not None:
                return existing
            activity = await self.bank.account_activity(account)
            current = {row["transfer_id"] for row in self.store.list_records("transfers")
                       if row.get("mode", row.get("bank_mode")) == self.bank.mode
                       and row.get("session_started_at") == self.store.session_started_at.isoformat()}
            balance = await self.bank.get_balance(account)
            baseline = {"session_id": key, "account_id": account, "mode": self.bank.mode,
                "session_started_at": self.store.session_started_at.isoformat(),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "bank_balance_cents_at_capture": balance.balance_cents,
                "prior_activity_ids": sorted(item.transfer_id for item in activity if item.transfer_id not in current),
                "basis": "submitted_payment_register_including_pending"}
            # Concurrent workers must retain the first successful snapshot.
            with self.store.transaction():
                winner = self.store.get_record("reconciliation_sessions", key)
                if winner is not None:
                    return winner
                self.store.save_record("reconciliation_sessions", key, baseline)
            return baseline

    async def reconcile(self):
        """Countersign's books against Countersign-lane bank movements.

        Left out of the difference, but reported with counts and every ID:
        - the unguarded lane's transfers and their journal entries (the comparison agent);
        - current-session transfers made before the books started (before the baseline was
          captured) that never got a journal entry.
        Bank activity this app never submitted stays bank_only and counts: unknown money is
        never excluded.
        """
        baseline = await self.ensure_baseline()
        account = baseline["account_id"]
        prior = set(baseline["prior_activity_ids"])
        activity = [item for item in await self.bank.account_activity(account)
                    if item.transfer_id not in prior and item.status.casefold() not in _REJECTED]
        if any(item.cash_change_cents is None for item in activity):
            raise BankError("New company-account activity has unknown routing direction; manual reconciliation is required.")
        all_cash = self.ledger.cash_entries(since=self.store.session_started_at, bank_mode=self.bank.mode)
        transfers = {row["transfer_id"]: row for row in self.store.list_records("transfers") if row.get("transfer_id")}
        lane = lambda transfer_id: (transfers.get(transfer_id) or {}).get("lane")
        booked = {entry["transfer_id"] for entry in all_cash if entry.get("transfer_id")}
        session = self.store.session_started_at.isoformat()
        books_started = datetime.fromisoformat(baseline["captured_at"])

        def submitted_at(row):
            try:
                at = datetime.fromisoformat(str(row.get("ts", "")).replace("Z", "+00:00"))
            except ValueError:
                return None
            return at if at.tzinfo else at.replace(tzinfo=timezone.utc)

        def before_books(transfer_id):
            row = transfers.get(transfer_id)
            at = submitted_at(row) if row is not None else None
            return (at is not None and transfer_id not in booked
                    and row.get("session_started_at") == session and at < books_started)

        groups = {}

        def exclude(reason, side, transfer_id, amount_cents):
            group = groups.setdefault((reason, side), {"transfer_ids": [], "amount_cents": 0})
            group["transfer_ids"].append(transfer_id)
            group["amount_cents"] += abs(amount_cents)

        included_activity = []
        for item in activity:
            if lane(item.transfer_id) == "unguarded":
                exclude("unguarded_lane", "bank", item.transfer_id, item.cash_change_cents)
            elif before_books(item.transfer_id):
                exclude("before_books_started", "bank", item.transfer_id, item.cash_change_cents)
            else:
                included_activity.append(item)
        cash = []
        for entry in all_cash:
            if entry.get("transfer_id") and lane(entry["transfer_id"]) == "unguarded":
                exclude("unguarded_lane", "ledger", entry["transfer_id"], entry["cash_change_cents"])
            else:
                cash.append(entry)
        activity = included_activity
        by_id = {}
        for entry in cash:
            by_id.setdefault(entry["transfer_id"], []).append(entry)
        used, items = set(), []
        for item in sorted(activity, key=lambda row: row.transfer_id):
            matches = by_id.get(item.transfer_id, [])
            note = f"Bank status: {item.status}. Submitted-payment register basis includes pending items; this is not settled bank balance."
            if len(matches) == 1 and matches[0]["cash_change_cents"] == item.cash_change_cents:
                entry = matches[0]
                used.add(entry["entry_id"])
                items.append(ReconciliationItem(transfer_id=item.transfer_id, journal_entry_id=entry["entry_id"],
                    amount_cents=item.amount_cents, status="matched", note=note))
            else:
                reason = "No cash journal entry with this transfer ID." if not matches else "Cash journal amount/direction or duplicate transfer linkage differs."
                items.append(ReconciliationItem(transfer_id=item.transfer_id, journal_entry_id=None,
                    amount_cents=item.amount_cents, status="bank_only", note=reason + " " + note))
        for entry in cash:
            if entry["entry_id"] not in used:
                items.append(ReconciliationItem(transfer_id=entry["transfer_id"], journal_entry_id=entry["entry_id"],
                    amount_cents=abs(entry["cash_change_cents"]), status="ledger_only",
                    note="Cash journal entry has no matching bank register ID and signed amount in this session. Reversals remain separate immutable entries."))
        bank_change = sum(item.cash_change_cents for item in activity)
        ledger_change = sum(entry["cash_change_cents"] for entry in cash)
        excluded = [ReconciliationExclusion(reason=reason, label=_LABELS[reason], side=side,
                        count=len(group["transfer_ids"]), amount_cents=group["amount_cents"],
                        transfer_ids=sorted(group["transfer_ids"]))
                    for (reason, side), group in sorted(groups.items())]
        return Reconciliation(as_of=datetime.now(timezone.utc), bank_balance_change_cents=bank_change,
            ledger_cash_change_cents=ledger_change, difference_cents=bank_change-ledger_change, items=items,
            scope="countersign_lane", excluded=excluded)

    async def balance_observation(self):
        """Separate actual settled balance evidence from register reconciliation."""
        baseline = await self.ensure_baseline()
        balance = await self.bank.get_balance(baseline["account_id"])
        return {"basis": baseline["basis"], "account_id": baseline["account_id"],
            "captured_bank_balance_cents": baseline["bank_balance_cents_at_capture"],
            "current_bank_balance_cents": balance.balance_cents,
            "observed_balance_change_cents": balance.balance_cents-baseline["bank_balance_cents_at_capture"]}
