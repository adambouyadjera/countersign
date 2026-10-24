"""Contract routes for human countersigns and their trusted actor identities."""

from typing import Literal

from fastapi import APIRouter, Request

from app.contract_models import Approval, ApprovalDecision, ErrorResponse, Person


router = APIRouter()


@router.get("/approvals", response_model=list[Approval], operation_id="get_approvals")
async def get_approvals(request: Request, status: Literal["pending", "resolved"] | None = None):
    return request.app.state.approval_manager.list(status)


@router.post("/approvals/{approval_id}", response_model=Approval, operation_id="resolve_approval",
             responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
async def resolve_approval(approval_id: str, body: ApprovalDecision, request: Request):
    return await request.app.state.approval_manager.resolve(approval_id, body)


@router.get("/people", response_model=list[Person], operation_id="get_people")
async def get_people(request: Request):
    return [Person.model_validate(row) for row in request.app.state.store.list_records("people")]
