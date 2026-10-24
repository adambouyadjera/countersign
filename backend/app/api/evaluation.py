"""Contract routes for durable dry-run planted-error jobs."""

from fastapi import APIRouter, HTTPException, Request

from app.contract_models import ErrorResponse, EvalJob, EvalRequest


router = APIRouter()


@router.post("/eval", response_model=EvalJob, operation_id="create_eval_job")
async def create_eval_job(body: EvalRequest, request: Request):
    if body.lanes is not None:
        if not body.lanes or len(set(body.lanes)) != len(body.lanes):
            raise HTTPException(422, "Evaluation lanes must be nonempty and distinct")
        if "countersign" not in body.lanes:
            raise HTTPException(422, "The Countersign lane is required; the unguarded lane is optional")
    return request.app.state.eval_manager.start(body)


@router.get("/eval/{job_id}", response_model=EvalJob, operation_id="get_eval_job",
            responses={404: {"model": ErrorResponse}})
async def get_eval_job(job_id: str, request: Request):
    job = request.app.state.eval_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Evaluation job not found")
    return job
