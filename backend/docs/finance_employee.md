# Countersign finance employee

The deterministic payment path now maintains double-entry books and vendor
history, and separates requesters, bank-detail verifiers, and payment approvers.
The shared Hyphenate API shapes are unchanged.

## Books and posting rules

The chart contains `1000 Cash - Operating`, `2000 Accounts Payable`,
`5100 Production Costs`, `5200 Security and Staffing`, `5300 Artist Fees`, and
`5400 Facilities`. Rentals/sound use production costs, security uses staffing,
artists use artist fees, and sanitation uses facilities.

- Accepting a bill posts Dr expense / Cr AP, once per opaque invoice ID.
- A validated bank submission posts Dr AP / Cr Cash, once per actual transfer ID.
- A held, blocked, or rejected payment posts nothing by itself.
- Approving a previously held bill creates its bill/payment entries attributed
  to the approving person's ID. The payment entry references the approval ID
  and separately retains the actual bank transfer ID for reconciliation.
- Every actual payment, including unguarded-lane payments, reaches the books.
  Both lanes moving money for the same invoice can therefore expose an actual
  AP overpayment; bookkeeping does not pretend the comparison lanes are separate
  companies or silently omit the unguarded bank movements.

Each entry records its date, memo, source, and creator. Python validation and
SQLite triggers reject unbalanced, negative, malformed, or unknown-account
lines. SQL blocks updates, deletes, and replacement overwrites. Corrections use
`EventBus.reverse_entry()` to add an explicitly linked reversing entry and its
`ledger.posted` event atomically in the original run (using `Ledger.reverse()`
internally). Repeated corrections reuse the entry/event. Journal records
survive demo resets and application restarts.

Accepted bills are booked before the bank call. Bank failure does not invent a
cash entry; an already accepted bill can remain payable. Journal entries,
vendor payment history, and `ledger.posted` events commit together. Failure to
persist the accepted bill prevents the transfer and rolls back its invoice
reservation. Failure after an external bank submission retains the actual bank
record and exposes the missing journal as `bank_only`.

Cash starts at zero and represents booked movement until a legitimate opening
journal is supplied. No opening equity, funding, or historical bank transfers
were invented to make balances look funded. Trial balance reports all retained
journals; reconciliation reports the current demo session's cash changes.

## Reconciliation basis

Before the first payment, an independent bank read captures the company account
and all pre-existing register IDs. The snapshot persists across workers and
restarts. Date-only Nessie records cannot distinguish two sessions on the same
day, so ID baselines are used rather than assigning invented timestamps.

The bank adapter reads native transfers, withdrawals, and deposits directly,
including items not submitted by this app. Paired Nessie payments use the real
company withdrawal ID as their transfer ID; the vendor deposit is not a second
company movement. Matching requires both that ID and the signed cent amount.
Missing/mismatched records appear as `bank_only` and/or `ledger_only`, with notes.
Unavailable collections and unknown new routing directions fail explicitly.

The current response uses **submitted-payment register basis, including pending
items**. Its existing `bank_balance_change_cents` field is the signed register
movement, not a claim that pending payments have settled. Every bank item notes
its real status and this basis. `difference_cents` is that register movement
minus booked cash movement. A zero difference means every register movement is
accounted for; it does not establish settlement. Live evidence separately saves
`Reconciler.balance_observation()`, with the actual observed bank balances and
their change. An additive contract request below proposes explicit basis and
observed-balance fields for the frontend.

## Vendor history and independent verification

The opening January–June history is explicitly seeded demo archive data, with
`example-history-*` archived journal references. Those are not claimed Nessie
transactions or current ledger entries. The archive records six monthly
payments per vendor and its original verifier/date. New actual payment postings
append their real journal IDs without rewriting the archive.

