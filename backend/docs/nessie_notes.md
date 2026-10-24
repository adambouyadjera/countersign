# Nessie integration findings (2026-10-23)

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
API correction is required to pass the real transfer test; local mocks cannot
resolve this external incompatibility.

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
