# B3 live integration failure diagnosis and fix

## Root cause

Gemini 3.8 requests exceeded deadlines and returned HTTP 504/503/429, while B3's
broad exception handler erased the cause and failed to create holds.

This sentence describes the confirmed reproduction and application defects. It
does not claim that every exception in the original run was a timeout or a rate
limit; those original exception details were discarded.

## Original run and missing traceback

Original live run: `run-1203ba14911e43648c080b603df2d1dc`.
Saved stream: `/tmp/countersign-acceptance-live.sse`.

INV-001 extracted and passed its policy checks, and Nessie accepted its payment
with status `pending`. INV-002 through INV-005 emitted only a generic error.
The first two errors were separated by approximately 120 seconds; the final two
came much faster. That timing is consistent with more than one possible cause,
but it is not sufficient evidence to identify an HTTP status.

The original runner caught each exception and emitted a fixed message without
logging its traceback. The original worker's output was not retained in the
later detached-worker log. Consequently, the exact exceptions for that run
cannot be recovered from the available log or SSE file. In particular, the
fast failures cannot honestly be labeled HTTP 429 without a recorded response.

## Live reproduction of INV-002

The diagnostic uses the original stored INV-002 PDF and numbered source text.
It records stage timings and redacted response data. It performs no bank write.

Baseline evidence: `/tmp/b3-failing-invoice-repro.jsonl`.

- PDF extraction succeeded in 0.139 seconds: one page, 19 lines, matching stored
  document text.
- The original Gemini request returned HTTP 200 at 78.214 seconds.
- Local extraction-schema validation succeeded.
- All 13 emitted field spans verified against the original source.
- Nessie was not called by this extraction-only reproduction.

This demonstrates that the invoice and schema can succeed, while the live
provider can take substantially longer than the requested 45-second limit.

Explicit-deadline evidence: `/tmp/b3-fixed-invoice-repro.jsonl`.

- PDF extraction again succeeded and matched the stored text.
- The 45-second Gemini probe returned HTTP 504 at 44.148 seconds.
- Schema validation and span verification were not reached; Nessie was not
  called.

The actual provider error body was:

```json
{
  "error": {
    "code": 504,
    "message": "Deadline expired before operation could complete.",
    "status": "DEADLINE_EXCEEDED"
  }
}
```

Credentials are redacted in diagnostic output. Opaque provider thought
signatures and full successful provider responses are not copied into this
repository report.

The first instrumented 3.8 batch, `run-cd8ca3f7b4a343fcbc0d97bfbcf923dd`,
also reproduced HTTP 503 and HTTP 429. It finished with one accepted payment
and four explicit `UNVERIFIED_SOURCE` holds. This verifies safe failure handling
but does not meet the healthy-batch acceptance target. Its stream remains at
`/tmp/b3-bugfix-live.sse`.

## Verified low-latency live model

The authenticated model catalog lists `models/gemini-3.5-flash-lite` with
`generateContent` support. With the exact same source, schema, and verification,
it returned INV-002 in 2.511 seconds; local schema validation and all 13 spans
passed at 2.522 seconds. It then passed the live INV-003 remit/canary test in
3.31 seconds. No bank operation was made by these extraction-only probes.

`EXTRACTOR_MODEL=gemini-3.5-flash-lite` is now the explicit default/example and
local configuration. There is no automatic model fallback. The live extractor
retains structured output, no tools, strict local schema validation, and the
same source/policy checks. This configuration change makes the 45-second bound
practical without increasing that deadline or substituting parsed/fake data.

## Minimal operational fixes

- Gemini has both a 45-second SDK timeout and a 45-second application deadline
  per attempt. Long SDK-side work cannot bypass the application deadline.
- The adapter has a shared semaphore limiting extraction concurrency to two.
- Retryable Gemini requests permit at most three attempts, including the first
  attempt. HTTP 408, 429, 500, 502, 503, 504 and retryable network/timeouts use
  bounded exponential backoff, with base delays of 1 and 2 seconds. Provider
  `Retry-After` or `google.rpc.RetryInfo` guidance is honored up to 30 seconds.
- Schema-validation, truncated-output, and span-verification failures keep
  their specific stages and do not silently become successful extractions.
- Every Nessie request has a 15-second deadline. Financial POSTs are never
  blindly retried; persisted paired-operation IDs remain available for
  reconciliation.
