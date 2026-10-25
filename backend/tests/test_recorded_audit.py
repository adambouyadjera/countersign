"""Backfilling a saved recording into the audit index (offline: document building only)."""

from app.audit.events import EVENT_MAPPINGS, map_event
from app.audit.definitions import AGENT_INSTRUCTIONS, tool_definitions
from app.audit.recorded import RECORDED_SOURCE, recorded_documents
from app.evidence import BankChangeEvidenceRepository
from app.store import Store

RUN_ID = "run-1b936c2ea67f4236b644609ac7c72089"


def test_recorded_run_documents_show_the_unguarded_off_file_payment_and_the_countersign_hold():
    replay = BankChangeEvidenceRepository().get_run(RUN_ID)
    documents = recorded_documents(replay)
    assert len(documents) == len(replay.events) == 70
    assert all(doc_id == f"recorded:{RUN_ID}:{doc['seq']}" for doc_id, doc in documents)
    assert all(doc["evidence_source"] == RECORDED_SOURCE and doc["recorded_source_file"] == replay.source_file
               for _, doc in documents)
    docs = [doc for _, doc in documents]
    off_file = [d for d in docs if d["type"] == "payment.executed" and d["payee_on_file"] is False]
    assert len(off_file) == 1
    [paid] = off_file
    assert (paid["lane"], paid["invoice_number"], paid["requested_account"], paid["amount_cents"]) == (
        "unguarded", "INV-003", "999900009999", 64000)
    assert paid["mode"] == "live" and paid["transfer_id"]
    hold = next(d for d in docs if d["type"] == "policy.decision" and d["invoice_id"] == paid["invoice_id"])
    assert (hold["lane"], hold["outcome"], hold["reason_codes"][0]) == ("countersign", "HOLD", "BANK_DETAILS_MISMATCH")
    assert hold["requested_account"] == "999900009999" and hold["account_on_file"] == "300300003333"
    assert not any(d["type"] == "payment.executed" and d["lane"] == "countersign" and d["invoice_id"] == paid["invoice_id"]
                   for d in docs)


def test_new_fields_are_mapped_and_live_events_leave_them_empty():
    props = EVENT_MAPPINGS["properties"]
    assert props["evidence_source"] == props["recorded_source_file"] == {"type": "keyword"}
    store = Store(":memory:")
    store.initialize()
    replay = BankChangeEvidenceRepository().get_run(RUN_ID)
    live_doc = map_event(store, replay.events[0])
    assert live_doc["evidence_source"] is None and live_doc["recorded_source_file"] is None


def test_agent_tools_return_the_recorded_label():
    tools = {tool["id"]: tool for tool in tool_definitions()}
    for name in ("held_payments_by_reason", "payments_to_accounts_not_on_file"):
        assert "evidence_source" in tools[name]["configuration"]["query"]
    assert "recorded_evidence" in AGENT_INSTRUCTIONS
