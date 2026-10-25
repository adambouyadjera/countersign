# B9 cleanup verification

All checks use temporary databases and offline/fake services unless explicitly
identified as live Bitdeer, Anthropic Haiku, Nessie, or Elastic below. No live
Gemini request is permitted. Port 8000 is not started, stopped, restarted, or
used for these checks. Shared contracts remain read-only; additive proposals
are in `contract_change_requests.md`.

## Item 1 — all failures and reason ordering

Initial command: `PYTHONPATH=.:tests .venv/bin/pytest -q -s /tmp/test_cleanup_item1.py`.
With a verified attacker-account span and a duplicate, current code already
returned `BLOCK`, `[BANK_DETAILS_MISMATCH, DUPLICATE_INVOICE]`, all seven checks,
and all failed graph nodes (`PAYEE_ON_FILE`, `NOT_DUPLICATE`, `VENDOR_HISTORY`).
The first-failure-only behavior was not reproduced.

Adding an unverified account source reproduced the ordering defect:

```text
['UNVERIFIED_SOURCE', 'BANK_DETAILS_MISMATCH', 'DUPLICATE_INVOICE']
1 failed, 1 passed in 0.13s
```

Fixed deterministic, deduplicated reason priority without changing policy
checks or outcome selection. The payment reservation race paths now append a
duplicate reason rather than replacing any existing reasons.

Validation: `.venv/bin/pytest -q tests/test_policy.py tests/test_runs.py tests/test_semantic_duplicates.py`.

```text
61 passed, 1 warning in 2.67s
```

## Item 2 — fresh intake identity and immediate admission

Initial command: `PYTHONPATH=. .venv/bin/pytest -q -s /tmp/test_cleanup_item2.py --tb=short`.
It failed: three submissions all printed `INV-001`, exposed only the four old
batch keys, and their inbox records had no source. Admission latency was
0.087/0.047/0.052 seconds: model-induced admission delay was not reproduced.

Fresh numbers are now transactional, per-base, persistent across reset/restart,
and rolled back on PDF failure. The POST receipt names the admitted document and
inbox item, and intake provenance is server-assigned. The ordinary batch shape
is preserved. Gating the first model call allowed two more submissions in
0.052/0.047/0.046 seconds: all three were received/extracted exactly once.
Semantic duplicate controls remain enforced independently of invoice numbers.

Verification: `.venv/bin/pytest -q tests/test_attack_intake.py tests/test_custom_attacks.py tests/test_custom_attack_runs.py tests/test_autopilot.py tests/test_contract.py --tb=short`.

```text
140 passed, 3 warnings in 9.02s
```

## Item 3 — graphs, transcripts and old recordings

Checks: `PYTHONPATH=. .venv/bin/python /tmp/countersign_cleanup_checks.py` and
`PYTHONPATH=. .venv/bin/python /tmp/countersign_historic_route_checks.py`.
Fresh batch, attack and autopilot invoices had graph/transcript HTTP 200 with
the policy event's IDs; they remained HTTP 200 after a simulated restart.
An isolated read-only copy of the existing database also returned 200/200 for
the reported `run-autopilot-fd1b8993e5354f9fa77f11fedcc152e2` /
`invoice-b81e3999c87b4a9d8bf30bc6acf7ff97`. Current persistence failures were
not reproduced; existing SQLite persistence was retained.

The older `run-f94a0e4db0db47ea9c68153ad693ee7b` actually predates artifacts:
five policy decisions, no graphs or agent transcripts, and its unguarded lane
was `not_implemented`. Its generic 404 diagnostics reproduced. Missing legacy
records now return the requested explicit diagnostic; no historical graph,
transcript or model identity is invented. New tests cover all three intake
paths and compare full artifacts before/after restart.

`.venv/bin/pytest -q tests/test_graph_diagnostics.py tests/test_autopilot.py tests/test_graph.py --tb=short`
returned `33 passed, 2 warnings in 3.70s`.

## Item 4 — stale rejection

The isolated session-change check reproduced HTTP 409 for both `reject` and
`pay_vendor_on_file`, with `Approval context is unavailable or belongs to an
earlier session`; no transfer occurred. The unconditional session guard ran
before the money-free rejection branch.

Stale, verifiable requests now allow rejection after the existing SoD, role and
amount authorization. Stale payment decisions retain a clear 409; missing or
mismatched context remains blocked. Staleness is computed, not trusted from a
stored flag. Rejection is recorded in approval/audit history without creating
a monetary journal for a held bill that was never posted.

`.venv/bin/pytest -q tests/test_approvals.py tests/test_contract.py --tb=short`:
`115 passed, 2 warnings in 2.22s`.
`.venv/bin/pytest -q tests/test_approval_sod.py --tb=short`:
`31 passed, 1 warning in 1.72s`.
An initial command named nonexistent `tests/test_approval_roles.py` and exited
4 (`no tests ran`); the actual SoD module above was then located and run.

