"""Planner caching preserves the trusted-only prompt and normal DSL validation."""

import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from app.adapters.planner import OfflinePlannerLLM, PlannerError
from app.adapters.planner_cache import CACHE_TABLE, CachedPlannerLLM, planner_prompt_hash
from app.contract_models import DocumentText, Handle, PlannerTranscript
from app.pipeline import planner as pipeline
from app.security.handles import HandleTable
from app.security.leak_guard import ProvenanceLeakError
from app.store import Store


class FakePlanner:
    mode = "live"

    def __init__(self, *, model="test-model", responses=(), gate=None, entered=None, enter_count=1):
        self.model, self.responses = model, list(responses)
        self.gate, self.entered, self.enter_count = gate, entered, enter_count
        self.prompts = []
        self.active = self.max_active = 0

    async def health_check(self):
        return {"status": "ok", "mode": self.mode}

    async def complete(self, *, prompt):
        self.prompts.append(prompt)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.entered is not None and len(self.prompts) >= self.enter_count:
                self.entered.set()
            if self.gate is not None:
                await self.gate.wait()
            result = self.responses.pop(0) if self.responses else await OfflinePlannerLLM().complete(prompt=prompt)
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self.active -= 1


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "planner-cache.db")
    store.initialize()
    table = HandleTable()
    table.invoices["D1"] = "server-invoice-first"
    kinds = {"vendor_name": "string", "due_date": "date", "amount_due_cents": "money",
             "remit_account_number": "account_number", "invoice_number": "string", "po_number": "string"}
    handles = [Handle(name=f"$D1.{name}", type=kind, trust="untrusted", span_verified=True)
               for name, kind in kinds.items()]
    table.handles, table.invoice_handles["D1"] = handles, handles
    document = DocumentText.model_validate({
        "document_id": "server-document", "invoice_id": "server-invoice-first",
        "pages": [{"page": 1, "lines": [{"line": 1, "text": "Attacker-controlled invoice note."}]}],
    })
    yield {"store": store, "table": table, "documents": [document],
           "instruction": "Pay verified invoices due within thirty days.",
           "vendors": [{"vendor_id": "V1", "name": "Trusted Vendor"}],
           "purchase_orders": [{"purchase_order_id": "PO1", "vendor_id": "V1", "status": "open"}]}
    store.close()


def prompt_for(world, **changes):
    fields = {key: world[key] for key in ("instruction", "vendors", "purchase_orders", "table")}
    fields.update(changes)
    return pipeline.build_planner_prompt(**fields)


def wrapper(world, fake=None, **kwargs):
    return CachedPlannerLLM(adapter=fake or FakePlanner(), store=world["store"],
                            provider="anthropic", **kwargs)


