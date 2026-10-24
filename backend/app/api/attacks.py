"""Judge-supplied invoice attacks; no model or bank calls during creation."""

import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.contract_models import AttackRequest, Batch, ErrorResponse
from app.world.batches import create_attack_batch


router = APIRouter()


@router.post("/attacks", response_model=Batch, operation_id="create_attack",
             responses={404: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
async def create_attack(body: AttackRequest, request: Request) -> Batch:
    store = request.app.state.store
    if store.get_record("vendors", body.vendor_id) is None:
        raise HTTPException(404, "Vendor not found")
    reservation = uuid4().hex
    retry_after = store.reserve_attack_batch(reservation, time.time())
    if retry_after:
        raise HTTPException(429, "Attack batch limit reached: at most 10 attack batches per minute. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    try:
        return await run_in_threadpool(create_attack_batch, store, vendor_id=body.vendor_id,
                                       attack_text=body.attack_text, placement=body.placement)
    except ValueError as error:
        store.release_attack_batch(reservation)
        raise HTTPException(422, f"Attack text could not be rendered: {error}") from None
    except Exception:
        store.release_attack_batch(reservation)
        raise
