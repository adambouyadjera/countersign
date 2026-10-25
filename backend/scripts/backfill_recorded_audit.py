"""Index a saved bank-change recording into countersign-events, labelled as recorded evidence.

Read-only with respect to the recording and the application database: documents are
built from the evidence file (app.audit.recorded) and indexed with deterministic IDs
(recorded:<run_id>:<seq>), so re-running it updates the same documents.

Usage (from backend/, ELASTIC_MODE=live):
  .venv/bin/python scripts/backfill_recorded_audit.py --run-id run-1b936c2ea67f4236b644609ac7c72089
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import httpx

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.audit.events import EVENT_MAPPINGS, INDEX_NAME  # noqa: E402
from app.audit.recorded import RECORDED_SOURCE, recorded_documents  # noqa: E402
from app.config import Settings  # noqa: E402
from app.evidence import BankChangeEvidenceRepository  # noqa: E402


class BackfillError(RuntimeError):
    """A credential-safe failure; never echoes an upstream body."""


def _check(response: httpx.Response, what: str) -> dict:
    if not response.is_success:
        raise BackfillError(f"{what} returned HTTP {response.status_code}.")
    return response.json()


def backfill(run_id: str, settings: Settings) -> dict:
    if settings.elastic_mode != "live" or not settings.elastic_api_key.get_secret_value() or not settings.elastic_url:
        raise BackfillError("ELASTIC_MODE=live with ELASTIC_URL and ELASTIC_API_KEY is required.")
    replay = BankChangeEvidenceRepository().get_run(run_id)
    if replay is None:
        raise BackfillError(f"No recorded evidence for {run_id}.")
    documents = recorded_documents(replay)
    headers = {"Authorization": "ApiKey " + settings.elastic_api_key.get_secret_value()}
    with httpx.Client(base_url=settings.elastic_url.rstrip("/"), headers=headers, timeout=60) as client:
        # Additive mapping update: the strict index needs the two recorded-evidence fields.
        _check(client.put(f"/{INDEX_NAME}/_mapping", json=EVENT_MAPPINGS), "Mapping update")
        lines = []
        for doc_id, document in documents:
            lines.append(json.dumps({"index": {"_index": INDEX_NAME, "_id": doc_id}}, separators=(",", ":")))
            lines.append(json.dumps(document, separators=(",", ":"), allow_nan=False))
        body = _check(client.post("/_bulk?refresh=wait_for", content="\n".join(lines) + "\n",
                                  headers={"Content-Type": "application/x-ndjson"}), "Bulk index")
        if body.get("errors"):
            failed = [item["index"].get("status") for item in body["items"] if item["index"].get("error")]
            raise BackfillError(f"Bulk index rejected {len(failed)} documents (statuses {sorted(set(failed))}).")
        query = (f'FROM {INDEX_NAME} | WHERE run_id == "{run_id}" AND evidence_source == "{RECORDED_SOURCE}" '
                 "| STATS events = COUNT(*), off_file_payments = COUNT(CASE(type == \"payment.executed\" "
                 "AND payee_on_file == false, 1, null)) BY lane | SORT lane")
        result = _check(client.post("/_query", json={"query": query}), "ES|QL verification")
    columns = [column["name"] for column in result["columns"]]
    return {"run_id": run_id, "source_file": replay.source_file, "indexed": len(documents),
            "verification": [dict(zip(columns, row)) for row in result["values"]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", default="run-1b936c2ea67f4236b644609ac7c72089")
    args = parser.parse_args()
    try:
        print(json.dumps(backfill(args.run_id, Settings()), indent=2))
    except BackfillError as error:
        print(f"backfill failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
