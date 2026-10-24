from fastapi import APIRouter, Request

from app.contract_models import ResetResponse
from app.world.seed import seed_world
from app.pipeline.runner import RunManager


router = APIRouter()


@router.post("/demo/reset", response_model=ResetResponse, operation_id="reset_demo")
async def reset_demo(request: Request) -> ResetResponse:
    await request.app.state.run_manager.close()
    await seed_world(request.app.state.store, request.app.state.adapters.bank)
    started = request.app.state.store.reset_demo()
    request.app.state.run_manager = RunManager(
        request.app.state.store, request.app.state.event_bus,
        request.app.state.adapters, request.app.state.settings,
    )
    return ResetResponse(session_started_at=started)
