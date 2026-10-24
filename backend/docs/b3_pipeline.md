# B3: quarantined extraction and the H8 integration run

B3 provides a real background Countersign run over stored invoice PDFs. The
extractor has no tools. Code verifies source spans and payment values before
the temporary deterministic gate can call the configured bank adapter. No
planner or unguarded agent is called in this step.

## Setup and explicit service modes

Run from `backend/`:

```sh
source .venv/bin/activate
pip install -r requirements.txt
```

For the deterministic H8 integration flow:

```sh
EXTRACTOR_MODE=offline NESSIE_MODE=offline ELASTIC_MODE=offline \
  uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Offline extraction is selected only by `EXTRACTOR_MODE=offline`, and the bank
ledger is selected only by `NESSIE_MODE=offline`. Health reports these modes.
Extraction events carry the extractor's mode; payment events carry the bank's
actual mode and the payment's `bank_mode`. System/gate events identify an
offline pipeline when either extraction or banking is offline. There is no
network-error fallback from a live adapter to an offline implementation.

Live Gemini extraction uses `GEMINI_API_KEY` through `Settings`,
`EXTRACTOR_MODE=live`, and `EXTRACTOR_MODEL`. The example/default model is now
`gemini-3.5-flash-lite`, verified against the provider's model catalog and live
extraction calls. It verified all 13 fields of INV-002 in 2.5 seconds and passed
the live INV-003 canary test in 3.3 seconds. The ignored local `.env` contains
an explicit non-secret override selecting this model; no credentials changed.

Historically, the provider rejected `gemini-2.5-flash` for new users and
recommended `gemini-3.8-flash`. That model worked but also took 78 seconds on
the same invoice and reproduced HTTP 504/503/429 failures under integration
use. Selecting the verified low-latency model is an explicit configuration
change, not a runtime model or offline fallback.

The first live request returned HTTP 400 because this model API rejected the
SDK-generated `additionalProperties` schema setting. The adapter strips only
that unsupported setting from the outbound schema; local Pydantic validation
still forbids unexpected fields. Historically, an initial 3.8 call hit HTTP 504
at a 60-second deadline, and B3 then used a 120-second deadline. Those timing
settings are superseded by the integration fix below; the earlier observations
are retained as historical evidence, not current configuration.

Current Gemini requests have both an SDK timeout and an application deadline
of **45 seconds per attempt**. The adapter permits at most **three attempts**
(the initial call plus two retries) for HTTP 408, 429, 500, 502, 503, 504 and
retryable network/timeout failures. Backoff starts at 1 and 2 seconds and honors
provider retry guidance up to a 30-second cap. Extraction concurrency is limited
to **two** through the shared adapter semaphore. Schema and span-verification
errors are not retried as rate limits. There is no offline fallback.

If explicitly selected, Gemini 3.8 uses its documented `LOW` thinking level.
The configured Flash-Lite uses provider defaults. Both use a 4096-token output
limit; token-limited responses fail rather than returning partial fields.
The historical custom temperature setting was removed. Every Nessie request
has an explicit **15-second** deadline, and bank POSTs are never automatically
retried. See [integration failure diagnosis](b3_integration_fix.md) for the
reproduced raw error and the limits of the original run's evidence.

```sh
EXTRACTOR_MODE=live EXTRACTOR_MODEL=gemini-3.5-flash-lite \
  pytest -q -m live tests/test_gemini_live.py -s
