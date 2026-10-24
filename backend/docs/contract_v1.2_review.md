# Contract v1.2 review: every removed line

Baseline: `main` at `cfd661e536d565eb9bba968be06e8c2cae469b30`. This report covers every deleted content line in `git diff main -- contracts/`, excluding diff file headers and the `No newline at end of file` marker (neither is a source line).

There are **14 deleted content lines**: 11 in `CONTRACT.md` and three in `openapi.yaml`. The generated `bank_change_replay.json` fixture is entirely added and has no removed line. Every line is mapped below to its exact replacement with old and current line numbers.

The API schemas are a semantic superset. The explicit exception is the current release-version metadata: `1.0.0` becomes `1.2.0`, rather than pretending a single string can retain two values. The original 1.0.0 changelog text remains identical.

## Compatibility checks

A recursive comparison against the baseline checked all **106 existing component schemas** (118 now): no component, field, required-field membership, type, reference, constraint, or enum member was removed, and no new narrowing constraint or required field was added to an existing schema. Exactly four accepted-value expansions exist:

- `Batch.kind`: `standard | attack` → `standard | attack | bank_change`.
- `BatchRequest.kind`: singleton `standard` → `standard | bank_change`.
- `ExtractedField.type`: `money | date | string | account_number` → those same members plus `number`.
- `Handle.type`: the same expansion as `ExtractedField.type`.

All **30 original paths / 32 method operations** remain, with identical operation IDs, response schemas, request-body schemas, and parameters. There are now 32 paths / 34 operations. Existing enum members and required fields are preserved recursively; the v1.2 provider/cache/replay extensions are additive.

The exact original lines `Five invoices, one poisoned (which one is never exposed)`, `is not identified in the response.`, and `IDs in fixtures start with example-.` are retained, not deletion candidates. Added text expressly applies poison anonymity to both standard and attack batches. Only the bank-change evidence replay names the letter in a recording of a finished run. Recorded-live fixtures have the explicit real-run/transfer-ID exception; the original synthetic fixtures are unchanged.

## Every removed line and its replacement

### 1. `contracts/CONTRACT.md:21` → `contracts/CONTRACT.md:24`

Removed line (exact):

```text
{kind: "standard"}
```

Replacement line (exact):

```text
{kind: "standard" | "bank_change"}
```

Superset: the old `standard` value remains accepted; `bank_change` is added. No old request field is removed.

### 2. `contracts/CONTRACT.md:97` → `contracts/CONTRACT.md:111`

Removed line (exact):

```text
interface Health { services: { nessie: ServiceHealth; elastic: ServiceHealth; anthropic: ServiceHealth; gemini: ServiceHealth; bitdeer: ServiceHealth }; session_started_at: string }
```

Replacement line (exact):

```text
interface Health { services: { nessie: ServiceHealth; elastic: ServiceHealth; anthropic: ServiceHealth; gemini: ServiceHealth; bitdeer: ServiceHealth; mutator?: ServiceHealth }; session_started_at: string }
```

Superset: all five existing services and `session_started_at` remain. Only optional `mutator?: ServiceHealth` is added; no existing field becomes optional or required differently.

### 3. `contracts/CONTRACT.md:100` → `contracts/CONTRACT.md:116`

Removed line (exact):

```text
interface Batch { batch_id: string; kind: "standard" | "attack"; created_at: string; invoices: InvoiceSummary[] }
```

Replacement line (exact):

```text
interface Batch { batch_id: string; kind: "standard" | "attack" | "bank_change"; created_at: string; invoices: InvoiceSummary[]; supporting_documents?: SupportingDocument[] }
```

Superset: `standard` and `attack`, every original field, and the original required fields remain. `bank_change` and optional `supporting_documents` are added. The separately added `BatchRequest` and `SupportingDocument` types do not replace or rename `Batch`.

### 4. `contracts/CONTRACT.md:102` → `contracts/CONTRACT.md:118`

Removed line (exact):

```text
interface DocumentText { document_id: string; invoice_id: string; pages: { page: number; lines: { line: number; text: string }[] }[] }
```

Replacement line (exact):

```text
interface DocumentText { document_id: string; invoice_id: string; pages: { page: number; lines: { line: number; text: string }[] }[]; document_type?: "invoice" | "vendor_bank_change_letter" }
```

Superset: `document_id`, `invoice_id`, and every nested page/line field remain unchanged. Optional `document_type` is added; old documents may omit it.

### 5. `contracts/CONTRACT.md:106` → `contracts/CONTRACT.md:122`

Removed line (exact):

```text
interface RunRequest { batch_id: string; lanes: Lane[]; instruction?: string }
```

Replacement line (exact):

```text
interface RunRequest { batch_id: string; lanes: Lane[]; instruction?: string; unguarded_mode?: UnguardedMode }
```

Superset: `batch_id`, `lanes`, and optional `instruction` remain unchanged. Optional `unguarded_mode` is added; omission still selects the original AP workflow.

### 6. `contracts/CONTRACT.md:108` → `contracts/CONTRACT.md:124`

Removed line (exact):

```text
interface Run { run_id: string; batch_id: string; instruction: string; created_at: string; lanes: { lane: Lane; status: "running" | "not_implemented" | "failed" | "completed" }[]; tallies?: Partial<Record<Lane, LaneTally>> }
```

Replacement line (exact):

```text
interface Run { run_id: string; batch_id: string; instruction: string; created_at: string; lanes: { lane: Lane; status: "running" | "not_implemented" | "failed" | "completed" }[]; tallies?: Partial<Record<Lane, LaneTally>>; unguarded_mode?: UnguardedMode }
```

