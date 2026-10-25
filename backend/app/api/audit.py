"""Auditor questions are answered only by the configured Elastic agent."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.contract_models import AuditAnswer, AuditAskRequest, ErrorDetail, ErrorResponse
from app.security.presentation import display_model


router = APIRouter()


def _error(status, code, message):
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@router.post("/audit/ask", response_model=AuditAnswer, operation_id="ask_audit",
             responses={503: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
async def ask_audit(body: AuditAskRequest, request: Request):
    if not body.question.strip():
        return _error(422, "validation_error", "Audit question must be nonempty.")
    sink = request.app.state.adapters.audit
    if sink.mode == "offline":
        return _error(503, "service_unavailable",
                      "Elastic audit is offline (ELASTIC_MODE=offline); Agent Builder answers are unavailable.")
    try:
        # Sink.ask flushes the durable event outbox before converse. Historical
        # run IDs remain valid even when SQLite no longer retains their runs.
        answer = AuditAnswer.model_validate(await sink.ask(body))
        return display_model(answer)
    except Exception:
        return _error(503, "service_unavailable",
                      "Elastic Agent Builder audit is unavailable; check Elastic health and agent configuration.")
