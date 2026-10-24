# Nessie integration findings (2026-10-23)

## Follow-up diagnosis and working payment path (2026-10-24 UTC)

The investigation started at **03:13:46 UTC**, with a 30-minute deadline of
**03:43:46 UTC**. The first updated live test passed at 03:22 UTC. All calls
used the configured official host, `https://api.nessieisreal.com`, with the key
loaded only through `Settings` and masked before logging.

The exact original request goes to **POST `/accounts/{payer_id}/transfers`**.
It does not go to `/bills`. Complete method, URL, headers, JSON body, HTTP
status, and response body for every diagnostic and live-test call are saved in:

- [Ordered diagnostic transcript](nessie_diagnostics.jsonl)
- [Live-test transcript](nessie_live_transcript.jsonl)
- [Readable request/response pairs](nessie_request_response_pairs.md)

### Requested attempts, in order

1. **Documented body plus pending status:** POST
   `/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/transfers?key=[REDACTED]`
   with `medium: balance`, `payee_id: c60e8616-4524-4005-af34-b28326d730bb`,
   `amount: 1.0`, `transaction_date: 2026-10-24`, `status: pending`, and the
   diagnostic description. **HTTP 400**, with exactly:

   ```text
   2 validation errors for TransferCreate
   medium
     extra fields not permitted (type=value_error.extra)
   payee_id
     extra fields not permitted (type=value_error.extra)
   ```

2. **Body requested by validation:** same POST and common fields, removing
   `medium` and `payee_id`. **HTTP 201**, `Transfer created`, real ID
   `307dab26-4f26-4ef5-9f95-5fe21ce65cd9`, status `pending`, amount `1`.
   The response contains no payer or payee. This is an unrouted diagnostic
   record, not a payment to the requested vendor.

3. **Existing object shape:** GET the same account's `/transfers` URL.
   **HTTP 200**, returning the diagnostic record with fields `id`,
   `transaction_date`, `status`, `amount`, and `description`. It uses `id`
   rather than `_id` and contains no recipient. It cannot establish routing.

### Fresh documentation and schema checks