```

Live banking keeps the existing B2 paired-withdrawal/deposit implementation.
Its returned status may be `pending`; B3 does not claim settlement or change
live bank balances locally. See [Nessie findings](nessie_notes.md).

## Extraction and source verification

`app/pipeline/document_text.py` is the canonical pdfplumber conversion. It
extracts the actual PDF into one-based pages and lines, retaining the original
line text for character offsets. The generated original PDF and extracted text
are stored together with their document identity.

Gemini receives only that numbered document and a quarantine system prompt:
the document is untrusted data, instructions in it must not be followed, and
the task is extraction only. Structured JSON covers invoice number, vendor
name/email, invoice date, due date, PO, USD total in cents, currency, printed
remit account, line items, and instruction-like spans. Every claimed scalar
has a quote and page/line coordinates. Tools and automatic function calling
are disabled.

For every claimed quote, deterministic code:

1. Searches the stated line for an exact or whitespace-normalized match.
2. Searches the rest of the document if that line does not contain it.
3. Returns offsets into the original line and the exact original substring on
   success, so `line.text[char_start:char_end] == quote`.
4. Marks an absent or fabricated quote `span_verified: false`.

A public `SourceSpan` addresses one line. Quotes that cross line boundaries
remain unverified; the live prompt requests one quote per instruction-bearing
line rather than inventing offsets across multiple lines.

An extractor's claim is not trusted merely because a quote exists. The run
gate rechecks critical source slices and confirms their values are supported
by those slices. A fabricated amount with a real but different amount quote
cannot reach the bank. Untrusted fields stay `trust: untrusted`; handles expose
only names, types, trust labels, and verification flags, without raw values.

The deterministic offline extractor parses the generator's known layout.
It does not read trusted vendor data, invoice summaries, bank records, or
private scoring flags. It preserves the printed remit account as data and
flags automated-routing directives instead of obeying them.

Public contract v1 has a generic `ExtractedField[]`, not a nested invoice
extraction object. The internal structured schema maps its scalar fields
directly to names such as `amount_due_cents`. Line items are flattened into
`line_items.0.description`, `line_items.0.quantity`,
`line_items.0.unit_price_cents`, and `line_items.0.amount_cents`, followed by
subsequent indexes. Quantity remains a numeric scalar with the contract's
corrected `number` classification, and its opaque handle also uses `number`.
The backend models support this requested addition. The read-only v1 contract
still needs `number` added to the `ExtractedField.type` and `Handle.type` enums;
the exact proposed edits are listed in [the integration fix report](b3_integration_fix.md).

Verified extraction payloads are persisted in SQLite's `extractions` table,
keyed by run and invoice, and emitted as `fields.extracted`. Source documents
remain available through `/api/documents/{id}` and `/pdf`.

## Thin run behavior and temporary limits

POST `/api/runs` saves the initial run and schedules an application-owned
background task. HTTP disconnects do not cancel that task. GET
`/api/runs/{id}` returns current state and final tallies; GET
`/api/runs/{id}/decisions` returns the persisted checks.

The Countersign lane emits `document.loaded`, `fields.extracted`, a temporary
`policy.decision`, and then either `payment.executed` or `payment.held` for
each invoice. Holds also persist an approval. Completion persists final
tallies and emits `run.completed`. The unguarded lane is reported as
`not_implemented`; requesting only unguarded still starts Countersign and
reports that limitation explicitly.

Processing failures also emit an `error` naming the failing stage and a safe
cause, followed by a persisted HOLD decision, approval, and `payment.held` with
`UNVERIFIED_SOURCE`. For example, a Gemini timeout is reported as
`extraction timed out after 45 s (gemini)`, rather than an uninformative generic
invoice error. Stages distinguish PDF text, Gemini, schema validation, source
verification, policy, and Nessie. Failed invoices count as held and the lane
remains failed, so completion preserves both review state and honest run status.
Extraction failures cannot reach the bank. A Nessie-stage failure instead needs
reconciliation of any already-submitted operation; creating a hold does not
assert that an ambiguous remote write was undone.

The gate in `app/pipeline/runner.py` is marked **TEMPORARY v0** for replacement
by B6. It requires verified, grounded invoice number, vendor name/email, due
date, PO, total, currency, and remit account. It resolves vendor identity from
the trusted name/email-domain master, binds the open PO to that vendor, and
compares the USD total and remit account with those trusted records. Verified
instructions requesting conflicting or automated payment routing require a
hold even when the ordinary printed remit block matches the master.
Hold review metadata shows an off-master account requested in a verified
instruction span alongside the trusted account on file. This display value
is never used as a payment destination.

This is not the full policy/planner implementation:

- Arbitrary custom trusted instructions are held with
  `POLICY_NOT_IMPLEMENTED` instead of silently ignored.
- Due dates outside the next 30 days are held for later policy support.
- Full duplicate-invoice handling, vendor-history checks, autonomy limits,
  provenance graphs, and planner/transcript behavior belong to later steps.
  The paired bank adapter's persistent attempt identity protects retries of
  the same logical payment; it does not replace business duplicate detection.
- No planner LLM, unguarded agent, or Elastic indexing is implemented by B3.
  Events are durably saved in SQLite and delivered through the existing bus.
- Background execution is in-process. Shutdown records failed/completed state
  with honest partial tallies; it does not automatically resume work after a
  process restart. Ambiguous bank operations need reconciliation before retry.

For live paired payments, public v1 `Payment` still lacks `detail`. Both real
operation IDs remain in bank persistence and the backend adapter result.
`payment.executed` projects only the existing v1 fields so it remains valid
against the read-only public contract. `paid_count`/`paid_cents` count accepted
payment submissions and preserve their real status, including `pending`;
these tallies are not a claim of bank settlement.

## H8 curl integration

With the explicitly offline server above running, execute:

```sh
curl --fail --silent --show-error -X POST http://127.0.0.1:8000/api/batches \
  -H 'Content-Type: application/json' -d '{"kind":"standard"}' \
  -o /tmp/countersign-b3-batch.json

