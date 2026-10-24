"""Document-byte cache identities and cached provenance remain explicit."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from app.contract_models import DocumentText
from app.pipeline.offline_extractor import parse_offline
from app.pipeline.spans import to_payload, verify_span
from app.security.extraction_cache import (
    ExtractionCache, ExtractionCacheError, cache_key, document_sha256,
)
from app.store import Store
from app.world.batches import create_standard_batch


PROVIDER = "anthropic"
MODEL = "claude-haiku-test"
VERSION = "quarantine-prompt-schema-v1"
ORIGINAL_TIME = "2026-10-24T20:00:00Z"


@pytest.fixture
def source():
    store = Store(":memory:")
    store.initialize()
    try:
        batch = create_standard_batch(store)
        invoice = batch.invoices[2]
        record = store.get_record("documents", invoice.document_id)
        document = DocumentText.model_validate(record["text"])
        payload = to_payload(document, parse_offline(document), MODEL)
        return base64.b64decode(record["pdf_base64"]), document, payload
    finally:
        store.close()


def put(cache, source, **overrides):
    pdf, _, payload = source
    return cache.put(document_sha256(pdf), VERSION, payload,
                     **{"provider": PROVIDER, "model": MODEL, "extracted_at": ORIGINAL_TIME, **overrides})


def test_cache_miss_hit_and_persistence_retain_actual_producer(tmp_path, source):
    path = tmp_path / "extraction_cache.db"
    cache = ExtractionCache(path)
    digest = document_sha256(source[0])
    assert cache.get(digest, VERSION) is None
    original = put(cache, source)
    cache.close()
    reopened = ExtractionCache(path)
    try:
        entry = reopened.get(digest, VERSION)
        assert entry == original
        assert entry.metadata() == {
            "provider": PROVIDER, "model": MODEL, "extracted_at": ORIGINAL_TIME,
            "cached": True, "cache_key": cache_key(digest, VERSION),
            "document_sha256": digest, "prompt_version": VERSION,
        }
        assert reopened.get(digest, VERSION).metadata()["extracted_at"] == ORIGINAL_TIME
    finally:
        reopened.close()


def test_cache_key_changes_with_file_bytes_or_prompt_version(source):
    pdf, _, _ = source
    cache = ExtractionCache(":memory:")
    try:
        put(cache, source)
        # A PDF comment can change file bytes without changing extracted lines.
        changed_digest = document_sha256(pdf + b"\n% cache identity change\n")
        assert changed_digest != document_sha256(pdf)
        assert cache.get(changed_digest, VERSION) is None
        assert cache.get(document_sha256(pdf), VERSION + "-v2") is None
    finally:
        cache.close()


def test_cached_claims_rebind_new_ids_clear_handles_and_require_reverification(source):
    pdf, document, payload = source
    cache = ExtractionCache(":memory:")
    try:
        put(cache, source)
        entry = cache.get(document_sha256(pdf), VERSION)
        assert document.document_id not in entry._payload_json
        assert document.invoice_id not in entry._payload_json
        current = document.model_copy(update={"document_id": "new-document", "invoice_id": "new-invoice"})
        replay = entry.to_payload(current)
        assert replay.invoice_id == current.invoice_id
        assert replay.extractor_model == MODEL
        assert replay.handles == []
        assert [field.value for field in replay.fields] == [field.value for field in payload.fields]
        for field in replay.fields:
            assert field.trust == "untrusted"
            if field.span is not None:
                assert field.span.document_id == current.document_id
                assert field.span.span_verified is False
                if field.span.quote:
                    assert verify_span(current, field.span).span_verified is True
        for span in replay.instruction_like_spans:
            assert span.document_id == current.document_id and not span.span_verified
            assert verify_span(current, span).span_verified is True
    finally:
        cache.close()


def test_cache_cannot_advertise_a_hit_as_fresh_inference(source):
    cache = ExtractionCache(":memory:")
    try:
        entry = put(cache, source)
        with pytest.raises(ValueError, match="cannot be reported as fresh"):
            entry.metadata(cached=False)
        assert entry.metadata()["cached"] is True
    finally:
        cache.close()


def test_payload_copies_do_not_mutate_the_cached_record(source):
    cache = ExtractionCache(":memory:")
    try:
        entry = put(cache, source)
        claims = entry.payload
        claims["fields"][0]["value"] = "attacker replacement"
        assert entry.payload["fields"][0]["value"] != "attacker replacement"
        replay = entry.to_payload(source[1])
        replay.fields[0].value = "mutated consumer"
        assert entry.to_payload(source[1]).fields[0].value != "mutated consumer"
    finally:
        cache.close()


def test_fabricated_cached_claim_never_becomes_verified(source):
    pdf, document, payload = source
    payload = payload.model_copy(deep=True)
    span = payload.fields[0].span
    span.quote = "This quote is absent from the PDF"
    span.span_verified = True
    cache = ExtractionCache(":memory:")
    try:
        # The cache does not confer trust, even if an internal caller incorrectly
        # supplies a self-certified claim. The wrapper re-verifies every hit.
        cache.put(document_sha256(pdf), VERSION, payload, provider=PROVIDER,
                  model=MODEL, extracted_at=ORIGINAL_TIME)
        replay = cache.get(document_sha256(pdf), VERSION).to_payload(document)
        assert replay.fields[0].span.span_verified is False
        assert verify_span(document, replay.fields[0].span).span_verified is False
    finally:
        cache.close()


@pytest.mark.parametrize("corruption", ["invalid_json", "payload_hash", "verified_flag", "source_identity"])
def test_corrupt_or_self_certified_cache_record_is_a_miss(tmp_path, source, corruption):
    path = tmp_path / "extraction_cache.db"
    cache = ExtractionCache(path)
    try:
        entry = put(cache, source)
        data = entry.payload
        if corruption == "verified_flag":
            data["fields"][0]["span"]["span_verified"] = True
        elif corruption == "source_identity":
            data["fields"][0]["span"]["document_id"] = "foreign-document"
        serialized = "{broken" if corruption == "invalid_json" else json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        checksum = "0" * 64 if corruption == "payload_hash" else hashlib.sha256(serialized.encode()).hexdigest()
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE extraction_cache_v1 SET payload_json=?, payload_sha256=? WHERE cache_key=?",
                               (serialized, checksum, entry.cache_key))
        assert cache.get(document_sha256(source[0]), VERSION) is None
    finally:
        cache.close()


def test_actual_fresh_result_may_replace_cache_origin_but_hits_keep_it(source):
    cache = ExtractionCache(":memory:")
    try:
        put(cache, source)
        payload = source[2].model_copy(update={"extractor_model": "gemini-test"})
        later = datetime(2026, 10, 24, 21, tzinfo=timezone.utc)
        cache.put(document_sha256(source[0]), VERSION, payload, provider="gemini",
                  model="gemini-test", extracted_at=later)
        entry = cache.get(document_sha256(source[0]), VERSION)
        assert entry.provider == "gemini" and entry.model == "gemini-test"
        assert entry.extracted_at == later.isoformat() and entry.metadata()["cached"]
    finally:
        cache.close()


@pytest.mark.parametrize("timestamp", ["", "not a timestamp", "2026-10-24T20:00:00", "2026-10-24T20:00:00+01:00"])
def test_cache_requires_original_utc_timestamp(source, timestamp):
    cache = ExtractionCache(":memory:")
    try:
        with pytest.raises(ValueError, match="original UTC"):
            put(cache, source, extracted_at=timestamp)
    finally:
        cache.close()


def test_cache_rejects_mislabeled_model(source):
    cache = ExtractionCache(":memory:")
    try:
        with pytest.raises(ValueError, match="recorded producer"):
            put(cache, source, model="another-model")
    finally:
        cache.close()


def test_broken_optional_cache_reads_miss_and_writes_fail_safely(tmp_path, source):
    path = tmp_path / "broken.db"
    path.write_bytes(b"not sqlite")
    cache = ExtractionCache(path)
    try:
        assert cache.get(document_sha256(source[0]), VERSION) is None
        with pytest.raises(ExtractionCacheError, match="could not store"):
            put(cache, source)
    finally:
        cache.close()


@pytest.mark.parametrize("digest,version", [("not-sha256", VERSION), ("a" * 64, ""), ("A" * 64, VERSION)])
def test_invalid_identity_cannot_access_cache(digest, version):
    with pytest.raises(ValueError):
        cache_key(digest, version)
