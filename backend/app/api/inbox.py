"""Contract endpoints for AP inbox intake and autonomous processing settings."""

from fastapi import APIRouter, Request

from app.contract_models import Autopilot, AutopilotRequest, ErrorResponse, InboxItem, InboxRequest


router = APIRouter()


@router.post("/inbox", response_model=InboxItem, operation_id="create_inbox_item",
             responses={404: {"model": ErrorResponse}})
async def create_inbox_item(body: InboxRequest, request: Request):
    return request.app.state.autopilot_manager.enqueue(body)


@router.get("/inbox", response_model=list[InboxItem], operation_id="list_inbox_items")
async def list_inbox_items(request: Request):
    return request.app.state.autopilot_manager.list()


@router.get("/autopilot", response_model=Autopilot, operation_id="get_autopilot")
async def get_autopilot(request: Request):
    return request.app.state.autopilot_manager.get()


@router.post("/autopilot", response_model=Autopilot, operation_id="set_autopilot")
async def set_autopilot(body: AutopilotRequest, request: Request):
    return await request.app.state.autopilot_manager.set(body)
