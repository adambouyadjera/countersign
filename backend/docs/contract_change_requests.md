# Additive contract changes published in v1.2

These extensions are applied to CONTRACT.md and openapi.yaml in v1.2 under the
user's one-time permission to edit `contracts/`. The proposal is retained here
for the implementation history. Existing v1 fixtures remain valid. The replay
endpoints and their generated real-evidence fixture are also part of v1.2.

```typescript
type UnguardedMode = "ap" | "ap_with_vendor_updates";

interface BatchRequest { kind: "standard" | "bank_change" }
interface SupportingDocument {
  document_id: string;
  invoice_id: string; // associated invoice, not another payable invoice
  vendor_id: string; // server-owned association, not document authority
  document_type: "vendor_bank_change_letter";
}
// Amend existing interfaces:
interface Batch {
  kind: "standard" | "attack" | "bank_change";
  supporting_documents?: SupportingDocument[];
}
interface DocumentText {
  document_type?: "invoice" | "vendor_bank_change_letter";
}
interface RunRequest { unguarded_mode?: UnguardedMode }
interface Run { unguarded_mode?: UnguardedMode }
interface AgentTranscript {
  provider?: string;
  unguarded_mode?: UnguardedMode;
}
interface RunEvent {
  provider?: string;
  model?: string;
  unguarded_mode?: UnguardedMode;
}
interface ServicesHealth { mutator?: ServiceHealth }
```

Omitting `RunRequest.unguarded_mode` selects `ap`. New runs return their effective
mode. All newly produced unguarded events and agent transcripts include provider,
model, and effective unguarded mode; the fields remain optional in the common
schema for legacy transcripts and other lanes. Existing `AgentTranscript.model`
remains required. Old transcripts with unknown provider are labelled
`legacy_unknown`, never relabelled using current configuration.

`POST /api/batches {"kind":"bank_change"}` creates five clean invoices and one
supporting letter, rather than adding another payable invoice. Document text is
untrusted regardless of its type or linked vendor. Both lanes receive the same
letter; only the separate unguarded vendor-update mode gets the update tool.

The optional mutator health entry reports its independently configured live or
explicit offline mode using read-only catalog probes. The legacy `bitdeer`
health key continues reporting the unguarded adapter, with actual provider/model
in its detail, for v1 compatibility.

Retain the previously requested `"number"` extension to both
`ExtractedField.type` and `Handle.type` for numeric extracted quantities.

## Extraction provider and cache provenance

Add optional provenance to `FieldsExtractedPayload`, which is already the
payload of `fields.extracted`. The existing `extractor_model` field remains the
actual model that produced the accepted result and must match the new metadata.

```typescript
interface ExtractionAttempt {
  provider: string;
  model: string;
  outcome: "success" | "failed" | "skipped";
  http_status?: number; // integer HTTP status, 100..599
  error_code?: string;  // safe classification, never the provider response body
}
interface ExtractionProvenance {
  provider: string;
  model: string;
  extracted_at: string; // original UTC extraction timestamp, including on a cache hit
  cached: boolean;
  document_sha256: string; // 64 lowercase hexadecimal characters; original document bytes
  prompt_version: string;
  cache_key?: string;
  attempts?: ExtractionAttempt[]; // original extraction/fallback attempts, not fresh calls on cache reuse
}
interface FieldsExtractedPayload {
  extraction_provenance?: ExtractionProvenance;
}
```

Legacy extractions omit this metadata; the backend does not infer a historical
provider or timestamp from current configuration. A cache hit retains the
original producer and timestamp with `cached: true`. Returning cached fields
never makes their document sources trusted or skips deterministic span checks.

The existing graph schema can represent the metadata without a new node kind:
an extraction operation uses `kind: "plan_step"`, `trust: "none"`, and
`detail.operation: "quarantined_extraction"`, with an explicit `(cached)` or
`(fresh)` label. Its detail includes the provenance above. Untrusted causal edges
link the operation to extracted fields; field and handle detail also retain that
origin. This operation is distinct from the planner's DSL program steps and does
not authorize a payment.