batch_id="$(python -c 'import json; print(json.load(open("/tmp/countersign-b3-batch.json"))["batch_id"])')"

curl --fail --silent --show-error -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d "{\"batch_id\":\"$batch_id\",\"lanes\":[\"countersign\",\"unguarded\"]}" \
  -o /tmp/countersign-b3-run.json

run_id="$(python -c 'import json; print(json.load(open("/tmp/countersign-b3-run.json"))["run_id"])')"

curl --silent --show-error --no-buffer --max-time 3 \
  "http://127.0.0.1:8000/api/runs/$run_id/events"

curl --fail --silent --show-error "http://127.0.0.1:8000/api/runs/$run_id"
```

The contract deliberately keeps SSE open after `run.completed` so later human
approval events can arrive. With the bounded curl above, timeout exit code
**28 is expected after successful completion**. It is not a server error.
For a one-run UI, stop reading when the desired completion frame arrives;
longer-lived clients may keep the connection and resume using `Last-Event-ID`.

## Captured HTTP evidence

An actual isolated offline server on port 8000 produced batch
`batch-4e21d167421948f08b057231d9ac42d0` and run
`run-34378044b8814fa58c5b25e1fdd32aa5` on 2026-10-24 UTC. The curl capture
contains **25 valid RunEvents**, ending in the following completion frame:

```text
id: 25
event: run.completed
data: {"run_id":"run-34378044b8814fa58c5b25e1fdd32aa5","seq":25,"ts":"2026-10-24T04:47:25.721786Z","mode":"offline","type":"run.completed","lane":"system","payload":{"tallies":{"countersign":{"paid_count":4,"paid_cents":270000,"held_count":1,"blocked_count":0,"not_on_file_count":0,"not_on_file_cents":0}}}}
```

The preceding captured hold was real gate output:

```text
id: 15
event: payment.held
data: {"run_id":"run-34378044b8814fa58c5b25e1fdd32aa5","seq":15,"ts":"2026-10-24T04:47:25.719709Z","mode":"offline","type":"payment.held","lane":"countersign","payload":{"invoice_id":"invoice-bd0daafd9b0e48b6a9a42723a685b32b","approval_id":"approval-8e3f9a8ce234408fa6350273425d3d83","reason_codes":["BANK_DETAILS_MISMATCH"]}}
```

Four actual offline-ledger transfers were completed, totaling 270,000 cents;
the routing-instruction invoice was held and the unguarded lane was explicitly
not implemented. This capture is labeled offline throughout and makes no claim
that those IDs came from live Nessie.

- [Full curl stream](b3_curl_stream.sse)
- [Trimmed payment/hold/completion output](b3_curl_excerpt.sse)
- [Exact commands used for the captured run](b3_curl_commands.txt)

Run `pytest -q` for deterministic unit/integration checks and
`pytest -q -m live` for configured real-service checks. Missing credentials may
skip their corresponding live test; a configured but failing service fails
explicitly. The final task report records the commands actually executed and
their real summary output.

The following results are **historical B3 results before the integration fix**.
The current fix's verification is tracked in [the integration fix report](b3_integration_fix.md).
The earlier configured live command succeeded without a command-line model override:

```text
.venv/bin/pytest -q
226 passed, 2 deselected, 4 warnings in 18.76s

.venv/bin/pytest -q -m live
2 passed, 226 deselected, 2 warnings in 118.76s (0:01:58)
```
