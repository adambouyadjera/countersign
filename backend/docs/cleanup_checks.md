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
