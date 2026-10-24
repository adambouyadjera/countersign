from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.contract_models import ErrorResponse, PolicyDecision, Run, RunRequest


router = APIRouter()


@router.post("/runs", response_model=Run, operation_id="create_run",
             responses={404: {"model": ErrorResponse}})
async def create_run(body: RunRequest, request: Request) -> Run:
    return request.app.state.run_manager.start(body)


@router.get("/runs/{run_id}", response_model=Run, operation_id="get_run",
            responses={404: {"model": ErrorResponse}})
async def get_run(run_id: str, request: Request) -> Run:
    run = request.app.state.store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}/decisions", response_model=list[PolicyDecision], operation_id="get_run_decisions",
            responses={404: {"model": ErrorResponse}})
async def get_run_decisions(run_id: str, request: Request) -> list[PolicyDecision]:
    if request.app.state.store.get_run(run_id) is None:
        raise HTTPException(404, "Run not found")
    return [PolicyDecision.model_validate(record) for record in request.app.state.store.list_records("decisions", run_id=run_id)]


@router.get(
    "/runs/{run_id}/events",
    operation_id="get_run_events",
    response_class=EventSourceResponse,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/RunEvent"}}}},
        404: {"model": ErrorResponse},
    },
)
async def get_run_events(
    run_id: str,
    request: Request,
    last_event_id: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0,
) -> EventSourceResponse:
    if request.app.state.store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def messages() -> AsyncIterator[ServerSentEvent]:
        async for event in request.app.state.event_bus.subscribe(run_id, last_event_id):
            yield ServerSentEvent(
                id=str(event.seq), event=event.type, data=event.model_dump_json()
            )

    return EventSourceResponse(
        messages(),
        ping=15,
        ping_message_factory=lambda: ServerSentEvent(comment="heartbeat"),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