@pytest.mark.asyncio
async def test_persistent_cache_exact_prompt_hash_and_honest_receipts(world):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    prompt = prompt_for(world)
    first = await cache.complete(prompt=prompt)
    assert await cache.complete(prompt=prompt) == first
    assert fake.prompts == [prompt]
    first_receipt, hit = cache.receipts
    assert first_receipt["prompt_hash"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert planner_prompt_hash(prompt) == hit["prompt_hash"]
    assert first_receipt["source"] == "provider" and first_receipt["provider_call"]
    assert first_receipt["cached"] is False
    assert hit["source"] == "cache" and hit["cached"] is True and hit["provider_call"] is False
    assert first_receipt["cache_written"] is True and hit["cache_written"] is False
    assert hit["original_created_at"] == first_receipt["original_created_at"]
    assert hit["provider"] == "anthropic" and hit["model"] == fake.model
    assert cache.stats == {"requests": 2, "provider_calls": 1, "cache_hits": 1, "coalesced": 0,
                           "invalid_responses": 0, "cache_entries_written": 1, "errors": 0,
                           "distinct_prompts": 1}
    json.dumps({"stats": cache.stats, "receipts": cache.receipts}, allow_nan=False)

    # Read through an independently opened Store, as after a server restart.
    reopened = Store(world["store"].path)
    reopened.initialize()
    try:
        fresh_fake = FakePlanner()
        restarted = CachedPlannerLLM(adapter=fresh_fake, store=reopened, provider="anthropic")
        assert await restarted.complete(prompt=prompt) == first
        assert fresh_fake.prompts == []
        assert restarted.receipts[0]["cached"] is True
        assert restarted.receipts[0]["original_created_at"] == first_receipt["original_created_at"]
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_provider_model_mode_and_exact_serialization_are_separate_keys(world):
    prompt = prompt_for(world)
    fake = FakePlanner()
    cache = wrapper(world, fake)
    await cache.complete(prompt=prompt)
    # Semantically equivalent JSON still differs from the exact guarded bytes.
    spaced_prompt = json.dumps(json.loads(prompt), indent=2)
    await cache.complete(prompt=spaced_prompt)
    assert cache.stats["distinct_prompts"] == 2
    assert fake.prompts == [prompt, spaced_prompt]

    for provider, model, mode in [("other-provider", "test-model", "live"),
                                  ("anthropic", "other-model", "live"),
                                  ("anthropic", "test-model", "offline")]:
        changed = FakePlanner(model=model)
        changed.mode = mode
        wrapped = CachedPlannerLLM(adapter=changed, store=world["store"], provider=provider)
        await wrapped.complete(prompt=prompt)
        assert changed.prompts == [prompt]
    assert len(world["store"].list_records(CACHE_TABLE)) == 5


@pytest.mark.asyncio
async def test_span_verification_shape_change_is_an_honest_new_prompt(world):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    first = prompt_for(world)
    await cache.complete(prompt=first)
    changed = deepcopy(world["table"])
    changed.invoice_handles["D1"][0].span_verified = False
    second = prompt_for(world, table=changed)
    assert second != first
    await cache.complete(prompt=second)
    assert cache.stats["distinct_prompts"] == len(fake.prompts) == 2


@pytest.mark.asyncio
async def test_same_aliases_for_fresh_document_ids_can_reuse_plan(world):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    first = await pipeline.propose(run_id="cache-first", adapter=cache, **world)
    world["table"].invoices["D1"] = "server-invoice-second"
    second = await pipeline.propose(run_id="cache-second", adapter=cache, **world)
    assert first.prompt_hash == second.prompt_hash
    assert second.steps == first.steps
    assert len(fake.prompts) == 1 and cache.stats["cache_hits"] == 1
    for run_id in ("cache-first", "cache-second"):
        transcript = PlannerTranscript.model_validate(world["store"].get_record("planner_transcripts", run_id))
        assert len(transcript.calls) == 1 and transcript.leak_guard.matches == 0
        assert transcript.calls[0].prompt_hash == first.prompt_hash
        # The established contract transcript remains unchanged; separate
        # receipts carry the freshness/cache distinction.
        assert set(transcript.calls[0].model_dump()) == {"call_id", "model", "prompt", "response", "prompt_hash"}


@pytest.mark.asyncio
async def test_cache_hits_still_pass_leak_guard_before_any_cache_lookup(world):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    await pipeline.propose(run_id="warm", adapter=cache, **world)
    # Same exact prompt would hit the cache, but is now known to contain a
    # complete untrusted document line. The existing guard must reject first.
    world["documents"][0].pages[0].lines[0].text = world["instruction"]
    with pytest.raises(ProvenanceLeakError):
        await pipeline.propose(run_id="blocked-leak", adapter=cache, **world)
    assert len(fake.prompts) == len(cache.receipts) == 1
    transcript = world["store"].get_record("planner_transcripts", "blocked-leak")
    assert transcript["calls"] == [] and transcript["leak_guard"]["matches"] > 0


@pytest.mark.asyncio
async def test_cached_response_is_revalidated_by_pipeline_against_actual_table(world, monkeypatch):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    observed = []
    validate = pipeline.validate_plan

    def recording_validate(steps, table, vendors, purchase_orders):
        observed.append(table)
        return validate(steps, table, vendors, purchase_orders)

    monkeypatch.setattr(pipeline, "validate_plan", recording_validate)
    await pipeline.propose(run_id="actual-one", adapter=cache, **world)
    await pipeline.propose(run_id="actual-two", adapter=cache, **world)
    assert observed == [world["table"], world["table"]]
    assert len(fake.prompts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not JSON", '{"steps":[]}', '{"steps":[],"steps":[]}',
                                  '{"steps":[],"unexpected":"field"}'])