The current [official docs](https://nessieisreal.com/docs) still load the
[published OpenAPI](https://nessieisreal.com/nessie-openapi-spec.yaml). It omits
account transfer creation/listing and `TransferCreate`. Its website transfer
example also omits the recipient. Official
[Go transfer source](https://github.com/nessieisreal/nessie-golang-sdk/blob/master/lib/transfer/transfer.go)
still documents `medium` and `payee_id`.

Authenticated GETs to the effective host's `/documentation`, `/openapi.json`,
and `/swagger.json` each returned **HTTP 403** with the JSON body
`{"message":"Missing Authentication Token"}`. This response is not evidence
that the valid account API key was missing from our request; the masked URLs
show that it was sent. No host Swagger revealed an alternate transfer body.

The published `BillCreate` requires `status`, `payee`, and `payment_amount`.
The observed transfer model instead accepts `status` and `amount`, so the
validation error alone does not establish that the bills route was called.

### Implemented real paired withdrawal and deposit

`NESSIE_PAYMENT_METHOD=paired_withdrawal_deposit` is now the explicit default.
`NESSIE_MODE` remains `live`; no offline fallback is involved. The adapter:

1. Verifies source funds and destination existence.
2. Atomically claims a persistent logical payment attempt.
3. POSTs a real withdrawal to `/accounts/{company_id}/withdrawals`.
4. Persists its returned ID before POSTing the payee deposit to
   `/accounts/{payee_id}/deposits`.
5. Returns `BankPayment`, a backend subtype of v1 `Payment`, with both real
   IDs and statuses in `detail`. The existing `transfer_id` is the actual
   withdrawal ID, not a generated synthetic transfer identifier.

Both POST bodies contain `medium: balance`, `amount: 1.0`, the UTC transaction
date, `status: pending`, and the payment description. Current official
`DepositCreate` requires all five fields. The real live-test results were:

```json
{
  "transfer_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
  "transfer_status": "pending",
  "amount_cents": 100,
  "bank_mode": "live",
  "detail": {
    "method": "paired_withdrawal_deposit",
    "atomic": false,
    "withdrawal_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "deposit_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "withdrawal_status": "pending",
    "deposit_status": "pending"
  }
}
```

Both creations returned **HTTP 201**. GET
`/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb` and GET
`/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb` returned **HTTP 200** with
the same IDs, amount `1`, and actual `pending` status. The current withdrawal
readback route is singular `/withdrawal/{id}`.

The separate manual diagnostic pair also returned real IDs: withdrawal
`94c14732-1b6b-4194-81ff-09d4951c564a` and deposit
`dc37c1bb-113c-4239-a669-a73ea68c6695`, both `pending`. Its full requests and
readbacks are retained in the diagnostic transcript.

**Observed balances did not move:** company 25,000,000 cents before and after;
vendor 0 cents before and after. The implementation does not label pending as
settled, change live balances locally, or claim immediate execution. Dollar
conversion remains the application's USD convention; the public schema does
not explicitly name currency units. The current deposit schema declares an
integer amount, so fractional-dollar paired payments are rejected before any
bank request until their support is verified. The tested amount is $1.00.

### Failure behavior and contract integration

The pair is **non-atomic**. Each leg and status is persisted. An ambiguous or
partial attempt requires reconciliation and cannot automatically issue a
second withdrawal/deposit. The identity includes session start, source,
destination, amount, and description; reuse a description for a retry of the
same logical payment, and use a distinct reference for a new payment. Repeats
of a successful request read both existing IDs back. Receipt totals count a
pair only after both real statuses indicate settlement.

The public read-only v1 contract has no `Payment.detail`. It remains unchanged;
backend adapter callers receive validated `BankPayment.detail`, and SQLite
retains it. Before emitting these details through the public SSE Payment schema,
the contract owner should add this exact optional field:

```typescript
detail?: {
  method: "paired_withdrawal_deposit";
  atomic: false;
  withdrawal_id: string;
  deposit_id: string;
  withdrawal_status: string;
  deposit_status: string;
}
```

### Final verification and timebox

Nessie work stopped at **03:30 UTC**, about 17 minutes after starting and before
the 03:43:46 UTC deadline. Final commands returned:

```text
.venv/bin/pytest -q
195 passed, 1 deselected, 3 warnings in 17.23s

.venv/bin/pytest -q -m live
1 passed, 195 deselected, 1 warning in 8.23s
```

The final live test also GETs `/accounts/{company_id}/withdrawals` and
`/accounts/{vendor_id}/deposits`, confirming the exact IDs occur under the
intended accounts. Repeated live tests reused the same IDs: the live transcript
contains just one withdrawal POST and one deposit POST, followed by readbacks.

## Historical B2 investigation

The following findings describe the original native-transfer investigation.
The paired payment path above supersedes the earlier live-test blocker.

## Sources checked before implementing

- Requested legacy documentation: <https://api.nessieisreal.com/documentation>.
  Both a browser fetch and an actual HTTP request returned **403 Forbidden**.
- Current official documentation: <https://nessieisreal.com/docs>.
  Its published application downloads the official specification from
  <https://nessieisreal.com/nessie-openapi-spec.yaml> (successfully fetched).
  That specification and the docs configure **https://prod-api.nessieisreal.com**
  as production; the backend default follows it. `NESSIE_BASE_URL` remains configurable.
- Official transfer SDK source:
  <https://github.com/nessieisreal/nessie-golang-sdk/blob/master/lib/transfer/transfer.go>.
  This supplements the current spec, which defines GET/PUT/DELETE
  `/transfers/{transfer_id}` but omits creation and per-account listing.
- The official Swift SDK independently confirms the same recipient field:
  <https://github.com/nessieisreal/nessie-ios-sdk/blob/master/Nessie-iOS-Wrapper/Transfer.swift>.

## Transfer request and units

The official SDK documents `POST /accounts/{payer_account_id}/transfers` with
JSON fields `medium`, `payee_id`, and numeric `amount`; `transaction_date`,
`status`, and `description` are optional. `GET /accounts/{account_id}/transfers`
lists transfers involving that account. Authentication uses the `key` query
parameter. The adapter redacts it from httpx logs and strips request/response
bodies and URLs from errors.

Observed on newly seeded live accounts: the transfer list endpoint returns
HTTP 404 with the JSON string `No transfers found for this account` instead
of an empty array. The adapter treats only this exact response as `[]`, after
verifying that GET `/accounts/{id}` succeeds. Other 404s remain errors.

Countersign submits:

```json
{
  "medium": "balance",
  "payee_id": "<destination Nessie account id>",
  "amount": 1.0,
  "transaction_date": "2026-10-23",
  "description": "Countersign B2 live $1.00 smoke test"
}
```

The application treats Nessie numeric money as **dollars**, converting integer
`amount_cents` to a decimal dollar number exactly once (100 cents -> 1.0), and
converts balances/returned amounts back to integer cents using `Decimal`.
The current official specification describes balance as an integer and the
website shows currency-like whole-number examples; it does **not explicitly
state the currency or unit**, so the dollar interpretation requires live
confirmation. Values with fractions smaller than a cent are rejected rather
than silently rounded. Local display account numbers are routing aliases in
SQLite, separate from Nessie's native account numbers and IDs.

## Execution, pending status, and balances

The official sources do **not** promise immediate execution, a particular
default status, a processing delay, or automatic account balance mutation.
The website's example transfer has `status: completed`; this example is not a
settlement guarantee. The adapter deliberately omits `status` from POST rather
than requesting or fabricating completion. It returns the actual Nessie ID and
status, and reads GET `/transfers/{id}` when POST omits status. No missing ID is
replaced by a generated one in live mode. An ambiguous create is reported as
an error and is never automatically retried.

### Production compatibility observed during B2 verification

Real customer/account seeding succeeded against
`https://prod-api.nessieisreal.com`. The live one-dollar transfer test then
received HTTP 400. Its validation response identified `TransferCreate`,
required `status`, and rejected both `medium` and `payee_id` as extra fields.
A diagnostic deliberately omitted the required `status` so it could not create
a transaction; all twenty candidate routing-field names were rejected. This
deployment therefore does not expose a usable destination field through this
endpoint. The adapter reports the incompatibility, retains the documented
routing payload, and never drops a destination to manufacture a successful
payment. No transfer ID or settlement result can be claimed from this failure.

The legacy HTTPS host `https://api.nessieisreal.com` was checked separately.
It returned the same seeded company customer UUID as the production host, and
the same `TransferCreate` rejection of `medium`/`payee_id` on another deliberately
invalid request. Switching between these two hostnames does not restore the
documented routed-transfer API. A compatible Nessie deployment or an upstream
API correction is required for a native routed transfer. The follow-up above
implements the explicitly requested paired real-operation alternative instead.

Authenticated read-only attempts at `/openapi.json`, `/api/openapi.json`, and
`/swagger.json` returned HTTP 403. The older official SDK hostname
`https://api.reimaginebanking.com` could not be reached through the adapter.
None of these checks revealed an alternative supported destination field.

A final read-only balance check succeeded for all seven live accounts. The
company balance converted to 25,000,000 cents in Countersign, matching the seed,
and all session transfer lists were empty. The deployed API returns HTTP 404
with JSON string `"No transfers found for this account"` for an empty history;
the adapter recognizes only this exact response and verifies that the account
exists before returning an empty list. Other 404 responses remain errors.

`get_balance` always returns the actual GET `/accounts/{id}` balance. A local
transfer never changes a reported live balance. GET `/api/balances` refreshes
transfer records and counts only statuses `completed`, `executed`, `success`,
or `succeeded` as received; pending/cancelled/rejected transfers are excluded.
Receipts include each actual transfer ID. This can legitimately differ from
the remote account balance if Nessie does not apply transactions to balances.

Nessie dates can have only day precision. Transfers submitted by this backend
retain their precise UTC submission time in SQLite, preserving it during
status refresh. Previously existing remote transfers retain their reported
date (UTC midnight); the backend does not claim they occurred after a reset
later that day. Reset retains bank history and advances `session_started_at`.

## Verification

`tests/test_bank.py` uses respx to verify payloads, dollars/cents conversion,
real-response ID/status propagation, status refresh, session reset boundaries,
pending/cancelled exclusion, sanitized errors, and explicit offline behavior.
`tests/test_seed_world.py` verifies idempotent customer/account creation.

`pytest -q -m live -s tests/test_nessie_live.py` seeds/reuses demo accounts,
submits 100 cents, reads the same ID back, and prints actual returned statuses
and source/destination balances before and after. It skips only when
`NESSIE_API_KEY` is absent; configured-key service errors fail. The root task's
verification report records its observed outcome. Until that test succeeds,
immediate execution, dollar denomination, and automatic balance updates remain
**unverified**, rather than claimed as working.

## Offline mode

Only `NESSIE_MODE=offline` selects the in-memory ledger. Customer, account,
balance and transfer results carry `mode: offline` (contract payments/balances
use `bank_mode: offline`). Offline IDs have an `offline-` prefix. Transfers
atomically debit/credit integer cents, reject insufficient funds, and retain
their records in SQLite so a restarted ledger can reconstruct balances once.
The directory resolves a display number to an account; only the independent
trusted vendor master determines `payee_on_file`.
