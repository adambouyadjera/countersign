"""Verify a finished planted-suite cache with every model path disabled.

All input SQLite connections are read-only. It creates only its requested JSON output file.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.adapters.extraction import EXTRACTION_PROMPT_VERSION, ResilientExtractor
from app.contract_models import DocumentText
from app.evaluation.extraction_once import OncePerDocumentExtractor
from app.security.extraction_cache import (
    DEFAULT_CACHE_PATH, ExtractionCache, cache_key, document_sha256,
)
from app.store import DEFAULT_DATABASE_PATH, Store


def readonly_connection(path):
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                 check_same_thread=False, isolation_level=None, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


class ReadOnlyStore(Store):
    def initialize(self):
        with self._lock:
            if self._connection is None:
                self._connection = readonly_connection(self.path)

    @contextmanager
    def transaction(self):
        # OncePerDocumentExtractor only reads an accepted receipt. Any path
        # attempting a new claim is blocked by both this store and SQLite.
        with self._lock:
            connection = self._db()
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                connection.rollback()

    def save_record(self, *args, **kwargs):
        raise AssertionError("Cache verification cannot create or alter any receipt/store record")


class ReadOnlyCache(ExtractionCache):
    def initialize(self):
        with self._lock:
            if self._connection is None:
                self._connection = readonly_connection(self.path)

    def put(self, *args, **kwargs):
        raise AssertionError("Cache verification cannot create or repair an extraction cache entry")


class NoProvider:
    model = "disabled-for-read-only-cache-verification"

    async def extract(self, *args, **kwargs):
        raise AssertionError("No provider extraction is permitted")


class CacheReplayOnly(ResilientExtractor):
    def __init__(self, store, cache):
        super().__init__(NoProvider(), NoProvider(), store, cache=cache)
        self.fresh_calls = 0

    async def extract(self, *args, **kwargs):
        self.fresh_calls += 1
        raise AssertionError("A fresh logical extraction was attempted during cache-only verification")


def table_digest(connection, table, key):
    # Identifiers here are fixed server-owned constants, never CLI input.
    rows = [list(row) for row in connection.execute(f"SELECT {key}, data FROM {table} ORDER BY {key}")]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def cache_digest(cache):
    rows = [list(row) for row in cache._db().execute(
        "SELECT * FROM extraction_cache_v1 ORDER BY cache_key")]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def same_origin(actual, original):
    # Pydantic's JSON representation uses Z; datetime.isoformat uses +00:00.
    # Both encode the original UTC instant and must compare equivalently.
    return (actual["provider"] == original["provider"]
            and actual["model"] == original["model"]
            and datetime.fromisoformat(actual["extracted_at"].replace("Z", "+00:00"))
                == datetime.fromisoformat(original["extracted_at"].replace("Z", "+00:00")))


async def verify(args):
    job_dir = args.data_root / args.job_id
    evidence = json.loads((job_dir / "evidence.json").read_text())
    if evidence.get("job", {}).get("job_id") != args.job_id:
        raise SystemExit("The evidence does not belong to the requested job")
    if evidence["job"]["status"] not in {"completed", "failed"}:
        raise SystemExit("The evaluation is still running; cache verification was not started")
    receipts = ReadOnlyStore(args.database)
    receipts.initialize()
    cache = ReadOnlyCache(args.cache)
    cache.initialize()
    records, skipped, failures = [], [], []
    total_fresh_calls = 0
    receipt_before = table_digest(receipts._db(), "eval_receipts", "receipt_id")
    cache_before = cache_digest(cache)
    try:
        manifest = evidence["manifest"]["items"]
        for item in manifest:
            source = ReadOnlyStore(job_dir / f"{item['item_id']}.db")
            source.initialize()
            delegate = CacheReplayOnly(source, cache)
            wrapper = OncePerDocumentExtractor(delegate, receipts)
            try:
                raw = source.get_record("documents", item["document_id"])
                if raw is None:
                    skipped.append({"item_id": item["item_id"], "status": "source_document_missing"})
                    continue
                document = DocumentText.model_validate(raw["text"])
                digest = document_sha256(delegate._source_bytes(document))
                if digest != item["sha256"]:
                    raise AssertionError("Private original PDF differs from the frozen manifest")
                receipt_id = "eval-extraction-" + cache_key(digest, EXTRACTION_PROMPT_VERSION)
                receipt = receipts.get_record("eval_receipts", receipt_id)
                if receipt is None or receipt.get("status") != "accepted":
                    skipped.append({"item_id": item["item_id"], "document_sha256": digest,
                                    "receipt_id": receipt_id,
                                    "status": receipt.get("status") if receipt else "receipt_missing"})
                    continue
                case = evidence["cases"].get(item["item_id"], {})
                original = next((row["extraction_provenance"] for row in case.get("extractions", [])
                                 if row.get("extraction_provenance")), None)
                result = await wrapper.extract(document)
                origin = result.extraction_provenance
                if origin is None or origin.cached is not True:
                    raise AssertionError("Cache replay was not labelled cached=true")
                if result.invoice_id != document.invoice_id:
                    raise AssertionError("Cached extraction was not rebound to its original invoice")
                actual_origin = {"provider": origin.provider, "model": origin.model,
                                 "extracted_at": origin.extracted_at.isoformat()}
                if not same_origin(actual_origin, receipt["origin"]):
                    raise AssertionError("Cached extraction changed its accepted producer or timestamp")
                if original is not None and not same_origin(actual_origin, original):
                    raise AssertionError("Cached producer/time differs from the original saved run")
                spans = [field.span for field in result.fields if field.span is not None]
                spans += result.instruction_like_spans
                if not all(span.span_verified and span.document_id == document.document_id for span in spans):
                    raise AssertionError("A cached source span failed deterministic re-verification")
                if wrapper.receipt_events[0]["event"] != "cache_hit" or delegate.fresh_calls:
                    raise AssertionError("Receipt replay did not remain on the cache-only path")
                records.append({"item_id": item["item_id"], "invoice_id": document.invoice_id,
                    "document_id": document.document_id, "document_sha256": digest,
                    "receipt_id": receipt_id, "cached": True, "spans_verified": len(spans),
                    "original_origin_preserved": True, "original_evidence_origin_available": original is not None,
                    "fresh_calls": delegate.fresh_calls, **actual_origin,
                    "receipt_event": wrapper.receipt_events[0]})
            except Exception as error:
                # No provider response or arbitrary exception body is reflected.
                failures.append({"item_id": item["item_id"], "error_type": type(error).__name__})
            finally:
                total_fresh_calls += delegate.fresh_calls
                source.close()
        receipt_after = table_digest(receipts._db(), "eval_receipts", "receipt_id")
        cache_after = cache_digest(cache)
        summary = {"job_id": args.job_id, "verified_at": datetime.now(timezone.utc).isoformat(),
            "source_evidence": str((job_dir / "evidence.json").relative_to(BACKEND)),
            "read_only": True, "provider_clients_created": 0, "fresh_extraction_calls": total_fresh_calls,
            "manifest_items": len(manifest), "accepted_receipts_verified": len(records),
            "skipped_count": len(skipped), "failure_count": len(failures),
            "receipts_unchanged": receipt_before == receipt_after,
            "cache_unchanged": cache_before == cache_after,
            "prompt_version": EXTRACTION_PROMPT_VERSION,
            "passed": not failures and total_fresh_calls == 0
                and receipt_before == receipt_after and cache_before == cache_after,
            "items": records, "skipped": skipped, "failures": failures}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({key: value for key, value in summary.items()
                          if key not in {"items", "skipped", "failures", "prompt_version"}}))
        return 0 if summary["passed"] else 1
    finally:
        cache.close()
        receipts.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--data-root", type=Path, default=BACKEND / "data/generated/eval")
    parser.add_argument("--output", type=Path, default=BACKEND / "docs/eval_cache_verification.json")
    raise SystemExit(asyncio.run(verify(parser.parse_args())))


if __name__ == "__main__":
    main()
