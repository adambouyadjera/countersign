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
