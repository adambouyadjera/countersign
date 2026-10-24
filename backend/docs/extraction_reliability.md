# Quarantined extraction reliability

Live Countersign extraction uses this order:

1. Reuse a verified cache entry for the exact PDF bytes and extractor prompt/schema version.
2. Call Gemini with `EXTRACTOR_MODEL`, using its existing bounded request retries.
3. After exhausted HTTP 429/503 or timeout failures, or invalid schema/source output,
   use Anthropic with `EXTRACTOR_FALLBACK_MODEL` (default `claude-haiku-5-5`).
4. If no eligible provider supplies valid source-grounded fields, retain the
   existing `UNVERIFIED_SOURCE` hold. Authentication/configuration failures do
   not silently select another provider.

The fallback is explicitly configured and reported. Both providers receive the
same `EXTRACTION_SYSTEM_PROMPT`, the same logical `RawExtraction` schema, and no
tools. Neither receives vendor-master payment authority. HTTP-200 schema/source
failures move to Haiku immediately; Gemini's existing retries cover request
failures. The AP agent's prompt is independent and unchanged.

Anthropic's wire schema uses the SDK's `transform_schema` helper because its
structured-output API rejects numeric `minimum` constraints. The local
`RawExtraction` schema still enforces those constraints, including one-based
page/line coordinates. The same deterministic span/value verifier is applied
before either provider's result can enter the handle table and policy gate.
See [Anthropic's schema limitations](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#json-schema-limitations).

## Cache and source verification

`EXTRACTOR_CACHE=on|off` defaults to `on`. Live application instances store cache
entries in `backend/data/extraction_cache.db`, which is gitignored. Mock
transports and in-memory application stores use an isolated in-memory cache.
Offline extraction remains explicitly offline and does not reuse live results.

The cache key binds SHA-256 of the original PDF bytes to a version derived from
the exact extraction prompt and output schema. PDF text is regenerated and
compared with the provided document before looking up claims. A changed file or
prompt/schema misses the cache. Newly generated PDFs can have different bytes
even when their visible invoice data matches; those are legitimate misses.

Cache records contain identity-free claims, original provider/model, and the
original UTC extraction timestamp. Loading one rebinds invoice/document IDs,
clears handles and all verification flags, and rechecks the spans and payment
values against the current document. Invalid or corrupted claims are treated as
a miss. Cache hits never change source trust or cache payment decisions.

Each `fields.extracted` event carries `extraction_provenance`, including producer,
model, timestamp, `cached`, document digest, prompt version, and provider attempts.
The event's top-level provider/model identifies the producer too. The graph has
an extraction-operation node labelled `(fresh)` or `(cached)`, with the same
metadata on fields and handles. A cached result retains its original timestamp;
it is never labelled as a fresh inference call.

## Real Haiku check

```sh
.venv/bin/python scripts/check_extraction.py --force-haiku --output docs/haiku_extraction_live.json
```

`--force-haiku` skips Gemini and cached data for this diagnostic. The check makes
no bank or AP-agent calls. The recorded successful check returned HTTP 200 from
`claude-haiku-5-5`; all 13 extracted fields had verified spans, and provenance
records `cached: false`. [Real evidence](haiku_extraction_live.json).

The initial rejected request is preserved in
[haiku_extraction_initial_400.json](haiku_extraction_initial_400.json); its wire
schema was corrected before the successful check. No model or system prompt was
changed to obtain the result.

See [bank_change_workflow.md](bank_change_workflow.md) for the separately saved
live comparison runs and the earlier results. Gemini remains reserved for the
Countersign extractor; the agent and mutator stay on Bitdeer.
