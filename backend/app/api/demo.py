from fastapi import APIRouter, Request

from app.contract_models import ResetResponse


router = APIRouter()


@router.post("/demo/reset", response_model=ResetResponse, operation_id="reset_demo")
async def reset_demo(request: Request) -> ResetResponse:
    return ResetResponse(session_started_at=request.app.state.store.reset_demo())
