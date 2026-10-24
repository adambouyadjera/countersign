"""Persist extraction claims and their original producer, never source authority.

The caller supplies a digest of the original document bytes and a version of the
quarantined prompt/schema. Cache records intentionally contain no invoice or
document identities. Loading one binds it to the current document and clears
verification flags; the deterministic verifier must certify the claims again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
from typing import Any

from app.contract_models import DocumentText, FieldsExtractedPayload


DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "extraction_cache.db"
_PAYLOAD_KEYS = frozenset({"invoice_id", "extractor_model", "fields", "handles", "instruction_like_spans"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ExtractionCacheError(RuntimeError):
    """An optional cache operation failed without reflecting stored content."""


def document_sha256(document_bytes: bytes) -> str:
    """Hash file bytes, not extracted text or a client-supplied document ID."""
    if not isinstance(document_bytes, bytes):
        raise TypeError("Original document bytes are required for extraction caching")
    return hashlib.sha256(document_bytes).hexdigest()


def cache_key(document_digest: str, prompt_version: str) -> str:
    if not isinstance(document_digest, str) or _DIGEST.fullmatch(document_digest) is None:
        raise ValueError("A lowercase SHA-256 document digest is required")
    if not isinstance(prompt_version, str) or not prompt_version.strip() or len(prompt_version) > 4096:
        raise ValueError("A nonempty bounded extractor prompt version is required")
    encoded = json.dumps([document_digest, prompt_version], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _timestamp(value: str | datetime) -> str:
    if isinstance(value, datetime):
        parsed, original = value, value.isoformat()
    elif isinstance(value, str):
        try:
            parsed, original = datetime.fromisoformat(value.replace("Z", "+00:00")), value
        except ValueError:
            raise ValueError("An original UTC extraction timestamp is required") from None
    else:
        raise ValueError("An original UTC extraction timestamp is required")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("An original UTC extraction timestamp is required")
    return original


def _claims(payload: FieldsExtractedPayload | dict[str, Any]) -> dict[str, Any]:
    validated = FieldsExtractedPayload.model_validate(payload)
    data = {name: validated.model_dump(mode="json")[name] for name in _PAYLOAD_KEYS}
    data["invoice_id"] = ""
    data["handles"] = []
    for field in data["fields"]:
        if field["span"] is not None:
            field["span"]["document_id"] = ""
            field["span"]["span_verified"] = False
    for span in data["instruction_like_spans"]:
        span["document_id"] = ""
        span["span_verified"] = False
    return data


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    document_sha256: str
    prompt_version: str
    provider: str
    model: str
    extracted_at: str
    _payload_json: str

    @property
    def payload(self) -> dict[str, Any]:
        """Return an independent copy of identity-free, unverified claims."""
        return json.loads(self._payload_json)

    def to_payload(self, document: DocumentText) -> FieldsExtractedPayload:
        data = self.payload
        data["invoice_id"] = document.invoice_id
        for field in data["fields"]:
            if field["span"] is not None:
                field["span"]["document_id"] = document.document_id
                field["span"]["span_verified"] = False
        for span in data["instruction_like_spans"]:
            span["document_id"] = document.document_id
            span["span_verified"] = False
        return FieldsExtractedPayload.model_validate(data)

    def metadata(self, *, cached: bool = True) -> dict[str, Any]:
        if cached is not True:
            raise ValueError("A cache entry cannot be reported as fresh inference")
        return {"provider": self.provider, "model": self.model, "extracted_at": self.extracted_at,
                "cached": True, "cache_key": self.cache_key,
                "document_sha256": self.document_sha256, "prompt_version": self.prompt_version}


class ExtractionCache:
    """Small independent SQLite cache; failures never manufacture a cache hit."""

    def __init__(self, path: str | Path = DEFAULT_CACHE_PATH):
        self.path = Path(path) if str(path) != ":memory:" else ":memory:"
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()

    def initialize(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            if isinstance(self.path, Path):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS extraction_cache_v1 ("
                    "cache_key TEXT PRIMARY KEY, document_sha256 TEXT NOT NULL, prompt_version TEXT NOT NULL, "
                    "provider TEXT NOT NULL, model TEXT NOT NULL, extracted_at TEXT NOT NULL, "
                    "payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL)"
                )
            except BaseException:
                connection.close()
                raise
            self._connection = connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _db(self) -> sqlite3.Connection:
        self.initialize()
        assert self._connection is not None
        return self._connection

    def get(self, document_digest: str, prompt_version: str) -> CacheEntry | None:
        key = cache_key(document_digest, prompt_version)
        with self._lock:
            try:
                row = self._db().execute(
                    "SELECT * FROM extraction_cache_v1 WHERE cache_key = ?", (key,)
                ).fetchone()
            except (sqlite3.Error, OSError):
                return None
            if row is None:
                return None
            try:
                if (row["document_sha256"] != document_digest or row["prompt_version"] != prompt_version
                        or row["payload_sha256"] != hashlib.sha256(row["payload_json"].encode()).hexdigest()):
                    return None
                data = json.loads(row["payload_json"])
                if not isinstance(data, dict) or set(data) != _PAYLOAD_KEYS:
                    return None
                # A valid cache-format record has no prior identities, handles,
                # verification flags or producer metadata embedded in its claims.
                if _canonical(_claims(data)) != row["payload_json"]:
                    return None
                if (not row["provider"].strip() or not row["model"].strip()
                        or data["extractor_model"] != row["model"]):
                    return None
                original_time = _timestamp(row["extracted_at"])
                return CacheEntry(key, document_digest, prompt_version, row["provider"], row["model"],
                                  original_time, row["payload_json"])
            except (ValueError, TypeError, KeyError):
                return None

    def put(self, document_digest: str, prompt_version: str, payload: FieldsExtractedPayload | dict[str, Any],
            *, provider: str, model: str, extracted_at: str | datetime) -> CacheEntry:
        key = cache_key(document_digest, prompt_version)
        if not isinstance(provider, str) or not provider.strip() or not isinstance(model, str) or not model.strip():
            raise ValueError("The actual extraction provider and model are required")
        original_time = _timestamp(extracted_at)
        data = _claims(payload)
        if data["extractor_model"] != model:
            raise ValueError("Cached extraction model must match its recorded producer")
        serialized = _canonical(data)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        with self._lock:
            try:
                self._db().execute(
                    "INSERT INTO extraction_cache_v1 "
                    "(cache_key, document_sha256, prompt_version, provider, model, extracted_at, payload_json, payload_sha256) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
                    "document_sha256=excluded.document_sha256, prompt_version=excluded.prompt_version, "
                    "provider=excluded.provider, model=excluded.model, extracted_at=excluded.extracted_at, "
                    "payload_json=excluded.payload_json, payload_sha256=excluded.payload_sha256",
                    (key, document_digest, prompt_version, provider, model, original_time, serialized, digest),
                )
            except (sqlite3.Error, OSError):
                raise ExtractionCacheError("Extraction cache could not store the verified result") from None
        return CacheEntry(key, document_digest, prompt_version, provider, model, original_time, serialized)