`VENDOR_HISTORY` compares the requested account with prior payment accounts and
cites the count, earliest month, account ending, verifier, and verification date.
A changed account fails with `BANK_DETAILS_MISMATCH`. A new on-file account
independently verified through the vendor-admin endpoint retains its audit and
can pass while its previous payment history is still shown. History does not
replace span verification, the current vendor master, PO matching, or duplicate
checks.

`POST /api/vendors/{vendor_id}/bank_change` requires a vendor_admin, a uniquely
resolved directory account, and a nonempty verification note. It appends account
history and an active verification audit, closes the previous interval, and
updates both trusted master account fields atomically. Batch regeneration does
not overwrite that verified master change.

## Roles, limits, and countersigns

- `person-ap-agent`: configurable autonomy, default 2,500,000 cents ($25,000).
- `person-ap-manager`: at most 5,000,000 cents ($50,000).
- `person-controller`: unlimited authority by its seeded null limit.
- `person-vendor-admin`: bank-detail verification, no payment approval authority.

Set `AGENT_AUTONOMY_LIMIT_CENTS` to change the agent limit. An otherwise valid
payment above it holds with `OVER_AUTONOMY_LIMIT`; payments above the manager
ceiling require a controller. The AP-manager role ceiling cannot be enlarged by
an oversized or null individual limit.

Approval bodies require `actor_id`. Unknown, insufficient-role, over-limit,
requester/self-approval, and verifier/payment conflicts return `403` with
`sod_violation` and the names of eligible independent approvers. Omitting the
required field remains contract `422`. Wrong account last-four confirmation is
also `422`; repeated successful resolution remains `409`.

`approve_new_account` requires a prior vendor-admin master update and a different
payment approver. The same verifier cannot bypass this by later choosing
`pay_vendor_on_file`, even after a role change. After slow preflight awaits,
payment execution rechecks the current actor, requester, amount, limit, master,
and active verifier inside the final bill/reservation transaction. Known
pre-bank denials preserve their 403/409 and permit another eligible approver;
uncertain bank attempts stay locked for reconciliation.

Actor IDs implement the contract's demo role model. Production caller
authentication and binding the authenticated identity to actor_id remain a
deployment integration; selecting a demo identity is not authentication.

## API and verification

```text
GET /api/people
GET /api/vendors/{vendor_id}
POST /api/vendors/{vendor_id}/bank_change
GET /api/ledger/entries?run_id=&invoice_id=
GET /api/ledger/trial_balance
GET /api/reconciliation
```

From backend:

```sh
.venv/bin/pytest -q
.venv/bin/pytest -q -m live tests/test_finance_live.py -s
```

The live check uses real extraction, planning, and Nessie payments in the
Countersign lane, preserves every trial as `docs/finance_live_run-*.json`, and
requires four real on-file submissions, one bank-details hold, eight journal
entries/events, balanced books, and a zero register reconciliation difference.
Its proof includes actual settled-balance observations and original statuses.

## Recorded live result

Run `run-729325d8a1304ead952b510c6c3cefc0` passed on 2026-10-24:
[complete evidence](finance_live_run-729325d8a1304ead952b510c6c3cefc0.json),
[exact trial-balance and reconciliation responses](finance_live_outputs.json).

Four real Nessie submissions totalled 270,000 cents, all on file. Golden Gate
was held with `BANK_DETAILS_MISMATCH`; it had no journal posting. Eight journal
entries emitted eight `ledger.posted` events. Total debits and credits were
both 540,000 cents. The six account balances were Cash -270,000, AP 0,
Production 210,000, Security/Staffing 42,000, Artist Fees 18,000, Facilities 0.

All four independent bank register IDs matched their exact cash journal IDs
and amounts. Register change and booked cash change were both -270,000 cents;
the difference was zero. Every bank status was still `pending`. The observed
settled bank balance remained 25,000,000 cents before and after, with observed
change zero; this result does not claim settlement. All five accepted
extractions in this run came from Anthropic/Claude Haiku through the configured
fallback chain. Planner leak matches were zero.
