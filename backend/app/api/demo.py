from fastapi import APIRouter, Request

from app.contract_models import AutopilotRequest, DemoResetRequest, ResetResponse
from app.world.seed import seed_world
from app.pipeline.runner import RunManager
from app.pipeline.autopilot import AutopilotManager


router = APIRouter()


@router.post("/demo/reset", response_model=ResetResponse, operation_id="reset_demo")
async def reset_demo(request: Request, body: DemoResetRequest | None = None) -> ResetResponse:
    await request.app.state.autopilot_manager.close()
    await request.app.state.run_manager.close()
    # A session boundary cannot erase a reservation while a human-approved
    # transfer is in flight. Match the approval->payment lock ordering.
    async with request.app.state.approval_manager._lock:
        async with request.app.state.payment_executor._lock:
            await seed_world(request.app.state.store, request.app.state.adapters.bank)
            started = request.app.state.store.reset_demo()
            request.app.state.run_manager = RunManager(
                request.app.state.store, request.app.state.event_bus,
                request.app.state.adapters, request.app.state.settings,
                request.app.state.payment_executor,
            )
            request.app.state.autopilot_manager = AutopilotManager(
                request.app.state.store, request.app.state.event_bus, request.app.state.run_manager,
            )
            request.app.state.autopilot_manager.start()
    # The reset clears autopilot state (off). The optional body turns it back on for both lanes.
    if body is not None and body.autopilot:
        await request.app.state.autopilot_manager.set(AutopilotRequest(enabled=True, lanes=["countersign", "unguarded"]))
    return ResetResponse(session_started_at=started)
