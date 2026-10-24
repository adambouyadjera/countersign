"""Contract endpoints for generated source documents and real bank balances."""

import base64

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.contract_models import Batch, BatchRequest, Balances, DocumentText, ErrorResponse
from app.world.batches import create_bank_change_batch, create_standard_batch
from app.security.presentation import display_model


router = APIRouter()


@router.post("/batches", response_model=Batch, operation_id="create_batch")
async def create_batch(body: BatchRequest, request: Request) -> Batch:
    factory = create_bank_change_batch if body.kind == "bank_change" else create_standard_batch
    return await run_in_threadpool(factory, request.app.state.store)


@router.get(
    "/batches/{batch_id}", response_model=Batch, operation_id="get_batch",
    responses={404: {"model": ErrorResponse}},
)
async def get_batch(batch_id: str, request: Request) -> Batch:
    record = request.app.state.store.get_record("batches", batch_id)
    if record is None:
        raise HTTPException(404, "Batch not found")
    return Batch.model_validate(record)


def _document(request: Request, document_id: str) -> dict:
    record = request.app.state.store.get_record("documents", document_id)
    if record is None:
        raise HTTPException(404, "Document not found")
    return record


@router.get(
    "/documents/{document_id}", response_model=DocumentText, operation_id="get_document",
    responses={404: {"model": ErrorResponse}},
)
async def get_document(document_id: str, request: Request) -> DocumentText:
    return display_model(DocumentText.model_validate(_document(request, document_id)["text"]))


@router.get(
    "/documents/{document_id}/pdf", operation_id="get_document_pdf",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}},
        404: {"model": ErrorResponse},
    },
)
async def get_document_pdf(document_id: str, request: Request) -> Response:
    pdf = base64.b64decode(_document(request, document_id)["pdf_base64"], validate=True)
    return Response(
        pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{document_id}.pdf"'},
    )


@router.get("/balances", response_model=Balances, operation_id="get_balances")
async def get_balances(request: Request) -> Balances:
    result = await request.app.state.adapters.bank.get_balances(
        request.app.state.store.session_started_at,
    )
    roles = [account.role for account in result.accounts]
    if (roles.count("company"), roles.count("vendor"), roles.count("attacker")) != (1, 5, 1):
        raise HTTPException(503, "Bank world is not completely seeded")
    return result
