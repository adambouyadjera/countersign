"""Trusted vendor maintenance and read-only books/reconciliation views."""

from fastapi import APIRouter, Request

from app.contract_models import (
    BankChangeRequest, ErrorResponse, JournalEntry, Reconciliation, TrialBalance, VendorDetail,
)
from app.world.vendors import record_bank_change, vendor_detail


router = APIRouter()


@router.get("/vendors/{vendor_id}", response_model=VendorDetail, operation_id="get_vendor",
            responses={404: {"model": ErrorResponse}})
def get_vendor(vendor_id: str, request: Request):
    return vendor_detail(request.app.state.store, vendor_id)


@router.post("/vendors/{vendor_id}/bank_change", response_model=VendorDetail,
             operation_id="change_vendor_bank", responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}})
async def bank_change(vendor_id: str, body: BankChangeRequest, request: Request):
    # A master change cannot interleave with an authorized payment's slow bank
    # preflight/submission in this process. Cross-worker freshness is checked
    # again in the payment's final SQLite authorization transaction.
    async with request.app.state.payment_executor._lock:
        return record_bank_change(request.app.state.store, vendor_id, body.actor_id,
            body.new_account_display, body.evidence_note, bank_mode=request.app.state.adapters.bank.mode)


@router.get("/ledger/entries", response_model=list[JournalEntry], operation_id="list_journal_entries")
def ledger_entries(request: Request, run_id: str | None = None, invoice_id: str | None = None):
    return request.app.state.ledger.list_entries(run_id=run_id, invoice_id=invoice_id)


@router.get("/ledger/trial_balance", response_model=TrialBalance, operation_id="get_trial_balance")
def trial_balance(request: Request):
    return request.app.state.ledger.trial_balance()


@router.get("/reconciliation", response_model=Reconciliation, operation_id="get_reconciliation")
async def reconciliation(request: Request):
    return await request.app.state.reconciler.reconcile()