- Live failures remain live failures. No network error selects the offline
  extractor or fabricates a field, transfer ID, or successful payment.

The adapter retains structured safe metadata (`stage`, `cause`, HTTP status,
retryability, and attempt count) instead of forwarding arbitrary raw SDK errors
into public events. Stage errors cover `pdf_text`, `gemini`, `schema`,
`span_verification`, `policy`, and `nessie`.

## Error and hold behavior

A failed invoice emits a stage-aware `error`, a HOLD policy decision, and
`payment.held`, with a valid persisted approval using `UNVERIFIED_SOURCE`.
It increases `held_count`; the lane still records failure rather than claiming
that the full run succeeded. Processing continues to the next invoice.

Example safe summaries include:

```text
extraction timed out after 45 s (gemini)
extraction rate limited after 3 attempts (gemini, HTTP 429)
Gemini response did not match the invoice extraction schema (schema)
payment timed out after 15 s (nessie); reconcile persisted bank attempts before retrying
```

Failures before banking cannot move money. A failure after a bank submission
requires reconciliation of the recorded operation: the hold is not a claim
that an ambiguous remote operation has been canceled or reversed.

## Numeric quantity and exact contract request

`line_items.0.quantity` now carries a JSON number and `type: "number"`.
The corresponding opaque handle also has `type: "number"`. Decimal quantities
remain numbers; values are not coerced into strings.

Backend `FieldType` includes the requested additive `number` label. Following
AGENTS.md, no files under the read-only `contracts/` directory were edited.
The contract owner needs these exact changes:

1. In `contracts/CONTRACT.md`, change `ExtractedField.type` from
   `"money" | "date" | "string" | "account_number"` to
   `"money" | "date" | "string" | "account_number" | "number"`.
   `Handle.type` already references that union.
2. In `contracts/openapi.yaml`, append `number` at
   `/components/schemas/ExtractedField/properties/type/enum`.
3. Append `number` at
   `/components/schemas/Handle/properties/type/enum`.

The parity test explicitly allows only these two additive enum entries when
comparing generated backend schemas with read-only v1. Every other endpoint and
component must match exactly. A regression test proves that the current old
enum rejects `number`, the proposed extension accepts it, and existing fixtures
continue to validate. Once the contract owner publishes the extension, restore
direct parity and remove the temporary expected-spec adjustment.

## Verification results

```text
.venv/bin/pytest -q
245 passed, 2 deselected, 4 warnings in 20.01s

.venv/bin/pytest -q -m live
2 passed, 245 deselected, 2 warnings in 10.83s
```

Regression coverage includes observed 504-then-success behavior, exhausted
timeouts, 429 backoff/provider guidance, maximum two concurrent extractions,
schema/span failures, redaction, error-stage formatting, hold-on-failure,
numeric quantity fields/handles, and the explicit 15-second bank timeout.

## Fresh live standard-batch acceptance

- Batch: `batch-dc7bbd19e3fd4c1f83e504d5c3c4ca8b`
- Run: `run-9d1637fc81e04d1a8a9299c370ed3eaf`
- Mode/model: `live` / `gemini-3.5-flash-lite`
- Duration: approximately 35.4 seconds
- Final status: `completed`
- Tallies: four paid/submitted (270,000 cents), one held, zero blocked/errors

All invoices emitted `document.loaded` and `fields.extracted`. The rest of each
invoice's actual event sequence was:

```text
INV-001 policy.decision ALLOW -> payment.executed pending
INV-002 policy.decision ALLOW -> payment.executed pending
INV-003 policy.decision HOLD  -> payment.held BANK_DETAILS_MISMATCH
INV-004 policy.decision ALLOW -> payment.executed pending
INV-005 policy.decision ALLOW -> payment.executed pending
run.completed paid_count=4 held_count=1 blocked_count=0
```

Event counts were `run.started=1`, `lane.status=2`, `document.loaded=5`,
`fields.extracted=5`, `policy.decision=5`, `payment.executed=4`,
`payment.held=1`, and `run.completed=1` (24 events total, no `error`). All five
quantity fields had numeric JSON values and `type: number`.

The four bank results retain actual Nessie IDs and `pending` statuses. Paid
tallies count accepted submissions; this report does not claim settlement.

- [Full real SSE stream](b3_integration_fix_live.sse)
- [Machine-readable event/invoice/outcome summary](b3_integration_fix_summary.json)
