"""Authorize and resolve each human countersign at most once."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re

from fastapi import HTTPException

from app.adapters.bank import BankError
from app.contract_models import Approval, ApprovalDecision, ApprovalResolution, Person
from app.pipeline.payments import DuplicatePaymentError, account_number, refresh_run_tallies


class ApprovalManager:
    def __init__(self, store, bus, payments):
        self.store, self.bus, self.payments = store, bus, payments
        self._lock = asyncio.Lock()

    def list(self, status=None) -> list[Approval]:
        approvals = [Approval.model_validate(row) for row in self.store.list_records("approvals")]
        return [item for item in approvals if status is None or item.status == status]

    def _authorize(self, approval, body):
        row = self.store.get_record("people", body.actor_id)
        if row is None:
            raise HTTPException(403, "The countersign actor is not authorized")
        actor = Person.model_validate(row)
        allowed = {"controller"} if approval.required_role == "controller" else {"ap_manager", "controller"}
        if (actor.person_id == approval.requested_by or actor.role not in allowed
                or approval.required_role not in {"ap_manager", "controller"}
                or (actor.approval_limit_cents is not None and approval.amount_cents > actor.approval_limit_cents)):
            raise HTTPException(403, "The countersign actor is not authorized for this payment")

    async def resolve(self, approval_id: str, body: ApprovalDecision) -> Approval:
        async with self._lock:
            raw = self.store.get_record("approvals", approval_id)
            if raw is None:
                raise HTTPException(404, "Approval not found")
            approval = Approval.model_validate(raw)
            if approval.status != "pending" or self.store.get_record("approval_attempts", approval_id):
                raise HTTPException(409, "Approval is already resolved or requires reconciliation")
            self._authorize(approval, body)
            if self.store.get_run(approval.run_id) is None:
                raise HTTPException(409, "Approval run is unavailable")
            context = self.store.get_record("approval_contexts", approval_id)
            if context is None or context.get("session_started_at") != self.store.session_started_at.isoformat():
                raise HTTPException(409, "Approval context is unavailable or belongs to an earlier session")
            if any(context.get(key) != getattr(approval, key) for key in ("run_id", "invoice_id", "invoice_number")):
                raise HTTPException(409, "Approval context does not match the requested payment")
            payment_args = None
            if body.decision != "reject":
                vendor_id = context.get("vendor_id")
                vendor = self.store.get_record("vendors", vendor_id) if vendor_id else None
                if vendor is None:
                    raise HTTPException(409, "Approval has no trusted vendor binding")
                if body.decision == "approve_new_account":
                    requested = account_number(approval.requested_account_display)
                    if (not re.fullmatch(r"[0-9]{4,34}", requested)
                            or body.confirm_last4 is None
                            or not re.fullmatch(r"[0-9]{4}", body.confirm_last4)
                            or body.confirm_last4 != requested[-4:]):
                        raise HTTPException(422, "confirm_last4 must match the requested account's last four digits")
                    destination, on_file = approval.requested_account_display, False
                else:
                    destination = vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))
                    on_file = True
                payment_args = dict(
                    run_id=approval.run_id, invoice_id=approval.invoice_id,
                    invoice_number=context["invoice_number"], vendor_id=vendor_id,
                    amount_cents=approval.amount_cents, to_display_number=destination,
                    via="approval", require_on_file=on_file, effective_mode=context.get("mode"),
                )
                # Known configuration errors do not consume the human's one
                # resolution attempt. Uncertain bank results do consume it.
                self.payments.prepare(vendor_id=vendor_id, amount_cents=approval.amount_cents,
                                      to_display_number=destination, require_on_file=on_file)
                if self.store.has_session_payment(context["invoice_number"]):
                    raise HTTPException(409, "Invoice already paid or reserved in this session")
            if not self.store.claim_approval_resolution(approval_id, body.decision, body.actor_id):
                raise HTTPException(409, "Approval is already being resolved")
            payment = None
            try:
                if payment_args is not None:
                    payment = await self.payments.execute(**payment_args)
            except DuplicatePaymentError:
                raise HTTPException(409, "Invoice already paid or reserved in this session") from None
            except asyncio.CancelledError:
                raise
            except Exception:
                # The durable approval/payment reservations intentionally remain
                # locked: an ambiguous transfer must be reconciled, not retried.
                raise BankError("Approval payment requires reconciliation before another attempt.") from None
            resolved_at = datetime.now(timezone.utc)
            resolution = ApprovalResolution(
                decision=body.decision, resolved_at=resolved_at,
                **({"note": body.note} if body.note is not None else {}),
                **({"transfer_id": payment.transfer_id} if payment is not None else {}),
            )
            approval = approval.model_copy(update={"status": "resolved", "resolution": resolution})
            self.store.save_record("approvals", approval_id, approval)
            attempt = self.store.get_record("approval_attempts", approval_id)
            self.store.save_record("approval_attempts", approval_id, {**attempt, "state": "resolved",
                "resolved_at": resolved_at.isoformat(),
                **({"transfer_id": payment.transfer_id} if payment is not None else {})})
            await self.bus.publish(approval.run_id, {
                "type": "approval.resolved", "lane": "countersign",
                "mode": "offline" if context.get("mode") == "offline" or self.payments.bank.mode == "offline" else "live",
                "payload": {"approval_id": approval_id, "invoice_id": approval.invoice_id,
                            "decision": body.decision, **({"note": body.note} if body.note is not None else {})},
            })
            refresh_run_tallies(self.store, approval.run_id)
            return approval
