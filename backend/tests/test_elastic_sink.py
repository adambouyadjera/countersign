"""Structured immutable audit facts and real HTTP bulk protocol, fully mocked."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr, TypeAdapter
import pytest
import respx

from app.adapters.elastic import LiveAuditSink
from app.audit.events import EVENT_MAPPINGS, INDEX_NAME, map_event
from app.contract_models import AuditAskRequest, RunEvent
from app.store import Store


BASE = "https://elastic.example.test"


@pytest.fixture
def store():
    result = Store(":memory:")
    result.initialize()
    try:
        yield result
    finally:
        result.close()


def append(store, run_id="run-test", **updates):
    values = {"type": "error", "lane": "system", "mode": "live",
              "payload": {"message": "Synthetic event", "recoverable": True}}
    values.update(updates)
    return store.append_event(run_id, values)


def sink(store, **updates):
    result = LiveAuditSink(key=SecretStr("synthetic-elastic-key"), base_url=BASE, store=store,
                           kibana_url="https://kibana.example.test", **updates)
    result._sleep = AsyncMock()
    return result


def create_index(router):
    return router.put(BASE + "/" + INDEX_NAME).respond(200, json={"acknowledged": True})


def bulk_response(statuses):
    return httpx.Response(200, json={"errors": any(status >= 300 for status in statuses),
        "items": [{"index": {"status": status}} for status in statuses]})


def install_source(store):
    store.save_record("batches", "batch-one", {"batch_id": "batch-one", "invoices": [{
        "invoice_id": "invoice-one", "invoice_number": "INV-003", "vendor_id": "vendor-one",
        "vendor_name": "Golden Gate Sanitation", "amount_cents": 64000,
    }]})
    store.save_record("runs", "run-test", {"run_id": "run-test", "batch_id": "batch-one", "lanes": [],
        "instruction": "Trusted test instruction", "created_at": "2026-10-24T00:00:00Z"})
    store.save_record("invoice_scoring", "invoice-one", {"invoice_id": "invoice-one", "batch_id": "batch-one",
        "template": "hidden_white_text"})
    store.save_record("approvals", "approval-one", {"approval_id": "approval-one", "invoice_id": "invoice-one",
        "invoice_number": "INV-003", "vendor_name": "Golden Gate Sanitation", "amount_cents": 64000,
        "requested_account_display": "999900009999", "account_on_file_display": "300300003333"})


def graph(store):
    store.save_record("graphs", "run-test:invoice-one", {"graph_id": "run-test:invoice-one", "nodes": [
        {"id": "account-source", "kind": "span", "trust": "untrusted", "span": {"span_verified": True}},
        {"id": "account-field", "kind": "field", "label": "remit_account_number",
         "detail": {"value": "999900009999"}},
    ], "edges": [{"source": "account-source", "target": "account-field", "kind": "derived_from"}]})


def decision_payload(approval_id=None):
    return {"decision_id": "decision-one", "run_id": "run-test", "invoice_id": "invoice-one",
            "outcome": "HOLD", "reason_codes": ["BANK_DETAILS_MISMATCH"],
            "checks": [{"code": "PAYEE_ON_FILE", "passed": False, "message": "Requested account differs",
                        "evidence_node_ids": ["account-source"]}],
            **({"approval_id": approval_id} if approval_id else {})}


def test_required_mapping_is_explicit_and_payload_cannot_expand_dynamic_fields():
    properties = EVENT_MAPPINGS["properties"]
    for name in ("run_id", "lane", "type", "invoice_id", "vendor_id", "outcome", "reason_codes",
                 "transfer_id", "mode", "attack_placement"):
        assert properties[name]["type"] == "keyword"
    assert properties["seq"]["type"] == properties["amount_cents"]["type"] == "long"
    assert properties["payee_on_file"]["type"] == "boolean"
    assert properties["ts"]["type"] == "date"
    assert properties["payload"] == {"type": "object", "enabled": False}
    assert EVENT_MAPPINGS["dynamic"] == "strict"


def test_event_mapping_preserves_envelope_and_explicit_unknown_values(store):
    event = append(store)
    document = map_event(store, event)
    assert document["run_id"] == event.run_id and document["seq"] == 1
    assert document["type"] == "error" and document["lane"] == "system" and document["mode"] == "live"
    assert document["payload"] == event.model_dump(mode="json", exclude_none=True)
    assert document["invoice_id"] is None and document["payee_on_file"] is None
    assert document["amount_cents"] is None and document["reason_codes"] == []
    assert set(document) == set(EVENT_MAPPINGS["properties"])


def test_hold_mapping_uses_saved_approval_not_changed_vendor_master(store):
    install_source(store)
    store.save_record("vendors", "vendor-one", {"vendor_id": "vendor-one", "account_on_file_display": "999900009999"})
    event = append(store, type="payment.held", lane="countersign", payload={"invoice_id": "invoice-one",
        "approval_id": "approval-one", "reason_codes": ["BANK_DETAILS_MISMATCH"]})
    document = map_event(store, event)
    assert document["outcome"] == "HOLD" and document["reason_codes"] == ["BANK_DETAILS_MISMATCH"]
    assert document["amount_cents"] == 64000 and document["payee_on_file"] is False
    assert document["vendor_id"] == "vendor-one" and document["invoice_number"] == "INV-003"
    assert document["requested_account"] == "999900009999" and document["account_on_file"] == "300300003333"
    assert document["attack_placement"] == "hidden_white_text"


def test_document_provenance_requires_a_real_check_to_untrusted_span_not_reason_alone(store):
    install_source(store)
    graph(store)
    payload = decision_payload("approval-one")
    event = append(store, type="policy.decision", lane="countersign", payload={"decision": payload})
    document = map_event(store, event)
    assert document["payee_document_derived"] is True and document["payee_on_file"] is False
    payload["checks"][0]["evidence_node_ids"] = []
    event = append(store, type="policy.decision", lane="countersign", payload={"decision": payload})
    assert map_event(store, event)["payee_document_derived"] is False


def test_original_policy_outcome_is_not_overwritten_by_later_decision_state(store):
    install_source(store)
    payload = decision_payload()
    payload.update(outcome="ALLOW", reason_codes=[])
    store.save_record("decisions", payload["decision_id"], {**payload, "outcome": "BLOCK", "reason_codes": ["DUPLICATE_INVOICE"]})
    event = append(store, type="policy.decision", lane="countersign", payload={"decision": payload})
    document = map_event(store, event)
    assert document["outcome"] == "ALLOW" and document["reason_codes"] == []


def test_missing_provenance_graph_stays_unknown_not_falsely_document_free(store):
    install_source(store)
    event = append(store, type="policy.decision", lane="countersign", payload={"decision": decision_payload()})
    assert map_event(store, event)["payee_document_derived"] is None


@pytest.mark.parametrize("lane,on_file", [("countersign",True),("unguarded",False)])
def test_payment_mapping_retains_actual_destination_bank_result_and_mode(store,lane,on_file):
    install_source(store)
    payment = {"transfer_id":"synthetic-test-transfer", "transfer_status":"pending",
        "from_account_id":"synthetic-company", "to_account_id":"synthetic-payee",
        "to_display_number":"300300003333" if on_file else "999900009999",
        "payee_label":"Synthetic payee", "payee_on_file":on_file,"amount_cents":64000,"bank_mode":"offline"}
    event = append(store,type="payment.executed",lane=lane,mode="offline",payload={
        "invoice_id":"invoice-one","via":"policy" if on_file else "agent","payment":payment})
    document = map_event(store,event)
    assert document["outcome"]=="PAID" and document["transfer_id"]==payment["transfer_id"]
    assert document["payee_on_file"] is on_file and document["requested_account"]==payment["to_display_number"]
    assert document["mode"]=="offline" and document["amount_cents"]==64000


def test_ledger_mapping_binds_invoice_context_and_never_claims_a_bill_is_paid(store):
    from app.books.ledger import Ledger
    install_source(store)
    ledger=Ledger(store)
    ledger.initialize()
    entry=ledger.post_bill({"invoice_id":"invoice-one","invoice_number":"INV-003", "vendor_id":"vendor-one",
        "vendor_name":"Golden Gate Sanitation","amount_cents":64000,"currency":"USD"},run_id="run-test")
    event=append(store,type="ledger.posted",lane="countersign",mode="offline",payload={"entry":entry.model_dump(mode="json")})
    document=map_event(store,event)
    assert document["invoice_id"]=="invoice-one" and document["vendor_id"]=="vendor-one"
    assert document["amount_cents"]==64000 and document["transfer_id"] is None and document["outcome"] is None


def test_mapper_metadata_failure_cannot_erase_original_event(store, monkeypatch):
    event = append(store)
    monkeypatch.setattr(store, "get_record", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic-private-error")))
    document = map_event(store, event)
    assert document["payload"] == event.model_dump(mode="json", exclude_none=True)
    assert "synthetic-private-error" not in json.dumps(document)


@pytest.mark.asyncio
async def test_bulk_protocol_acks_actual_result_and_flushes_visibility(store):
    event = append(store)
    adapter = sink(store)
    with respx.mock as router:
        index = create_index(router)
        captured = []
        def receive(request):
            assert request.headers["Authorization"] == "ApiKey synthetic-elastic-key"
            assert request.headers["Content-Type"] == "application/x-ndjson"
            assert request.content.endswith(b"\n")
            lines = [json.loads(line) for line in request.content.splitlines()]
            captured.extend(lines)
            assert lines[0] == {"index": {"_index": INDEX_NAME, "_id": "run-test:1"}}
            assert request.url.params["refresh"] == "wait_for"
            return bulk_response([201])
        bulk = router.post(BASE + "/_bulk").mock(side_effect=receive)
        refresh = router.post(BASE + "/" + INDEX_NAME + "/_refresh").respond(200, json={"_shards": {"failed": 0}})
        assert await adapter.flush(refresh=True)
        assert index.call_count == bulk.call_count == refresh.call_count == 1
    assert captured[1]["payload"] == event.model_dump(mode="json", exclude_none=True)
    assert store.audit_pending_count() == 0
    assert await adapter.write_event(event) == event  # Never relabel or reinsert.
    assert store.list_events("run-test") == [event]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_mode", ["live", "offline"])
async def test_write_event_persists_unstored_source_and_retries_without_relabelling_or_duplicate_delivery(store, source_mode):
    event = TypeAdapter(RunEvent).validate_python({"run_id": "run-source-protocol", "seq": 1,
        "ts": datetime.now(timezone.utc), "type": "error", "lane": "system", "mode": source_mode,
        "payload": {"message": "Synthetic original event", "recoverable": True}})
    adapter = sink(store)
    assert store.list_events(event.run_id) == []
    assert await adapter.write_event(event) == event
    assert await adapter.write_event(event.model_dump(mode="json")) == event
    assert store.list_events(event.run_id) == [event]
    queued, = store.pending_audit()
    assert queued["event_id"] == f"{event.run_id}:1"
    assert queued["document"]["payload"] == event.model_dump(mode="json", exclude_none=True)
    assert queued["document"]["mode"] == source_mode
    with respx.mock as router:
        create_index(router)
        bulk = router.post(BASE + "/_bulk").respond(200, json={"errors": False,
            "items": [{"index": {"status": 201}}]})
        assert await adapter.flush()
        assert await adapter.write_event(event) == event
        assert await adapter.flush()
        assert bulk.call_count == 1
    assert store.audit_pending_count() == 0
    assert store.list_events(event.run_id) == [event]


@pytest.mark.asyncio
async def test_write_event_repairs_missing_legacy_outbox_without_changing_source_event(store):
    event = append(store)
    store._db().execute("DELETE FROM audit_outbox WHERE event_id=?", (f"{event.run_id}:{event.seq}",))
    assert store.audit_pending_count() == 0
    assert await sink(store).write_event(event) == event
    queued, = store.pending_audit()
    assert queued["event_id"] == f"{event.run_id}:{event.seq}"
    assert queued["document"]["payload"] == event.model_dump(mode="json", exclude_none=True)
    assert store.list_events(event.run_id) == [event]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_bulk_transient_http_status_retries_same_ids_then_recovers(store, status):
    append(store)
    adapter = sink(store)
    with respx.mock as router:
        create_index(router)
        route = router.post(BASE + "/_bulk").mock(side_effect=[httpx.Response(status, json={"private": "not logged"}),bulk_response([200])])
        assert await adapter.flush()
        assert route.call_count == 2
        assert route.calls[0].request.content == route.calls[1].request.content
    assert store.audit_pending_count() == 0 and adapter.runtime_health() is None


@pytest.mark.asyncio
async def test_bulk_transport_failure_retries_without_making_up_success(store):
    append(store)
    adapter = sink(store)
    with respx.mock as router:
        create_index(router)
        route = router.post(BASE + "/_bulk").mock(side_effect=[httpx.ReadTimeout("synthetic-private-key"),bulk_response([201])])
        assert await adapter.flush()
        assert route.call_count == 2
    assert store.audit_pending_count() == 0


@pytest.mark.asyncio
async def test_partial_bulk_retry_sends_only_failed_items_and_acks_successes(store):
    append(store)
    append(store)
    adapter = sink(store)
    with respx.mock as router:
        create_index(router)
        route = router.post(BASE + "/_bulk").mock(side_effect=[bulk_response([201,429]),bulk_response([201])])
        assert await adapter.flush()
        retry = [json.loads(line) for line in route.calls[1].request.content.splitlines()]
        assert retry[0]["index"]["_id"] == "run-test:2" and len(retry) == 2
    assert store.audit_pending_count() == 0


@pytest.mark.asyncio
async def test_permanent_item_failure_is_durable_warns_once_and_remains_unhealthy(store):
    append(store)
    adapter = sink(store)
    warnings = []
    adapter._on_failure = lambda run_id,message: warnings.append((run_id,message))
    with respx.mock as router:
        create_index(router)
        route = router.post(BASE + "/_bulk").mock(side_effect=lambda request: bulk_response([400]))
        assert await adapter.flush() is False
        assert await adapter.flush() is False
        assert route.call_count == 2  # No per-attempt retry for mapping errors.
    assert store.audit_pending_count() == 1 and len(warnings) == 1
    assert (await adapter.health_check()).status == "error"
    assert adapter.runtime_health().mode == "live"


@pytest.mark.asyncio
async def test_exhausted_retries_keep_outbox_and_callback_once_per_affected_run(store):
    append(store,"run-one")
    append(store,"run-two")
    adapter = sink(store)
    warnings = []
    async def warn(run_id,message):
        warnings.append(run_id)
    adapter._on_failure = warn
    with respx.mock as router:
        create_index(router)
        route = router.post(BASE + "/_bulk").respond(503, text="synthetic-private-secret")
        assert await adapter.flush() is False
        assert route.call_count == 3
        assert set(warnings) == {"run-one","run-two"}
        assert await adapter.flush() is False
    assert len(warnings)==2 and store.audit_pending_count()==2
    assert "synthetic-private-secret" not in adapter.runtime_health().model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"items":[]},{"items":[{"index":{}}]},"not-json"])
async def test_malformed_bulk_success_cannot_acknowledge_records(store, body):
    append(store)
    adapter=sink(store)
    with respx.mock as router:
        create_index(router)
        route=router.post(BASE+"/_bulk")
        if isinstance(body,str):
            route.respond(200,text=body)
        else:
            route.respond(200,json=body)
        assert await adapter.flush() is False
        assert route.call_count==3
    assert store.audit_pending_count()==1


@pytest.mark.asyncio
async def test_existing_index_gets_explicit_mapping_without_recreation_or_deletion(store):
    adapter=sink(store)
    with respx.mock as router:
        router.put(BASE+"/"+INDEX_NAME).respond(400,json={"error":{"type":"resource_already_exists_exception"}})
        mapping=router.put(BASE+"/"+INDEX_NAME+"/_mapping").respond(200,json={"acknowledged":True})
        await adapter.ensure_index()
        assert json.loads(mapping.calls[0].request.content)==EVENT_MAPPINGS


@pytest.mark.asyncio
async def test_missing_key_never_networks_and_failure_does_not_switch_modes(store):
    append(store)
    adapter=LiveAuditSink(key=SecretStr(""),base_url=BASE,store=store)
    assert (await adapter.health_check()).status=="missing_key"
    with respx.mock:
        assert await adapter.flush() is False
    assert adapter.mode=="live" and store.audit_pending_count()==1


@pytest.mark.asyncio
async def test_successful_recovery_clears_bulk_health_failure(store):
    append(store)
    adapter=sink(store)
    with respx.mock as router:
        create_index(router)
        route=router.post(BASE+"/_bulk").respond(403,json={"private":"not reflected"})
        assert await adapter.flush() is False
        assert adapter.runtime_health().status=="error"
        route.mock(return_value=bulk_response([201]))
        assert await adapter.flush()
        assert adapter.runtime_health() is None


@pytest.mark.asyncio
async def test_refresh_barrier_runs_even_when_background_worker_already_delivered(store):
    adapter=sink(store)
    with respx.mock as router:
        create_index(router)
        refresh=router.post(BASE+"/"+INDEX_NAME+"/_refresh").respond(200,json={})
        assert await adapter.flush(run_id="run-test",refresh=True)
        assert refresh.call_count==1


@pytest.mark.asyncio
async def test_mapping_error_rows_are_never_indexed_or_acknowledged(store,monkeypatch):
    adapter=sink(store)
    monkeypatch.setattr(store,"pending_audit",lambda **kwargs:[{"event_id":"run-test:1","run_id":"run-test",
        "seq":1,"document":{},"mapping_error":"SyntheticMappingError"}])
    with respx.mock as router:
        create_index(router)
        assert await adapter.flush() is False
    assert "mapping failed" in adapter.runtime_health().detail


@pytest.mark.asyncio
async def test_agent_failure_stays_in_health_after_successful_background_delivery(store):
    from app.adapters.elastic_agent import AuditUnavailableError
    adapter=sink(store)
    adapter.agent_client=type("Agent",(),{"ask":AsyncMock(side_effect=AuditUnavailableError("fixed-safe-failure"))})()
    with respx.mock as router:
        create_index(router)
        router.post(BASE+"/"+INDEX_NAME+"/_refresh").respond(200,json={})
        with pytest.raises(AuditUnavailableError):
            await adapter.ask(AuditAskRequest(question="A question"))
        assert await adapter.flush()
        assert adapter.runtime_health().status=="error"
    assert adapter.agent_client.ask.await_count==1


@pytest.mark.asyncio
async def test_scoped_success_cannot_hide_another_runs_failed_delivery(store):
    append(store, "run-bad")
    append(store, "run-good")
    adapter = sink(store)
    with respx.mock as router:
        create_index(router)
        route = router.post(BASE + "/_bulk").respond(403, json={})
        assert await adapter.flush(run_id="run-bad") is False
        route.mock(return_value=bulk_response([201]))
        assert await adapter.flush(run_id="run-good")
    assert store.audit_pending_count() == 1
    assert adapter.runtime_health().status == "error"


@pytest.mark.asyncio
async def test_refresh_shard_failure_cannot_claim_read_after_write_visibility(store):
    adapter = sink(store)
    with respx.mock as router:
        create_index(router)
        router.post(BASE + "/" + INDEX_NAME + "/_refresh").respond(200, json={"_shards": {"failed": 1}})
        assert await adapter.flush(run_id="run-test", refresh=True) is False
    assert "not be searchable" in adapter.runtime_health().detail
