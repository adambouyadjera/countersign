"""Read-only demo replay: saved evidence only, no run or bank operations."""

from fastapi import APIRouter, HTTPException, Request

from app.contract_models import BankChangeReplay, BankChangeReplaySummary, ErrorResponse
from app.evidence import EvidenceUnavailable


router = APIRouter()


@router.get("/evidence/bank-change", response_model=list[BankChangeReplaySummary],
            operation_id="list_bank_change_evidence")
def list_bank_change_evidence(request: Request):
    try:
        return request.app.state.evidence_repository.list_runs()
    except EvidenceUnavailable:
        raise HTTPException(503, "Recorded bank-change evidence is unavailable") from None


@router.get("/evidence/bank-change/{run_id}", response_model=BankChangeReplay,
            operation_id="get_bank_change_evidence", responses={404: {"model": ErrorResponse}})
def get_bank_change_evidence(run_id: str, request: Request):
    try:
        return request.app.state.evidence_repository.get_run(run_id)
    except EvidenceUnavailable:
        raise HTTPException(503, "Recorded bank-change evidence is unavailable") from None
    except LookupError:
        raise HTTPException(404, "Recorded run not found") from None
