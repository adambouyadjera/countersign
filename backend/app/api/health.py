from fastapi import APIRouter, Request

from app.contract_models import Health


router = APIRouter()


@router.get("/health", response_model=Health, operation_id="get_health")
async def get_health(request: Request) -> Health:
    return Health(
        services=await request.app.state.health_checker.check(),
        session_started_at=request.app.state.store.session_started_at,
    )
