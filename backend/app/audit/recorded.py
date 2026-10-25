"""Audit documents for a saved recording of a finished run (read-only evidence).

The recording's events are mapped by the same projection as live events, using a
throwaway in-memory store filled from the recording itself, so enrichment matches
what the live run indexed. Documents are labelled as recorded evidence and use
deterministic IDs, so re-indexing is idempotent and never collides with live IDs.
"""

from app.audit.events import map_event
from app.contract_models import BankChangeReplay
from app.store import Store

RECORDED_SOURCE = "recorded_evidence"


def recorded_document_id(run_id: str, seq: int) -> str:
    return f"recorded:{run_id}:{seq}"


def recorded_documents(replay: BankChangeReplay) -> list[tuple[str, dict]]:
    evidence = replay.evidence
    store = Store(":memory:")
    store.initialize()
    store.save_run(replay.run)
    store.save_record("batches", replay.batch.batch_id, replay.batch)
    for approval in [*evidence.get("approvals", []), *([evidence["pending_approval"]] if evidence.get("pending_approval") else [])]:
        store.save_record("approvals", approval["approval_id"], approval)
    for decision in evidence.get("decisions", []):
        store.save_record("decisions", decision["decision_id"], decision)
    for transfer in evidence.get("transfers", []):
        store.save_record("transfers", transfer["transfer_id"], transfer)
    if replay.held_invoice_graph is not None:
        graph = replay.held_invoice_graph
        store.save_record("graphs", f"{graph.run_id}:{graph.invoice_id}", graph)
    documents = []
    for event in replay.events:
        document = map_event(store, event)
        document["evidence_source"] = RECORDED_SOURCE
        document["recorded_source_file"] = replay.source_file
        documents.append((recorded_document_id(event.run_id, event.seq), document))
    return documents