Superset: every original field, lane-status member (`running`, `not_implemented`, `failed`, `completed`), and optional tally field remains. Only optional `unguarded_mode` is added.

### 7. `contracts/CONTRACT.md:111` → `contracts/CONTRACT.md:127`

Removed line (exact):

```text
interface ExtractedField { name: string; value: string | number | null; type: "money" | "date" | "string" | "account_number"; span: SourceSpan | null; trust: "untrusted" }
```

Replacement line (exact):

```text
interface ExtractedField { name: string; value: string | number | null; type: "money" | "date" | "string" | "account_number" | "number"; span: SourceSpan | null; trust: "untrusted" }
```

Superset: every original field, scalar value type, null allowance, trust value, and the four existing type members (`money`, `date`, `string`, `account_number`) remain. `number` is added to the field-type enum; `Handle.type` references the same expanded type.

### 8. `contracts/CONTRACT.md:129` → `contracts/CONTRACT.md:145`

Removed line (exact):

```text
interface AgentTranscript { run_id: string; invoice_id: string; model: string; steps: { kind: "message" | "tool_call"; text?: string; tool?: string; args?: Record<string, unknown>; result?: string }[] }
```

Replacement line (exact):

```text
interface AgentTranscript { run_id: string; invoice_id: string; model: string; provider?: string; unguarded_mode?: UnguardedMode; steps: { kind: "message" | "tool_call"; text?: string; tool?: string; args?: Record<string, unknown>; result?: string }[] }
```

Superset: `run_id`, `invoice_id`, required `model`, both step-kind members, and all existing optional step fields remain. Optional `provider` and `unguarded_mode` are added.

### 9. `contracts/CONTRACT.md:142` → `contracts/CONTRACT.md:158`

Removed line (exact):

```text
Every SSE message's data is a RunEvent: {run_id, seq, ts, type, lane: Lane | "system", mode?: Mode, payload}. seq increases by one per run across both lanes.
```

Replacement line (exact):

```text
Every SSE message's data is a RunEvent: {run_id, seq, ts, type, lane: Lane | "system", mode?: Mode, provider?: string, model?: string, unguarded_mode?: UnguardedMode, payload}. seq increases by one per run across both lanes.
```

Superset: all original event fields, lane members, optional mode, payload, and per-run sequence rule remain. Only optional `provider`, `model`, and `unguarded_mode` metadata are added.

### 10. `contracts/CONTRACT.md:157` → `contracts/CONTRACT.md:173`

Removed line (exact):

```text
{invoice_id, extractor_model, fields: ExtractedField[], instruction_like_spans: SourceSpan[], handles: Handle[]}
```

Replacement line (exact):

```text
{invoice_id, extractor_model, fields: ExtractedField[], instruction_like_spans: SourceSpan[], handles: Handle[], extraction_provenance?: ExtractionProvenance}
```

Superset: `invoice_id`, `extractor_model`, `fields`, `instruction_like_spans`, and `handles` remain. Only optional `extraction_provenance` is added.

### 11. `contracts/CONTRACT.md:262` → `contracts/CONTRACT.md:279`

Removed line (exact):

```text
• 1.0.0: initial contract, including the Hyphenate track additions (Fogline Festival, ledger, roles, segregation of duties, inbox and autopilot, planted-errors eval).
```

Replacement line (exact):

```text
• 1.0.0: initial contract, including the Hyphenate track additions (Fogline Festival, ledger, roles, segregation of duties, inbox and autopilot, planted-errors eval).
```

Identical content retained: this is not a semantic removal. Git reports the old final line as removed/readded because the original file ended without a newline and new changelog/documentation content follows. The 1.0.0 changelog text is unchanged; a 1.2.0 entry is added above it.

### 12. `contracts/openapi.yaml:4` → `contracts/openapi.yaml:4`

Removed line (exact):

```text
  version: 1.0.0
```

Replacement line (exact):

```text
  version: 1.2.0
```

Release-metadata exception: the current release label intentionally changes from 1.0.0 to 1.2.0; a single version string is not a set-valued schema and cannot honestly be called a value superset. The 1.0.0 version remains documented in the unchanged changelog. The API data schemas and existing endpoint names remain compatible.

### 13. `contracts/openapi.yaml:176` → `contracts/openapi.yaml:181`

Removed line (exact):

```text
        limit: 10 requests per minute.'
```

Replacement lines (exact):

```text
        limit: 10 requests per minute.
        The poisoned invoice is not identified in the response.
        Standard and attack batches never reveal which invoice is poisoned.'
```

Prose superset: the original 10-per-minute rate limit remains verbatim. The YAML closing quote moves after two added sentences explicitly preserving poison anonymity for both standard and attack batches. The decoded description retains all old words; no endpoint, input field, value, or rate limit changes.

### 14. `contracts/openapi.yaml:1447` → `contracts/openapi.yaml:1561`

Removed line (exact):

```text
          const: standard
```

Replacement lines (exact):

```text
          enum:
          - standard
          - bank_change
```

Schema superset: this is `BatchRequest.properties.kind`. The former singleton value `standard` remains in the enum; `bank_change` is added. The field name, string type, and required status remain unchanged.

## Reproduce the review

Run the source diff from the repository root:

```sh
git diff main --unified=0 -- contracts/
```

Run the offline contract/schema/fixture validation from `backend/`:

```sh
.venv/bin/pytest -q tests/test_contract.py tests/test_evidence.py
```

The audit compares against the exact baseline commit above. File line numbers refer to the reviewed v1.2 contract, not generated-diff offsets.