## Item 5 — reconciliation difference

Reproduced against a read-only copy of the running database
(`backend/data/countersign.db`) and the live `GET /api/reconciliation`:
difference -4,022,000 cents with 50 matched and 60 `bank_only` items.
Of the 60: 29 unguarded-lane and 1 Countersign-lane transfers were made in the
current session between 20:51 and 21:28 UTC, before the baseline was captured
at 22:01 (books started) and never booked; 30 were not submitted by this
database at all (other processes paying from the same Nessie company account).
All 50 matched items were unguarded-lane payments: the bookkeeper books both
lanes, so the difference was mixing the comparison lane into Countersign's
books.

The reconciler now compares Countersign's ledger with Countersign-lane
transfers only, reports the unguarded lane and pre-ledger transfers as counted,
labelled exclusions with every ID, and keeps unknown bank activity in the
difference. `tests/test_reconciliation.py` reproduces both causes on the
committed code (-604,000 vs -270,000 for a two-lane run; -333 for a pre-ledger
transfer) and passes after the change.

## Item 6 — hidden reference marker in UI spans

Reproduced with `tests/test_canary_marker_spans.py` on the committed code: an
extractor that flags the white 1-point `Reference: CS-CANARY-XXXX` line (as the
live extractor did) produced a UI-facing `fields.extracted` span
`'Reference:               '` after display masking.

The presentation layer (`display_model`, used by SSE events, decisions, graphs,
documents and transcripts) now drops marker-only spans before masking: from
`instruction_like_spans`, and as graph span nodes together with their edges and
evidence references; decisions (REST and `policy.decision` events) drop
evidence ids of those hidden nodes, computed from the stored extraction and
graph. A span is a marker only if its quote is a canary token,
optionally preceded by the `Reference:` label; any other text keeps it. Stored
extractions, stored events, raw graphs, audit records and leak-guard inputs are
unchanged, and the leak guard still checks the canary. No API shape changed.

## Item 7 — demo reset

`tests/test_demo_reset.py` on the committed code: after a busy two-lane session,
`POST /api/demo/reset` already emptied the approval queue, inbox and runs,
reconciled to 0 from a new baseline, re-seeded the world and January-June
archive, served the recorded bank-change evidence unchanged and did not touch
the extraction cache. It ignored the body: `{"autopilot": true}` left autopilot
off, and an invalid body was accepted.

Now the optional body sets autopilot (true: both lanes, fresh run; false or
absent: off) and is validated. The ledger is deliberately not cleared: journal
entries are append-only (SQLite `ledger_no_delete` trigger) and remain the audit
trail across resets; reconciliation is already scoped to the new session.

## Item 9 — Elastic on both lanes

Not reproduced as an indexing defect. Every appended event, from both lanes,
enters the audit outbox through the same projection (`map_event`), which takes
`payee_on_file` from the payment. A read-only ES|QL check of the live
`countersign-events` index found 2528 unguarded and 1742 Countersign events and
`payment.executed` rows for both lanes, all `payee_on_file: true`. A read-only
copy of the running database agreed: 47 Countersign and 79 unguarded payments,
none off file, and no undelivered outbox rows. "Zero results" was the correct
answer for the indexed live runs: the only off-file payment is in the saved
bank-change recording, which had never been indexed.

Added `scripts/backfill_recorded_audit.py`: it maps the recording's events with
the same projection (enriched from the recording via an in-memory store) and
indexes them with IDs `recorded:<run_id>:<seq>`, labelled
`evidence_source: "recorded_evidence"` and `recorded_source_file`. Backfilled
run-1b936c2ea67f4236b644609ac7c72089: 70 events, one unguarded off-file payment.
The two row tools return `evidence_source` and the agent instructions explain it
(re-provisioned with `scripts/setup_elastic_agent.py`). The real answer and its
ES|QL steps are saved in `docs/elastic_answers.json`.

## Item 10 — auditor timeout

Confirmed the delay is Elastic's: our flush is 0.29-0.57 s and our parsing about
1 ms per question, while the Agent Builder converse call took 12.7-83.8 s, and
in four of five measurements ended in HTTP 429 from Elastic, which the route
reported as a generic 503. There was no server-side limit (only the 180 s HTTP
client timeout). Added a 140 s limit returning 504 with a clear message, and a
429 `rate_limited` response for Elastic's 429. Shapes in `docs/elastic_notes.md`;
tests in `tests/test_audit_timeout.py`.

## Item 11 — demo history wording

Reproduced with `tests/test_vendor_archive_labels.py` on the committed code: the
check read "Paid 6 times since January 2026 to the account ending 1111, verified
by Morgan Diaz on 2026-03-12." with no archive label, and vendor rows had no
archive flag. Now the message reads "Paid 6 times since January 2026 (includes
seeded demo history) to the account ending 1111, ..." when archive payments or
the archive verification are cited, and vendor rows carry a computed `archive`
flag. Recorded evidence files keep their original wording.
