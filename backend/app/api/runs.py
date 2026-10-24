from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.contract_models import AgentTranscript, Batch, ErrorResponse, PlannerTranscript, PolicyDecision, ProvenanceGraph, Run, RunRequest
from app.security.graph import overlay_resolution


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


@router.get("/runs/{run_id}/planner_transcript", response_model=PlannerTranscript,
            operation_id="get_planner_transcript", responses={404: {"model": ErrorResponse}})
async def get_planner_transcript(run_id: str, request: Request) -> PlannerTranscript:
    if request.app.state.store.get_run(run_id) is None:
        raise HTTPException(404, "Run not found")
    record = request.app.state.store.get_record("planner_transcripts", run_id)
    return PlannerTranscript.model_validate(record or {
        "run_id": run_id, "calls": [],
        "leak_guard": {"strings_checked": 0, "canaries_checked": 0, "matches": 0},
    })


@router.get("/runs/{run_id}/invoices/{invoice_id}/agent_transcript", response_model=AgentTranscript,
            operation_id="get_agent_transcript", responses={404: {"model": ErrorResponse}})
async def get_agent_transcript(run_id: str, invoice_id: str, request: Request) -> AgentTranscript:
    store = request.app.state.store
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    batch = store.get_record("batches", run.batch_id)
    if batch is None or invoice_id not in {item["invoice_id"] for item in batch["invoices"]}:
        raise HTTPException(404, "Invoice does not belong to this run")
    record = store.get_record("agent_transcripts", f"{run_id}:{invoice_id}")
    if record is None:
        raise HTTPException(404, "Agent transcript is not available")
    payload = {key: value for key, value in record.items() if key != "transcript_id"}
    # Older runs predate provider recording. Never relabel historical calls
    # using today's configured endpoint.
    if not payload.get("provider"):
        payload["provider"] = "legacy_unknown"
    return AgentTranscript.model_validate(payload)


@router.get("/runs/{run_id}/invoices/{invoice_id}/graph", response_model=ProvenanceGraph,
            operation_id="get_invoice_graph", responses={404: {"model": ErrorResponse}})
async def get_invoice_graph(run_id: str, invoice_id: str, request: Request) -> ProvenanceGraph:
    store = request.app.state.store
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    batch_record = store.get_record("batches", run.batch_id)
    batch = Batch.model_validate(batch_record) if batch_record is not None else None
    if batch is None or invoice_id not in {invoice.invoice_id for invoice in batch.invoices}:
        raise HTTPException(404, "Invoice does not belong to this run")
    record = store.get_record("graphs", f"{run_id}:{invoice_id}")
    if record is None:
        raise HTTPException(404, "Provenance graph is not available")
    graph = ProvenanceGraph.model_validate({key: value for key, value in record.items() if key != "graph_id"})
    if graph.run_id != run_id or graph.invoice_id != invoice_id:
        raise HTTPException(404, "Provenance graph is not available")
    return overlay_resolution(graph, store.list_events(run_id), store.list_records("approvals", run_id=run_id))


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
