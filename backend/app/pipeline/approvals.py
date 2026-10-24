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
        payments.authorize_approval = self.authorize_execution

    def list(self, status=None) -> list[Approval]:
        approvals = [Approval.model_validate(row) for row in self.store.list_records("approvals")]
        return [item for item in approvals if status is None or item.status == status]

    @staticmethod
    def _allowed_roles(approval):
        if approval.required_role == "controller":
            return {"controller"}
        return {"ap_manager", "controller"} if approval.required_role == "ap_manager" else set()

    @staticmethod
    def _effective_limit(actor):
        # An erroneous/unlimited individual record cannot enlarge the AP
        # manager role's contractual $50,000 authority.
        if actor.role == "ap_manager":
            return min(actor.approval_limit_cents, 5_000_000) if actor.approval_limit_cents is not None else 5_000_000
        return actor.approval_limit_cents

    def _approval_guidance(self, approval, *, exclude=()):
        """Name a different eligible employee instead of suggesting a retry."""
        excluded = {approval.requested_by, *exclude}
        eligible = []
        for row in self.store.list_records("people"):
            try:
                person = Person.model_validate(row)
            except ValueError:
                continue
            limit = self._effective_limit(person)
            if (person.person_id not in excluded
                    and person.role in self._allowed_roles(approval)
                    and (limit is None or approval.amount_cents <= limit)):
                eligible.append(f"{person.name} ({person.person_id})")
        if eligible:
            return "Have " + " or ".join(eligible) + " approve instead."
        return "An independent controller with sufficient approval authority must approve instead."

    def _forbidden(self, approval, reason, *, exclude=()):
        raise HTTPException(403, reason + " " + self._approval_guidance(approval, exclude=exclude))

    def _authorize(self, approval, body) -> Person:
        row = self.store.get_record("people", body.actor_id)
        if row is None:
            self._forbidden(approval, "The approval actor is not a known authorized employee.")
        try:
            actor = Person.model_validate(row)
        except ValueError:
            self._forbidden(approval, "The approval actor has no valid role and approval limit.")
        if actor.person_id != body.actor_id:
            self._forbidden(approval, "The approval actor's stored identity does not match the requested employee.")
        if actor.person_id == approval.requested_by:
            self._forbidden(approval, f"{actor.name} requested this payment and cannot approve their own request.",
                            exclude=(actor.person_id,))
        if actor.role not in self._allowed_roles(approval):
            self._forbidden(approval, f"{actor.name} does not have the required payment approval role.",
                            exclude=(actor.person_id,))
        limit = self._effective_limit(actor)
        if limit is not None and approval.amount_cents > limit:
            self._forbidden(approval, f"This payment exceeds {actor.name}'s approval limit.",
                            exclude=(actor.person_id,))
        return actor

    def authorize_execution(self, *, approval_id, created_by, vendor_id,
                            to_display_number, amount_cents):
        """Recheck authority at the payment transaction's linearization point.

        PaymentExecutor calls this synchronously inside its store transaction,
        after network/baseline awaits and before bill posting or bank submission.
        Every identity and account below is reloaded from the current snapshot.
        """
        from app.world.vendors import latest_account_verification

        raw = self.store.get_record("approvals", approval_id)
        attempt = self.store.get_record("approval_attempts", approval_id)
        context = self.store.get_record("approval_contexts", approval_id)
        if raw is None or attempt is None or context is None:
            raise HTTPException(409, "Approval binding is unavailable before payment submission")
        approval = Approval.model_validate(raw)
        if (approval.approval_id != approval_id or approval.status != "pending"
                or attempt.get("state") != "resolving"
                or attempt.get("decision") not in {"pay_vendor_on_file", "approve_new_account"}):
            raise HTTPException(409, "Approval is no longer pending for this payment submission")
        if attempt.get("actor_id") != created_by:
            self._forbidden(approval, "The payment actor does not match the employee who countersigned this attempt.")
        actor = self._authorize(approval, ApprovalDecision(
            decision=attempt["decision"], actor_id=created_by))
        if (context.get("session_started_at") != self.store.session_started_at.isoformat()
                or self.store.get_run(approval.run_id) is None
                or any(context.get(key) != getattr(approval, key)
                       for key in ("run_id", "invoice_id", "invoice_number"))
                or context.get("vendor_id") != vendor_id
                or type(amount_cents) is not int or amount_cents != approval.amount_cents):
            raise HTTPException(409, "Approval context or amount changed before payment submission; review again")
        vendor = self.store.get_record("vendors", vendor_id)
        if vendor is None:
            raise HTTPException(409, "Approval no longer has a trusted vendor binding")
        destination = account_number(to_display_number)
        trusted = account_number(vendor.get("account_on_file_display", vendor.get("remit_account_number", "")))
        if not destination or destination != trusted:
            raise HTTPException(409, "The vendor account changed before payment submission; review the current account on file")
        verification = latest_account_verification(self.store, vendor_id, destination)
        if attempt["decision"] == "approve_new_account":
            if (destination != account_number(approval.requested_account_display) or verification is None):
                self._forbidden(approval,
                    "A vendor_admin must first record and verify the requested account in the vendor master; "
                    "then a different ap_manager or controller can approve the payment.")
        if verification is not None and actor.person_id == verification.get("verified_by"):
            self._forbidden(approval,
                f"{actor.name} verified this account and cannot also approve its payment.",
                exclude=(actor.person_id,))

    def _release_prebank_denial(self, approval_id, actor_id, invoice_number):
        """Release only a known unsubmitted attempt; uncertain money stays locked."""
        with self.store.transaction() as connection:
            attempt = self.store.get_record("approval_attempts", approval_id)
            if (attempt is not None and attempt.get("state") == "resolving"
                    and attempt.get("actor_id") == actor_id
                    and not self.store.has_session_payment(invoice_number)):
                connection.execute("DELETE FROM approval_attempts WHERE approval_id = ?", (approval_id,))

    async def resolve(self, approval_id: str, body: ApprovalDecision) -> Approval:
        async with self._lock:
            raw = self.store.get_record("approvals", approval_id)
            if raw is None:
                raise HTTPException(404, "Approval not found")
            approval = Approval.model_validate(raw)
            if approval.status != "pending" or self.store.get_record("approval_attempts", approval_id):
                raise HTTPException(409, "Approval is already resolved or requires reconciliation")
            actor = self._authorize(approval, body)
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
                from app.world.vendors import latest_account_verification

                verification = None
                if body.decision == "approve_new_account":
                    requested = account_number(approval.requested_account_display)
                    if (not re.fullmatch(r"[0-9]{4,34}", requested)
                            or body.confirm_last4 is None
                            or not re.fullmatch(r"[0-9]{4}", body.confirm_last4)
                            or body.confirm_last4 != requested[-4:]):
                        raise HTTPException(422, "confirm_last4 must match the requested account's last four digits")
                    trusted = account_number(vendor.get("account_on_file_display", vendor.get("remit_account_number", "")))
                    verification = latest_account_verification(self.store, vendor_id, requested)
                    if trusted != requested or verification is None:
                        self._forbidden(approval,
                            "A vendor_admin must first record and verify the requested account in the vendor master; "
                            "then a different ap_manager or controller can approve the payment.")
                    destination, on_file = approval.requested_account_display, True
                else:
                    destination = vendor.get("account_on_file_display", vendor.get("remit_account_number", ""))
                    on_file = True
                    verification = latest_account_verification(self.store, vendor_id, account_number(destination))
                # Selecting "on file" after a bank change must not let the
                # employee who verified that account also authorize its payment.
                if verification is not None and actor.person_id == verification.get("verified_by"):
                    self._forbidden(approval,
                        f"{actor.name} verified this account and cannot also approve its payment.",
                        exclude=(actor.person_id,))
                payment_args = dict(
                    run_id=approval.run_id, invoice_id=approval.invoice_id,
                    invoice_number=context["invoice_number"], vendor_id=vendor_id,
                    amount_cents=approval.amount_cents, to_display_number=destination,
                    via="approval", require_on_file=on_file, effective_mode=context.get("mode"),
                    created_by=actor.person_id, approval_id=approval_id,
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
            except HTTPException:
                # Execution revalidation denies before the invoice reservation,
                # ledger posting and bank call. Preserve its explicit 403/409;
                # do not convert a known authorization failure to bank ambiguity.
                self._release_prebank_denial(approval_id, body.actor_id, context["invoice_number"])
                raise
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
