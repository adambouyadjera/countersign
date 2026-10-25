"""Contract routes for persistent, bank-capability-denied red-team jobs."""

from fastapi import APIRouter, HTTPException, Request

from app.contract_models import ErrorResponse, RedTeamJob, RedTeamRequest
from app.security.presentation import display_model


router = APIRouter()


@router.post("/redteam", response_model=RedTeamJob, operation_id="create_redteam_job")
async def create_redteam_job(body: RedTeamRequest, request: Request):
    return display_model(request.app.state.redteam_manager.start(body))


@router.get("/redteam/{job_id}", response_model=RedTeamJob, operation_id="get_redteam_job",
            responses={404: {"model": ErrorResponse}})
async def get_redteam_job(job_id: str, request: Request):
    job = request.app.state.redteam_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Red-team job not found")
    return display_model(job)
