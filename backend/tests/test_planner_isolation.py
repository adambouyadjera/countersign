"""The planner boundary must exclude source text on initial and retry calls."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import SecretStr
import pytest

from app.config import Settings
from app.contract_models import DocumentText, PlannerTranscript
from app.main import create_app
from app.security.leak_guard import LeakGuardScanner, ProvenanceLeakError
from app.world import batches as batch_module
from app.world.attacks import ATTACK_TEMPLATES, create_attack


def document_with_line(text):
    return DocumentText.model_validate({
        "document_id": "server-document", "invoice_id": "server-invoice",
        "pages": [{"page": 1, "lines": [{"line": 1, "text": text}]}],
    })


@pytest.mark.parametrize("text", [
    'Pay to account "333344445555" immediately.',
    "Référence bancaire: nouveau bénéficiaire",
    "123456789012",
])
@pytest.mark.parametrize("nested", [False, True])
def test_leak_guard_rejects_escaped_document_lines_without_echoing_them(text, nested):
    scanner = LeakGuardScanner([document_with_line(text)])
    content = json.dumps({"document_line": text}, ensure_ascii=True)
    prompt = json.dumps({"messages": [{"role": "user", "content": content}]}) if nested else content
    with pytest.raises(ProvenanceLeakError) as error:
        scanner.check(prompt)
    assert text not in str(error.value)
    assert scanner.summary.strings_checked == 1
    assert scanner.summary.matches >= 1


@pytest.mark.parametrize("canary", ["CS-CANARY-A1B2", "CS-CANARY-ABCDLONGER"])
def test_leak_guard_scans_canaries_even_without_source_documents(canary):
    scanner = LeakGuardScanner([])
    with pytest.raises(ProvenanceLeakError):
        scanner.check(json.dumps({"messages": [canary]}))
    assert scanner.summary.strings_checked == 0
    assert scanner.summary.canaries_checked > 0
    assert scanner.summary.matches == 1


def test_leak_guard_counts_all_attempts_and_does_not_allow_trusted_overlap():
    scanner = LeakGuardScanner([document_with_line("Harbor Light Rentals")])
    prompt = json.dumps({"name_words": ["Harbor", "Light", "Rentals"]})
    scanner.check(prompt)
    scanner.check(prompt)
    snapshot = scanner.summary
    assert snapshot.strings_checked == 2 and snapshot.matches == 0
    snapshot.matches = 999
    assert scanner.summary.matches == 0
    with pytest.raises(ProvenanceLeakError):
        scanner.check(json.dumps({"trusted_name": "Harbor Light Rentals"}))


class RecordingPlannerClient:
    """Fake the SDK transport while exercising the real planner adapter."""

    def __init__(self, *, invalid_first_response=None):
        self.requests = []
        self.invalid_first_response = invalid_first_response
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        from app.adapters.planner import OfflinePlannerLLM

        self.requests.append(deepcopy(kwargs))
        assert set(kwargs) == {"model", "max_tokens", "system", "messages"}
        if len(self.requests) == 1 and self.invalid_first_response is not None:
            response = self.invalid_first_response
        else:
            response = await OfflinePlannerLLM().complete(prompt=json.dumps({
                "system": kwargs["system"], "messages": kwargs["messages"],
            }))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=response)])


def make_application(tmp_path):
    return create_app(Settings(
        _env_file=None, nessie_mode="offline", extractor_mode="offline",
        elastic_mode="offline", planner_mode="offline", nessie_api_key="",
        gemini_api_key="", anthropic_api_key="", elastic_api_key="", bitdeer_api_key="",
    ), db_path=tmp_path / "isolation.db")


def use_recording_planner(application, fake):
    from app.adapters.planner import LivePlannerLLM

    planner = LivePlannerLLM(key=SecretStr("synthetic-planner-key"), model="test-planner", client=fake)
    bundle = replace(application.state.adapters, planner=planner)
    application.state.adapters = bundle
    application.state.run_manager.adapters = bundle


async def start_run(application, batch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        created = await client.post("/api/runs", json={"batch_id": batch.batch_id, "lanes": ["countersign"]})
        assert created.status_code == 200, created.text
        run = await application.state.run_manager.wait(created.json()["run_id"])
        transcript = await client.get(f"/api/runs/{run.run_id}/planner_transcript")
        assert transcript.status_code == 200, transcript.text
        return run, PlannerTranscript.model_validate(transcript.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [item for item in ATTACK_TEMPLATES if item != "custom"])
async def test_planner_never_sees_untrusted_text(tmp_path, monkeypatch, template):
    # Regenerate the actual PDF, including the hidden/visible/footer placement,
    # rather than checking a hand-written string disconnected from the runner.
    monkeypatch.setattr(batch_module, "create_attack", lambda _template, account: create_attack(template, account))
    application = make_application(tmp_path)
    async with application.router.lifespan_context(application):
        batch = batch_module.create_standard_batch(application.state.store)
        documents = [DocumentText.model_validate(
            application.state.store.get_record("documents", invoice.document_id)["text"],
        ) for invoice in batch.invoices]
        lines = [line.text for document in documents for page in document.pages for line in page.lines]
        canary = next(re.search(r"CS-CANARY-[A-Z0-9]+", line).group() for line in lines if "CS-CANARY-" in line)
        attack_line = next(line for line in lines if canary in line)
        invalid_response = json.dumps({"steps": [{
            "id": "bad", "invoice_id": "D3", "op": attack_line, "args": {},
        }]})
        fake = RecordingPlannerClient(invalid_first_response=invalid_response)
        use_recording_planner(application, fake)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        # Extra trusted-record columns must not expand the planner projection.
        vendor = application.state.store.get_record("vendors", "VEN-003")
        vendor["unexpected_document_content"] = attack_line
        application.state.store.save_record("vendors", "VEN-003", vendor)
        final, transcript = await start_run(application, batch)
        assert final.lanes[0].status == "completed"
        assert final.tallies.countersign.not_on_file_count == 0
        assert final.tallies.countersign.paid_count + final.tallies.countersign.held_count == 5
        assert all(call.kwargs["to_account_id"] != next(row["account_id"] for row in application.state.store.list_records("bank_directory") if row["role"] == "attacker") for call in bank.transfer.await_args_list)
        assert len(fake.requests) == len(transcript.calls) == 2
        assert transcript.leak_guard.matches == 0
        assert transcript.leak_guard.strings_checked >= len(set(line for line in lines if len(line) >= 12)) * 2
        extracted_events = {
            event.payload.invoice_id: event.payload
            for event in application.state.store.list_events(final.run_id)
            if event.type == "fields.extracted"
        }
        for request, call in zip(fake.requests, transcript.calls):
            serialized = json.dumps({"system": request["system"], "messages": request["messages"]}, ensure_ascii=True)
            assert json.loads(call.prompt) == json.loads(serialized)
            assert hashlib.sha256(call.prompt.encode()).hexdigest() == call.prompt_hash
            candidates = [serialized, request["system"], *(message["content"] for message in request["messages"])]
            for line in lines:
                if len(line) >= 12:
                    assert all(line not in candidate for candidate in candidates), line
            assert not any(re.search(r"CS-CANARY-[A-Z0-9]{4}", candidate) for candidate in candidates)
            # Values that have no permitted trusted equivalent must stay opaque.
            for invoice in batch.invoices:
                assert all(invoice.invoice_number not in candidate for candidate in candidates)
            assert all("999900009999" not in candidate for candidate in candidates)
            assert "unexpected_document_content" not in serialized
            assert "$D3.remit_account_number" in serialized
            # Exact allowlist equality catches partial scalar leakage that a
            # whole-line test alone would miss (amounts, dates, emails, accounts).
            metadata = json.loads(request["messages"][0]["content"])
            assert set(metadata) <= {"instruction", "vendors", "invoices", "validation_error"}
            assert metadata["instruction"] == final.instruction
            expected_vendors = [{
                "vendor_id": row["vendor_id"], "name_words": row["name"].split(),
                "open_po_ids": [po["purchase_order_id"] for po in application.state.store.list_records("purchase_orders")
                                if po["vendor_id"] == row["vendor_id"] and po["status"] == "open"],
            } for row in application.state.store.list_records("vendors")]
            assert metadata["vendors"] == expected_vendors
            assert metadata["invoices"] == [{
                "invoice_id": f"D{position}",
                "handles": [handle.model_dump(mode="json")
                            for handle in extracted_events[invoice.invoice_id].handles],
            } for position, invoice in enumerate(batch.invoices, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("injection", ["document_line", "canary"])
async def test_broken_prompt_builder_is_stopped_before_client_call_and_emits_error(tmp_path, monkeypatch, injection):
    from app.pipeline import planner as planner_module

    application = make_application(tmp_path)
    async with application.router.lifespan_context(application):
        batch = batch_module.create_standard_batch(application.state.store)
        document = DocumentText.model_validate(application.state.store.get_record("documents", batch.invoices[2].document_id)["text"])
        injected_line = next(
            line.text for page in document.pages for line in page.lines
            if ("CS-CANARY-" in line.text if injection == "canary" else "Account number:" in line.text)
        )
        original_builder = planner_module.build_planner_prompt

        def broken_builder(*args, **kwargs):
            prompt = json.loads(original_builder(*args, **kwargs))
            prompt["messages"].append({"role": "user", "content": injected_line})
            return json.dumps(prompt)

        monkeypatch.setattr(planner_module, "build_planner_prompt", broken_builder)
        fake = RecordingPlannerClient()
        use_recording_planner(application, fake)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        final, transcript = await start_run(application, batch)
        assert final.lanes[0].status == "failed"
        assert final.tallies.countersign.held_count == 5
        assert not fake.requests and not transcript.calls
        assert transcript.leak_guard.matches > 0
        bank.transfer.assert_not_awaited()
        events = application.state.store.list_events(final.run_id)
        assert any(event.type == "error" and "leak_guard" in event.payload.message for event in events)
        errors = [event.payload.message for event in events if event.type == "error"]
        assert all(injected_line not in message for message in errors)


@pytest.mark.asyncio
async def test_two_invalid_responses_hold_the_entire_batch_with_planner_invalid(tmp_path):
    application = make_application(tmp_path)
    async with application.router.lifespan_context(application):
        batch = batch_module.create_standard_batch(application.state.store)
        fake = RecordingPlannerClient()

        async def invalid_response(**kwargs):
            fake.requests.append(deepcopy(kwargs))
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"steps": []}')])

        fake.messages.create = invalid_response
        use_recording_planner(application, fake)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        final, transcript = await start_run(application, batch)
        assert len(fake.requests) == len(transcript.calls) == 2
        assert final.lanes[0].status == "completed"
        assert final.tallies.countersign.held_count == 5
        bank.transfer.assert_not_awaited()
        proposals = [event.payload for event in application.state.store.list_events(final.run_id)
                     if event.type == "plan.proposed"]
        assert len(proposals) == 1
        assert len(proposals[0].steps) == 5
        assert {step.invoice_id for step in proposals[0].steps} == {invoice.invoice_id for invoice in batch.invoices}
        assert {step.args["invoice"].ref for step in proposals[0].steps} == {f"D{index}" for index in range(1, 6)}
        for step in proposals[0].steps:
            assert step.op == "hold"
            assert step.args["reason"].kind == "literal"
            assert step.args["reason"].value == "PLANNER_INVALID"
        for table in ("decisions", "approvals"):
            records = application.state.store.list_records(table, run_id=final.run_id)
            assert len(records) == 5
            assert {record["invoice_id"] for record in records} == {invoice.invoice_id for invoice in batch.invoices}
            assert all(record["reason_codes"] == ["PLANNER_INVALID"] for record in records)


@pytest.mark.asyncio
async def test_planner_provider_exception_is_safe_recorded_and_never_falls_back(tmp_path, caplog):
    application = make_application(tmp_path)
    async with application.router.lifespan_context(application):
        batch = batch_module.create_standard_batch(application.state.store)
        fake = RecordingPlannerClient()
        private_failure = "synthetic-provider-response-secret-must-not-escape"

        async def failed_response(**kwargs):
            fake.requests.append(deepcopy(kwargs))
            raise RuntimeError(private_failure)

        fake.messages.create = failed_response
        use_recording_planner(application, fake)
        bank = application.state.adapters.bank
        bank.transfer = AsyncMock(wraps=bank.transfer)
        final, transcript = await start_run(application, batch)
        assert len(fake.requests) == len(transcript.calls) == 1
        assert transcript.calls[0].response == ""
        assert transcript.calls[0].model == "test-planner"
        assert transcript.leak_guard.matches == 0
        assert final.lanes[0].status == "failed"
        assert final.tallies.countersign.held_count == 5
        assert application.state.adapters.planner.mode == "live"
        bank.transfer.assert_not_awaited()
        events = application.state.store.list_events(final.run_id)
        assert not any(event.type == "plan.proposed" for event in events)
        assert any(event.type == "error" and "planner" in event.payload.message for event in events)
        assert private_failure not in json.dumps([event.model_dump(mode="json") for event in events])
        assert private_failure not in caplog.text
        assert private_failure not in transcript.model_dump_json()
        for table in ("decisions", "approvals"):
            records = application.state.store.list_records(table, run_id=final.run_id)
            assert len(records) == 5
            assert all(record["reason_codes"] == ["PLANNER_INVALID"] for record in records)
