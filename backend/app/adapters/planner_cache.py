"""Validated planner-response reuse for a dry-run red-team job.

The normal planner pipeline still builds and leak-checks every prompt, records
the exact response, and validates it again. This transport wrapper neither
receives document values nor changes a prompt. Cache receipts live outside the
contract transcript so a reused response cannot masquerade as a provider call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from app.contract_models import Handle
from app.pipeline.planner import _Plan, _no_duplicate_keys
from app.security.handles import HandleTable
from app.security.interpreter import validate_plan


CACHE_TABLE = "redteam_planner_cache"
_VERSION = 1


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def planner_prompt_hash(prompt: str) -> str:
    """Use the exact bytes hashed by pipeline.planner.propose, not normalized JSON."""
    return hashlib.sha256(prompt.encode()).hexdigest()


def planner_cache_key(*, provider: str, model: str, mode: str, prompt_hash: str) -> str:
    identity = json.dumps([_VERSION, provider, model, mode, prompt_hash],
                          ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def _valid_response(prompt, response):
    """Run the same schema/DSL validators using only trusted prompt metadata.

    DSL validation needs invoice aliases, handle metadata and trusted vendor
    IDs, never handle values. The pipeline validates again against its actual
    table before any execution; this extra check only decides whether to store
    the provider response. Invalid responses pass through unchanged for its
    ordinary retry/PLANNER_INVALID behavior.
    """
    try:
        if not isinstance(response, str) or len(response) > 200_000:
            return False
        request = json.loads(prompt, object_pairs_hook=_no_duplicate_keys)
        metadata = json.loads(request["messages"][0]["content"], object_pairs_hook=_no_duplicate_keys)
        table = HandleTable()
        for invoice in metadata["invoices"]:
            alias = invoice["invoice_id"]
            if alias in table.invoices:
                return False
            handles = [Handle.model_validate(handle) for handle in invoice["handles"]]
            table.invoices[alias] = alias
            table.handles.extend(handles)
            table.invoice_handles[alias] = handles
        vendors = metadata["vendors"]
        # Current DSL validation needs PO metadata only for execution, which
        # remains outside this adapter; preserve the trusted open IDs here.
        orders = [{"purchase_order_id": po, "vendor_id": vendor["vendor_id"], "status": "open"}
                  for vendor in vendors for po in vendor["open_po_ids"]]
        data = json.loads(response, object_pairs_hook=_no_duplicate_keys)
        validate_plan(_Plan.model_validate(data).steps, table, vendors, orders)
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class _Response:
    response: str
    created_at: str
    valid: bool
    cache_written: bool


class CachedPlannerLLM:
    """PlannerLLM-compatible, persistent cache with per-key single flight.

    Share one wrapper across a job to bound distinct provider calls to four.
    Concurrent identical prompts share one in-flight call, including invalid
    responses and errors; neither is retained in the persistent cache.
    """

    def __init__(self, *, adapter, store, provider: str, max_concurrency: int = 4):
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("Planner cache provider must be configured")
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("Planner cache concurrency must be a positive integer")
        if not isinstance(adapter.model, str) or not adapter.model:
            raise ValueError("Planner cache model must be configured")
        self.adapter, self.store = adapter, store
        self.provider, self.model, self.mode = provider, adapter.model, adapter.mode
        self.receipts: list[dict] = []
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._flights: dict[str, asyncio.Task] = {}
        self._flight_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._closed = self._close_complete = False
        self._pending_receipts: set[asyncio.Future] = set()
        self._provider_calls = 0
        self._invalid_responses = 0
        self._entries_written = 0
        self._prompts: set[str] = set()

    @property
    def stats(self) -> dict:
        return {
            "requests": len(self.receipts), "provider_calls": self._provider_calls,
            "cache_hits": sum(row.get("cached", False) for row in self.receipts),
            "coalesced": sum(row.get("coalesced", False) for row in self.receipts),
            "invalid_responses": self._invalid_responses,
            "cache_entries_written": self._entries_written,
            "errors": sum(row["status"] == "error" for row in self.receipts),
            "distinct_prompts": len(self._prompts),
        }

    async def health_check(self):
        return await self.adapter.health_check()

    def _cached(self, *, prompt, digest, key):
        try:
            entry = self.store.get_record(CACHE_TABLE, key)
            expected = {"version": _VERSION, "cache_key": key, "prompt_hash": digest,
                        "provider": self.provider, "model": self.model, "mode": self.mode}
            if not isinstance(entry, dict) or any(entry.get(k) != v for k, v in expected.items()):
                return None
            response, created_at = entry["response"], entry["created_at"]
            if not isinstance(created_at, str) or datetime.fromisoformat(created_at).tzinfo is None:
                return None
            if (not _valid_response(prompt, response)
                    or entry["response_sha256"] != hashlib.sha256(response.encode()).hexdigest()):
                return None
            return _Response(response, created_at, True, True)
        except Exception:
            # A missing, unreadable or corrupt cache cannot authorize a plan.
            # The provider and ordinary pipeline validation remain available.
            return None

    async def _produce(self, *, prompt, digest, key, receipt):
        async with self._semaphore:
            self._provider_calls += 1
            receipt["provider_call"] = True
            response = await self.adapter.complete(prompt=prompt)
        created_at = _timestamp()
        valid = _valid_response(prompt, response)
        written = False
        if valid:
            entry = {"version": _VERSION, "cache_key": key, "prompt_hash": digest,
                     "provider": self.provider, "model": self.model, "mode": self.mode,
                     "response": response, "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
                     "created_at": created_at}
            try:
                self.store.save_record(CACHE_TABLE, key, entry)
            except Exception:
                # Cache availability must not turn a valid fresh plan into a
                # failed run. Receipts report that nothing was persisted.
                pass
            else:
                written = True
                self._entries_written += 1
        else:
            self._invalid_responses += 1
        return _Response(response, created_at, valid, written)

    def _retire(self, key, task):
        if self._flights.get(key) is task:
            del self._flights[key]
        if not task.cancelled():
            # The only waiter may have been cancelled; consume an exception
            # without changing what other waiters observe when they await it.
            task.exception()

    async def complete(self, *, prompt: str) -> str:
        if self._closed:
            raise RuntimeError("Planner response cache is closed")
        if self.adapter.model != self.model or self.adapter.mode != self.mode:
            raise ValueError("Planner cache adapter identity changed; create a new wrapper")
        digest = planner_prompt_hash(prompt)
        key = planner_cache_key(provider=self.provider, model=self.model, mode=self.mode, prompt_hash=digest)
        self._prompts.add(digest)
        receipt = {"request_id": f"planner-cache-{uuid4().hex}", "cache_key": key,
                   "prompt_hash": digest, "provider": self.provider, "model": self.model,
                   "mode": self.mode, "requested_at": _timestamp(), "status": "pending",
                   "cached": False, "coalesced": False, "provider_call": False}
        self.receipts.append(receipt)
        receipt_done = asyncio.get_running_loop().create_future()
        self._pending_receipts.add(receipt_done)
        try:
            async with self._flight_lock:
                if self._closed:
                    raise RuntimeError("Planner response cache is closed")
                result = self._cached(prompt=prompt, digest=digest, key=key)
                if result is not None:
                    receipt.update(source="cache", cached=True)
                    task = None
                else:
                    task = self._flights.get(key)
                    if task is None:
                        task = asyncio.create_task(self._produce(prompt=prompt, digest=digest, key=key,
                                                                receipt=receipt))
                        self._flights[key] = task
                        task.add_done_callback(lambda done, cache_key=key: self._retire(cache_key, done))
                        receipt.update(source="provider")
                    else:
                        receipt.update(source="singleflight", coalesced=True)
            if task is not None:
                # Cancelling one variant must not cancel a shared provider call.
                result = await asyncio.shield(task)
            receipt.update(status="success", response_valid=result.valid,
                           response_sha256=hashlib.sha256(result.response.encode()).hexdigest()
                           if isinstance(result.response, str) else None,
                           original_created_at=result.created_at,
                           cache_written=result.cache_written and receipt["source"] == "provider")
            return result.response
        except asyncio.CancelledError:
            receipt["status"] = "cancelled"
            raise
        except Exception:
            receipt["status"] = "error"
            # Do not copy provider exception text or generated content to receipts.
            raise
        finally:
            receipt["completed_at"] = _timestamp()
            if not receipt_done.done():
                receipt_done.set_result(None)
            self._pending_receipts.discard(receipt_done)

    async def aclose(self):
        """Stop job-level work and settle every receipt before its final snapshot.

        A single cancelled variant still leaves a shared producer available to
        other variants. Closing the whole job explicitly cancels those shielded
        producers, including calls waiting for the concurrency semaphore.
        """
        self._closed = True
        async with self._close_lock:
            if self._close_complete:
                return
            async with self._flight_lock:
                tasks = list(self._flights.values())
                for task in tasks:
                    if not task.done():
                        task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # Producer completion wakes the complete() waiters. Wait for their
            # finally blocks too, so cancelled receipts cannot change later.
            pending = list(self._pending_receipts)
            if pending:
                await asyncio.gather(*pending)
            self._close_complete = True
