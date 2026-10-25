"""Elasticsearch bulk delivery from the durable event outbox, never finance I/O."""

from __future__ import annotations

import asyncio
import inspect
import json
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr, TypeAdapter

from app.audit.events import EVENT_MAPPINGS, INDEX_NAME
from app.contract_models import RunEvent, ServiceHealth


_EVENT = TypeAdapter(RunEvent)


def _transient(status):
    return status in {408, 429} or 500 <= status <= 599


class _DeliveryError(RuntimeError):
    def __init__(self, summary, *, retryable=False):
        self.summary, self.retryable = summary, retryable
        super().__init__(summary)


class LiveAuditSink:
    mode = "live"

    def __init__(self, *, key, base_url, store, kibana_url="", agent_id="countersign-audit-analyst",
                 transport=None, batch_size=100, flush_interval=0.5, max_attempts=3):
        if type(batch_size) is not int or batch_size < 1 or type(max_attempts) is not int or max_attempts < 1 or flush_interval <= 0:
            raise ValueError("Elastic buffer size, interval and retry budget must be positive")
        self._key = key if isinstance(key, SecretStr) else SecretStr(key)
        self._base_url = base_url.rstrip("/")
        self.store, self.kibana_url, self.agent_id = store, kibana_url, agent_id
        self._transport = transport
        self.batch_size, self.flush_interval, self.max_attempts = batch_size, flush_interval, max_attempts
        self._wake = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self._worker = None
        self._closed = False
        self._failure = None
        self._agent_failure = None
        self._on_failure = None
        self._notified_runs = set()
        self._index_ready = False
        self._sleep = asyncio.sleep
        self.agent_client = None

    def _configuration(self):
        if not self._key.get_secret_value():
            raise _DeliveryError("ELASTIC_API_KEY is not configured.")
        parsed = urlsplit(self._base_url)
        if (parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise _DeliveryError("The Elasticsearch project URL is not configured correctly.")

    def _headers(self):
        return {"Authorization": "ApiKey " + self._key.get_secret_value()}

    def start(self, on_failure=None):
        self._on_failure = on_failure
        if self._worker is None and not self._closed:
            self._worker = asyncio.create_task(self._run(), name="elastic-audit-outbox")
            self.notify()

    def notify(self):
        self._wake.set()

    async def write_event(self, event):
        validated = _EVENT.validate_python(event)
        persisted = self.store.persist_audit_event(validated, require_offline=False)
        self.notify()
        return persisted

    def runtime_health(self):
        failure = self._failure or self._agent_failure
        return ServiceHealth(status="error", mode="live", detail=failure) if failure else None

    async def health_check(self):
        if not self._key.get_secret_value():
            return ServiceHealth(status="missing_key", mode="live", detail="ELASTIC_API_KEY is not configured.")
        if self.runtime_health() is not None:
            return self.runtime_health()
        try:
            self._configuration()
            async with httpx.AsyncClient(transport=self._transport, timeout=5, follow_redirects=False) as client:
                response = await client.get(self._base_url + "/_security/_authenticate", headers=self._headers())
            if not response.is_success:
                raise _DeliveryError(f"Elastic read-only health probe returned HTTP {response.status_code}.")
            if not isinstance(response.json(), dict) or not isinstance(response.json().get("username"), str):
                raise _DeliveryError("Elastic read-only health probe returned an unexpected response shape.")
        except _DeliveryError as error:
            return ServiceHealth(status="error", mode="live", detail=error.summary)
        except Exception:
            return ServiceHealth(status="error", mode="live", detail="Elastic read-only health probe failed.")
        return ServiceHealth(status="ok", mode="live", detail="Elasticsearch authentication succeeded; audit delivery is live.")

    async def _request(self, method, path, **kwargs):
        self._configuration()
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10, follow_redirects=False) as client:
                return await client.request(method, self._base_url + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise _DeliveryError("Elastic audit request failed (transport).", retryable=True) from None

    async def _json_request(self, method, path, **kwargs):
        for attempt in range(self.max_attempts):
            try:
                response = await self._request(method, path, **kwargs)
                if response.is_success:
                    return response
                raise _DeliveryError(f"Elastic audit request returned HTTP {response.status_code}.",
                                     retryable=_transient(response.status_code))
            except _DeliveryError as error:
                if not error.retryable or attempt + 1 == self.max_attempts:
                    raise
                await self._sleep(min(2 ** attempt * 0.1, 2))

    async def ensure_index(self):
        if self._index_ready:
            return
        for attempt in range(self.max_attempts):
            try:
                response = await self._request("PUT", "/" + INDEX_NAME, json={"mappings": EVENT_MAPPINGS})
                if response.is_success:
                    self._index_ready = True
                    return
                try:
                    exists = response.json().get("error", {}).get("type") == "resource_already_exists_exception"
                except (ValueError, TypeError, AttributeError):
                    exists = False
                if exists:
                    await self._json_request("PUT", "/" + INDEX_NAME + "/_mapping", json=EVENT_MAPPINGS)
                    self._index_ready = True
                    return
                raise _DeliveryError(f"Elastic index setup returned HTTP {response.status_code}.",
                                     retryable=_transient(response.status_code))
            except _DeliveryError as error:
                if not error.retryable or attempt + 1 == self.max_attempts:
                    raise
                await self._sleep(min(2 ** attempt * 0.1, 2))

    async def _failed(self, rows, message):
        self._failure = message
        await self._notify_failure({row["run_id"] for row in rows}, message)

    async def _notify_failure(self, run_ids, message):
        if self._on_failure is None:
            return
        for run_id in sorted(run_ids):
            if run_id in self._notified_runs or not self.store.audit_run_known(run_id):
                continue
            try:
                # The callback owns the atomic durable claim + event write.
                # Do not reserve a notification before awaiting the bus lock:
                # cancellation there must leave it eligible for a later retry.
                result = self._on_failure(run_id, message)
                if inspect.isawaitable(result):
                    result = await result
            except Exception:
                # A notification failure must not crash payment processing or
                # destroy the original durable outbox record.
                pass
            else:
                if result is not False:
                    self._notified_runs.add(run_id)

    async def _deliver(self, rows, *, refresh):
        retrying, failures = list(rows), []
        message = "Elastic audit bulk delivery failed; events remain queued."
        for attempt in range(self.max_attempts):
            actions = []
            for row in retrying:
                actions.extend((json.dumps({"index": {"_index": INDEX_NAME, "_id": row["event_id"]}}, separators=(",", ":")),
                                json.dumps(row["document"], separators=(",", ":"), allow_nan=False)))
            try:
                response = await self._request("POST", "/_bulk", content="\n".join(actions) + "\n",
                    params={"refresh": "wait_for"} if refresh else None,
                    headers={"Content-Type": "application/x-ndjson"})
                if not response.is_success:
                    raise _DeliveryError(f"Elastic audit bulk returned HTTP {response.status_code}; events remain queued.",
                                         retryable=_transient(response.status_code))
                body = response.json()
                items = body.get("items") if isinstance(body, dict) else None
                if not isinstance(items, list) or len(items) != len(retrying):
                    raise _DeliveryError("Elastic audit bulk returned an invalid result; events remain queued.", retryable=True)
                statuses = []
                for item in items:
                    operation = item.get("index") if isinstance(item, dict) else None
                    status = operation.get("status") if isinstance(operation, dict) else None
                    if type(status) is not int:
                        raise _DeliveryError("Elastic audit bulk returned an invalid item; events remain queued.", retryable=True)
                    statuses.append(status)
                acknowledgements, again = [], []
                for row, status in zip(retrying, statuses):
                    if 200 <= status < 300:
                        acknowledgements.append(row["event_id"])
                    elif _transient(status):
                        again.append(row)
                    else:
                        failures.append(row)
                if acknowledgements:
                    self.store.acknowledge_audit(acknowledgements)
                retrying = again
                if not retrying:
                    break
            except (ValueError, TypeError):
                message = "Elastic audit bulk returned invalid JSON; events remain queued."
            except _DeliveryError as error:
                message = error.summary
                if not error.retryable:
                    failures.extend(retrying)
                    retrying = []
                    break
            if attempt + 1 < self.max_attempts:
                await self._sleep(min(2 ** attempt * 0.1, 2))
        failures.extend(retrying)
        if failures:
            await self._failed(failures, message)
            return False
        return True

    async def flush(self, run_id=None, refresh=False):
        async with self._flush_lock:
            seen = set()
            try:
                await self.ensure_index()
                while True:
                    rows = self.store.pending_audit(limit=self.batch_size, run_id=run_id)
                    if not rows:
                        break
                    if any(row.get("mapping_error") for row in rows):
                        await self._failed(rows, "Elastic audit event mapping failed; events remain queued for review.")
                        return False
                    seen.update(row["run_id"] for row in rows)
                    if not await self._deliver(rows, refresh=refresh):
                        return False
                if refresh:
                    response = await self._json_request("POST", "/" + INDEX_NAME + "/_refresh")
                    try:
                        body = response.json()
                        shards = body.get("_shards", {})
                    except (ValueError, AttributeError):
                        raise _DeliveryError("Elastic audit refresh returned an invalid result.") from None
                    if not isinstance(shards, dict) or shards.get("failed", 0) != 0:
                        raise _DeliveryError("Elastic audit refresh failed; records may not be searchable.")
                # A successful scoped flush cannot hide another run's pending
                # failed records from the global service-health response.
                if self.store.audit_pending_count() == 0:
                    self._failure = None
                return True
            except asyncio.CancelledError:
                raise
            except _DeliveryError as error:
                rows = self.store.pending_audit(limit=self.batch_size, run_id=run_id)
                if not rows:
                    targets = seen | ({run_id} if run_id and self.store.audit_run_known(run_id) else set())
                    rows = [{"run_id": item} for item in targets]
                await self._failed(rows, error.summary)
                return False
            except Exception:
                rows = self.store.pending_audit(limit=self.batch_size, run_id=run_id)
                await self._failed(rows, "Elastic audit delivery failed; events remain queued.")
                return False

    async def _run(self):
        while not self._closed:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.flush_interval)
            except TimeoutError:
                pass
            self._wake.clear()
            if not await self.flush():
                await self._sleep(5)

    async def ask(self, request):
        from app.adapters.elastic_agent import AgentBuilderClient, AuditUnavailableError

        if not await self.flush(run_id=request.run_id, refresh=True):
            raise AuditUnavailableError("Elastic audit delivery is unavailable; the question cannot use incomplete records.")
        if self.agent_client is None:
            self.agent_client = AgentBuilderClient(key=self._key, kibana_url=self.kibana_url,
                agent_id=self.agent_id, transport=self._transport)
        try:
            answer = await self.agent_client.ask(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._agent_failure = "Elastic Agent Builder audit is unavailable."
            if request.run_id is not None:
                await self._notify_failure({request.run_id}, self._agent_failure)
            raise
        self._agent_failure = None
        return answer

    async def aclose(self):
        self._closed = True
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