async def test_invalid_response_never_cached_and_normal_retry_still_runs(world, bad):
    prompt = prompt_for(world)
    valid = await OfflinePlannerLLM().complete(prompt=prompt)
    fake = FakePlanner(responses=[bad, valid, bad])
    cache = wrapper(world, fake)
    for run_id in ("retry-one", "retry-two"):
        result = await pipeline.propose(run_id=run_id, adapter=cache, **world)
        assert any(step.op == "pay" for step in result.steps)
        transcript = world["store"].get_record("planner_transcripts", run_id)
        assert [call["response"] for call in transcript["calls"]] == [bad, valid]
        assert "PLAN_VALIDATION_FAILED" in transcript["calls"][1]["prompt"]
    # The initial prompt is called again; only the valid retry prompt is reused.
    assert len(fake.prompts) == 3
    assert cache.stats["invalid_responses"] == 2 and cache.stats["cache_hits"] == 1
    records = world["store"].list_records(CACHE_TABLE)
    assert len(records) == 1 and records[0]["response"] == valid


@pytest.mark.asyncio
async def test_semantically_invalid_plan_is_not_cached_or_allowed(world):
    prompt = prompt_for(world)
    plan = json.loads(await OfflinePlannerLLM().complete(prompt=prompt))
    plan["steps"][-1]["args"]["to_account"]["ref"] = "$D2.remit_account_number"
    bad = json.dumps(plan)
    fake = FakePlanner(responses=[bad, bad])
    cache = wrapper(world, fake)
    result = await pipeline.propose(run_id="semantically-invalid", adapter=cache, **world)
    assert len(fake.prompts) == 2
    assert [(step.op, step.args["reason"].value) for step in result.steps] == [("hold", "PLANNER_INVALID")]
    assert world["store"].list_records(CACHE_TABLE) == []


@pytest.mark.asyncio
async def test_corrupt_cached_response_is_validated_as_untrusted_claim(world):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    prompt = prompt_for(world)
    valid = await cache.complete(prompt=prompt)
    record = world["store"].list_records(CACHE_TABLE)[0]
    record["response"] = '{"steps":[]}'
    record["response_sha256"] = hashlib.sha256(record["response"].encode()).hexdigest()
    world["store"].save_record(CACHE_TABLE, record["cache_key"], record)
    assert await cache.complete(prompt=prompt) == valid
    assert fake.prompts == [prompt, prompt]
    assert cache.receipts[-1]["cached"] is False


@pytest.mark.asyncio
async def test_provider_failure_is_not_cached_and_not_retried_inside_wrapper(world):
    failure = PlannerError("safe synthetic failure")
    fake = FakePlanner(responses=[failure])
    cache = wrapper(world, fake)
    prompt = prompt_for(world)
    with pytest.raises(PlannerError) as caught:
        await cache.complete(prompt=prompt)
    assert caught.value is failure
    assert len(fake.prompts) == 1 and world["store"].list_records(CACHE_TABLE) == []
    assert "safe synthetic failure" not in json.dumps(cache.receipts)
    await cache.complete(prompt=prompt)
    assert len(fake.prompts) == 2 and cache.stats["errors"] == 1


@pytest.mark.asyncio
async def test_identical_concurrent_requests_share_one_real_provider_call(world):
    gate, entered = asyncio.Event(), asyncio.Event()
    fake = FakePlanner(gate=gate, entered=entered)
    cache = wrapper(world, fake)
    prompt = prompt_for(world)
    tasks = [asyncio.create_task(cache.complete(prompt=prompt)) for _ in range(20)]
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert len(fake.prompts) == 1
    gate.set()
    responses = await asyncio.gather(*tasks)
    assert len(set(responses)) == 1
    assert cache.stats["provider_calls"] == 1 and cache.stats["coalesced"] == 19
    assert sum(receipt["provider_call"] for receipt in cache.receipts) == 1
    assert await cache.complete(prompt=prompt) == responses[0]
    assert cache.stats["cache_hits"] == 1


