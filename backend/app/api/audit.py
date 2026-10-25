"""Auditor questions are answered only by the configured Elastic agent."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.contract_models import AuditAnswer, AuditAskRequest, ErrorDetail, ErrorResponse
from app.security.presentation import display_model


router = APIRouter()

# Agent Builder runs its own tool queries and model calls; measured answers took 15-95 s.
# Stop waiting at 140 s with a clear 504 rather than leaving the caller hanging.
AUDIT_ASK_TIMEOUT_SECONDS = 140.0
TIMEOUT_MESSAGE = ("The Elastic auditor did not answer within 140 s. Agent Builder runs its own queries "
                   "and model calls; try again, or ask about one run.")
RATE_LIMIT_MESSAGE = ("Elastic Agent Builder is rate limiting requests (HTTP 429 from Elastic); "
                      "wait a minute and ask again.")


def _error(status, code, message):
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


@router.post("/audit/ask", response_model=AuditAnswer, operation_id="ask_audit",
             responses={503: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        429: {"model": ErrorResponse}, 504: {"model": ErrorResponse}})
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
        answer = AuditAnswer.model_validate(
            await asyncio.wait_for(sink.ask(body), timeout=AUDIT_ASK_TIMEOUT_SECONDS))
        return display_model(answer)
    except TimeoutError:
        return _error(504, "service_unavailable", TIMEOUT_MESSAGE)
    except Exception as error:
        if getattr(error, "http_status", None) == 429:
            return _error(429, "rate_limited", RATE_LIMIT_MESSAGE)
        return _error(503, "service_unavailable",
                      "Elastic Agent Builder audit is unavailable; check Elastic health and agent configuration.")
