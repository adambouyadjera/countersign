# Demo reset duplicate-register fix

Offline reproduction before the fix:

```text
.venv/bin/pytest -q -s tests/test_demo_reset_duplicates.py --tb=short
{'after_reset': {'paid': 0, 'held': 0, 'blocked': 5}}
1 failed, 1 warning in 1.20s
```

Reset already deleted `session_payment_claims` and advanced the session clock,
but semantic duplicate detection scanned every retained transfer without that
boundary. Exact-number checks already used the session. Both paths now use one
predicate: the recorded session must match; legacy transfers use their actual
submission timestamp or retained server-owned run binding. An explicit old
session cannot be overridden by a newer provider-refresh timestamp.

The transfer register, bank idempotency records, audit data and append-only
journal are retained. Reset still clears pending session reservations. The
January-June vendor archive is untouched; planted-errors prior-paid fixtures
remain current-session claims in their isolated evaluation stores. No policy
matching rule, reason code or API changes, and no shared contracts changes.

The regression first pays all five original invoices (including a controller's
on-file resolution), verifies that replay without reset blocks all five, then
resets and draws a new standard batch. It verifies four payments and one
`BANK_DETAILS_MISMATCH` hold in pending approvals, zero duplicate blocks, and
unchanged original journal/transfer/archive records. Replaying this new batch
without reset pays nothing: the four paid invoices block as duplicates; the
unpaid poisoned invoice remains held.

Focused verification (all offline):

```text
.venv/bin/pytest -q tests/test_demo_reset_duplicates.py tests/test_semantic_claims.py tests/test_semantic_duplicates.py tests/test_payments.py tests/test_demo_reset.py tests/test_eval_jobs.py --tb=short
68 passed, 1 warning in 4.90s
```

Full offline suite:

```text
.venv/bin/pytest -q
1265 passed, 11 deselected, 4 warnings in 47.23s
```

No Gemini/live service calls, no operation on port 8000, and no API or contract
changes. Journal contents, archived history, transfer evidence and bank-attempt
idempotency records are retained.
