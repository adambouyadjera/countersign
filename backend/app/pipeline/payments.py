"""One durable invoice reservation precedes every real bank transfer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re

from pydantic import BaseModel

from app.adapters.bank import BankError
from app.contract_models import LaneTally, Payment, RunTallies
from app.security.duplicate_identity import verified_invoice_references


_FAILED = {"failed", "rejected", "cancelled", "canceled", "error"}


def account_number(value: str) -> str:
    return re.sub(r"[\s-]", "", value) if isinstance(value, str) else ""


class DuplicatePaymentError(RuntimeError):
    """A paid or pending payment already owns this invoice identity."""


def refresh_run_tallies(store, run_id):
    """Recompute from durable results, so concurrent completion cannot double count."""
    run = store.get_run(run_id)
    if run is None:
        return None
    payments = {}
    decisions = {}
    for event in store.list_events(run_id):
        if event.type == "payment.executed" and event.lane == "countersign":
            payments[event.payload.payment.transfer_id] = event.payload
        elif event.type == "policy.decision":
            decisions[event.payload.decision.invoice_id] = event.payload.decision
    paid_invoices = {payload.invoice_id for payload in payments.values()}
    rejected = {
        record["invoice_id"] for record in store.list_records("approvals", run_id=run_id)
        if record["status"] == "resolved" and (record.get("resolution") or {}).get("decision") == "reject"
    }
    blocked = {identifier for identifier, decision in decisions.items() if decision.outcome == "BLOCK"} | rejected
    held = {identifier for identifier, decision in decisions.items() if decision.outcome == "HOLD"}
    held |= {record["invoice_id"] for record in store.list_records("approvals", run_id=run_id) if record["status"] == "pending"}
    held -= paid_invoices | blocked
    tally = LaneTally(
        paid_count=len(payments), paid_cents=sum(item.payment.amount_cents for item in payments.values()),
        held_count=len(held), blocked_count=len(blocked - paid_invoices),
        not_on_file_count=sum(not item.payment.payee_on_file for item in payments.values()),
        not_on_file_cents=sum(item.payment.amount_cents for item in payments.values() if not item.payment.payee_on_file),
    )
    if run.tallies is None:
        run.tallies = RunTallies()
    if any(lane.lane == "countersign" for lane in run.lanes):
        run.tallies.countersign = tally
    if any(lane.lane == "unguarded" for lane in run.lanes):
        agent_payments = {
            event.payload.payment.transfer_id: event.payload.payment
            for event in store.list_events(run_id)
            if event.type == "payment.executed" and event.lane == "unguarded"
        }
        run.tallies.unguarded = LaneTally(
            paid_count=len(agent_payments), paid_cents=sum(item.amount_cents for item in agent_payments.values()),
            held_count=0, blocked_count=0,
            not_on_file_count=sum(not item.payee_on_file for item in agent_payments.values()),
            not_on_file_cents=sum(item.amount_cents for item in agent_payments.values() if not item.payee_on_file),
        )
    store.save_run(run)
    return run


class PaymentExecutor:
    def __init__(self, store, bus, bank):
        self.store, self.bus, self.bank = store, bus, bank
        self._lock = asyncio.Lock()

    def prepare(self, *, vendor_id, amount_cents, to_display_number, require_on_file):
        if type(amount_cents) is not int or amount_cents <= 0:
            raise BankError("Payment amount must be a positive integer number of cents.")
        number = account_number(to_display_number)
        if not re.fullmatch(r"[0-9]{4,34}", number):
            raise BankError("Payment destination has an invalid account display number.")
        vendor = self.store.get_record("vendors", vendor_id) if vendor_id else None
        trusted_number = account_number((vendor or {}).get("account_on_file_display", (vendor or {}).get("remit_account_number", "")))
        if require_on_file and (vendor is None or number != trusted_number):
            raise BankError("Payment destination no longer matches the trusted vendor master.")
        directory = [item for item in self.store.list_records("bank_directory") if item["mode"] == self.bank.mode]
        sources = [item for item in directory if item["role"] == "company"]
        targets = [item for item in directory if account_number(item["display_number"]) == number]
        if len(sources) != 1 or len(targets) != 1 or sources[0]["account_id"] == targets[0]["account_id"]:
            raise BankError("Bank directory is not seeded for this payment destination.")
        return sources[0], targets[0]

    async def execute(self, *, run_id, invoice_id, invoice_number, vendor_id,
                      amount_cents, to_display_number, via, effective_mode=None,
                      require_on_file=None, created_by="person-ap-agent", approval_id=None) -> Payment:
        """Return and emit only the bank's validated result; no service fallback.

        A failure after reservation is conservatively uncertain. That invoice
        cannot be submitted again until an operator reconciles the saved bank
        attempts. Preflight failures happen before reservation.
        """
        if via not in {"policy", "approval"}:
            raise ValueError("Unsupported payment authority")
        require_on_file = True if via == "policy" else bool(require_on_file)
        async with self._lock:
            source, target = self.prepare(vendor_id=vendor_id, amount_cents=amount_cents,
                                          to_display_number=to_display_number,
                                          require_on_file=require_on_file)
            await self.bus.before_payment()
            attempt = f"Countersign {via} {run_id} {invoice_id}"
            references = {}

            def authorize():
                # This runs inside the bill/event SQLite transaction, after all
                # pre-transfer awaits. Bind the exact current master, actor and
                # directory rather than trusting a stale earlier policy read.
                if approval_id is not None:
                    callback = getattr(self, "authorize_approval", None)
                    if callback is None:
                        raise BankError("Approval authority is unavailable for this payment.")
                    callback(approval_id=approval_id, created_by=created_by, vendor_id=vendor_id,
                             to_display_number=to_display_number, amount_cents=amount_cents)
                current_source, current_target = self.prepare(vendor_id=vendor_id, amount_cents=amount_cents,
                    to_display_number=to_display_number, require_on_file=require_on_file)
                if current_source["account_id"] != source["account_id"] or current_target["account_id"] != target["account_id"]:
                    raise BankError("Payment directory changed before submission; review is required.")
                references.update(verified_invoice_references(self.store, run_id, invoice_id))
                if not self.store.claim_session_payment(invoice_number, attempt,
                        vendor_id=vendor_id, amount_cents=amount_cents, run_id=run_id,
                        invoice_id=invoice_id, **references):
                    raise DuplicatePaymentError("Invoice number or vendor/amount/date-or-PO is already paid or pending")

            await self.bus.accept_bill(run_id=run_id, invoice_id=invoice_id,
                invoice_number=invoice_number, vendor_id=vendor_id,
                amount_cents=amount_cents, created_by=created_by, authorize=authorize,
                mode="offline" if self.bank.mode == "offline" or effective_mode == "offline" else "live")
            try:
                result = await self.bank.transfer(
                    from_account_id=source["account_id"], to_account_id=target["account_id"],
                    amount_cents=amount_cents, description=attempt,
                )
                raw = result.model_dump(mode="python") if isinstance(result, BaseModel) else dict(result)
                payment = Payment.model_validate({key: raw[key] for key in Payment.model_fields if key in raw})
                if (not payment.transfer_id or not payment.transfer_status
                        or payment.transfer_status.casefold() in _FAILED
                        or payment.bank_mode != self.bank.mode
                        or payment.from_account_id != source["account_id"]
                        or payment.to_account_id != target["account_id"]
                        or account_number(payment.to_display_number) != account_number(to_display_number)
                        or payment.amount_cents != amount_cents
                        or (require_on_file and not payment.payee_on_file)):
                    raise BankError("Bank payment result requires reconciliation.")
                previous = self.store.get_record("transfers", payment.transfer_id) or {}
                self.store.save_record("transfers", payment.transfer_id, {
                    **previous, **raw, "run_id": run_id, "invoice_id": invoice_id,
                    "invoice_number": invoice_number, "vendor_id": vendor_id,
                    "session_started_at": self.store.session_started_at.isoformat(),
                    "created_by": created_by, "approval_id": approval_id,
                    **references,
                    "via": via, "lane": "countersign", "ts": previous.get("ts", datetime.now(timezone.utc).isoformat()),
                })
                self.store.finish_session_payment(invoice_number, state="submitted", transfer_id=payment.transfer_id)
            except BaseException:
                self.store.finish_session_payment(invoice_number, state="uncertain")
                raise
            await self.bus.publish(run_id, {
                "type": "payment.executed", "lane": "countersign",
                "mode": "offline" if self.bank.mode == "offline" or effective_mode == "offline" else "live",
                "payload": {"invoice_id": invoice_id, "via": via, "payment": payment.model_dump(mode="python")},
            })
            return payment