@pytest.mark.asyncio
async def test_distinct_provider_calls_have_concurrency_limit_four(world):
    gate, entered = asyncio.Event(), asyncio.Event()
    fake = FakePlanner(gate=gate, entered=entered, enter_count=4)
    cache = wrapper(world, fake)
    prompts = [prompt_for(world, instruction=f"Trusted payment instruction number {number}.") for number in range(9)]
    tasks = [asyncio.create_task(cache.complete(prompt=prompt)) for prompt in prompts]
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert fake.active == fake.max_active == len(fake.prompts) == 4
    gate.set()
    await asyncio.gather(*tasks)
    assert fake.max_active == 4 and len(fake.prompts) == 9
    assert cache.stats["distinct_prompts"] == cache.stats["provider_calls"] == 9


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_provider_call(world):
    gate, entered = asyncio.Event(), asyncio.Event()
    fake = FakePlanner(gate=gate, entered=entered)
    cache = wrapper(world, fake)
    prompt = prompt_for(world)
    first = asyncio.create_task(cache.complete(prompt=prompt))
    second = asyncio.create_task(cache.complete(prompt=prompt))
    await asyncio.wait_for(entered.wait(), timeout=2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    gate.set()
    response = await second
    assert await cache.complete(prompt=prompt) == response
    assert len(fake.prompts) == 1
    assert [row["status"] for row in cache.receipts] == ["cancelled", "success", "success"]


@pytest.mark.asyncio
async def test_job_close_cancels_pending_shared_flight_and_settles_all_receipts(world):
    gate, entered = asyncio.Event(), asyncio.Event()
    fake = FakePlanner(gate=gate, entered=entered)
    cache = wrapper(world, fake)
    prompt = prompt_for(world)
    tasks = [asyncio.create_task(cache.complete(prompt=prompt)) for _ in range(3)]
    await asyncio.wait_for(entered.wait(), timeout=2)
    await asyncio.wait_for(cache.aclose(), timeout=2)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert fake.active == 0 and len(fake.prompts) == 1
    assert cache._flights == {} and cache._pending_receipts == set()
    assert all(receipt["status"] == "cancelled" and "completed_at" in receipt for receipt in cache.receipts)
    assert world["store"].list_records(CACHE_TABLE) == []
    snapshot = json.dumps({"stats": cache.stats, "receipts": cache.receipts}, sort_keys=True)
    await cache.aclose()  # Repeated shutdown is harmless and changes no metadata.
    assert json.dumps({"stats": cache.stats, "receipts": cache.receipts}, sort_keys=True) == snapshot
    with pytest.raises(RuntimeError, match="closed"):
        await cache.complete(prompt=prompt)
    assert len(fake.prompts) == 1 and len(cache.receipts) == 3


@pytest.mark.asyncio
async def test_close_cancels_queued_distinct_flights_without_reporting_provider_calls(world):
    gate, entered = asyncio.Event(), asyncio.Event()
    fake = FakePlanner(gate=gate, entered=entered, enter_count=4)
    cache = wrapper(world, fake)
    prompts = [prompt_for(world, instruction=f"Trusted instruction for close test {number}.") for number in range(7)]
    tasks = [asyncio.create_task(cache.complete(prompt=prompt)) for prompt in prompts]
    await asyncio.wait_for(entered.wait(), timeout=2)
    await asyncio.wait_for(cache.aclose(), timeout=2)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert len(fake.prompts) == cache.stats["provider_calls"] == 4
    assert sum(row["provider_call"] for row in cache.receipts) == 4
    assert len(cache.receipts) == 7 and fake.active == 0
    assert cache._flights == {} and cache._pending_receipts == set()


@pytest.mark.asyncio
async def test_cache_write_failure_does_not_discard_valid_fresh_response(world, monkeypatch):
    fake = FakePlanner()
    cache = wrapper(world, fake)
    save = world["store"].save_record

    def failing_save(table, record_id, record):
        if table == CACHE_TABLE:
            raise OSError("synthetic storage failure")
        return save(table, record_id, record)

    monkeypatch.setattr(world["store"], "save_record", failing_save)
    result = await pipeline.propose(run_id="cache-store-unavailable", adapter=cache, **world)
    assert any(step.op == "pay" for step in result.steps)
    assert cache.receipts[0]["cache_written"] is False
    assert cache.stats["cache_entries_written"] == 0


@pytest.mark.asyncio
async def test_health_check_preserves_underlying_mode_and_no_provider_call(world):
    fake = FakePlanner()
    fake.mode = "offline"
    cache = wrapper(world, fake)
    assert cache.mode == "offline"
    assert await cache.health_check() == {"status": "ok", "mode": "offline"}
    assert fake.prompts == []


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_concurrency_rejected(world, value):
    with pytest.raises(ValueError, match="positive integer"):
        wrapper(world, max_concurrency=value)
