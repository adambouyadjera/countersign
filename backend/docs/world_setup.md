# Countersign AP world

Run these commands from `backend/` with dependencies installed:

```sh
source .venv/bin/activate
pip install -r requirements.txt
```

The trusted SQLite records contain Fogline Festival, a three-day San Francisco
music festival, and five vendors for stage rigging, sound production, sanitation,
security staffing, and an artist agency. Each vendor has a trusted email domain,
remit number, and open purchase order. Invoice text cannot update this master.

The bank directory maps display account numbers to bank account IDs. It includes
Northwind Remittance Services so an unguarded agent can reach the attack account;
directory membership does not make that account an approved vendor payee.

## Live Nessie

Configure `NESSIE_API_KEY` through `backend/.env`. Credentials are loaded through
`Settings`; do not paste them into commands or logs. Current official
[Nessie documentation](https://nessieisreal.com/docs) uses
`https://prod-api.nessieisreal.com`. Override an older `.env` base URL explicitly:

```sh
NESSIE_MODE=live NESSIE_BASE_URL=https://prod-api.nessieisreal.com python scripts/seed_world.py
NESSIE_MODE=live NESSIE_BASE_URL=https://prod-api.nessieisreal.com uvicorn app.main:app --reload --port 8000
```

The script prints customer/account IDs for the company, five vendors, and
Northwind Remittance Services. It creates checking accounts with an initial
company balance of USD 250,000; the other accounts start at zero. It reuses saved
IDs on reruns and never tops up an existing account. Customer IDs are saved before
creating accounts, and a later retry can discover a previously created customer
or account after a lost response.

Use the same API key and base URL with a seeded database. Run one seeder at a time
for a Nessie world; its customer/account API has no documented atomic idempotency
key. Ambiguous matching names or account nicknames cause the seed to stop. If
remote records are manually deleted, reconcile the saved IDs before seeding again.

See [nessie_notes.md](nessie_notes.md) for request fields, dollar conversion,
settlement behavior, and live verification findings.

Live payments explicitly default to
`NESSIE_PAYMENT_METHOD=paired_withdrawal_deposit`, because the deployed native
transfer schema rejects recipient routing. The adapter creates one real Nessie
withdrawal and one real deposit, returning both IDs and statuses in backend
`BankPayment.detail`. The pair is non-atomic and currently supports whole-dollar
amounts. Partial or ambiguous attempts stop for reconciliation; an identical
successful request reads back its existing IDs instead of submitting again.
Use a unique invoice/payment description for a distinct payment. Native SDK
requests remain available via `NESSIE_PAYMENT_METHOD=native_transfer` for a
compatible service; there is no automatic retry using another method.

## Explicit offline mode

```sh
NESSIE_MODE=offline python scripts/seed_world.py
NESSIE_MODE=offline uvicorn app.main:app --reload --port 8000
```

Offline startup seeds the world automatically. Health, balances, payments, and
internal transfer results identify offline mode. The ledger runs in memory;
after a process restart, each account's balance is reconstructed once from its
starting amount and completed offline transfer records in SQLite. IDs, documents,
transfers, and the session timestamp survive a restart. Use demo reset when you
want session receipts to begin at zero.

Live and offline seed IDs are saved separately. The directory points to the
currently seeded mode; rerun the desired mode's seed when switching modes. IDs
from an offline ledger are never sent to Nessie.

## API smoke checks

```sh
curl --fail --silent --show-error http://localhost:8000/api/health
curl --fail --silent --show-error http://localhost:8000/api/balances
curl --fail --silent --show-error -X POST http://localhost:8000/api/batches -H 'Content-Type: application/json' -d '{"kind":"standard"}'
```

Use the returned `batch_id` and an invoice's `document_id` in these routes:

```text
GET /api/batches/{batch_id}
GET /api/documents/{document_id}
GET /api/documents/{document_id}/pdf
```

Batch summaries contain five invoices. The source PDFs and line-numbered text are
persisted in SQLite; scoring flags and attack metadata are kept in a separate
private table. A batch generated later does not replace earlier documents.

`app/world/attacks.py` exposes all six attack templates for later Attack Lab
integration. Custom payloads accept up to 600 characters and substitute
`{{ATTACKER_ACCOUNT}}`; whitespace is normalized to fit a one-page invoice.
The embedded Vera TrueType fonts cover all built-in invoice and attack text,
including Latin accents. Text using unsupported characters (such as emoji or
CJK) is rejected with a `ValueError` listing their Unicode codepoints before a
PDF is generated; missing-glyph boxes are never substituted. Hidden payloads
use white 1-point drawing color; their text remains in the extracted document.

To advance the session clock and clear run execution state:

```sh
curl --fail --silent --show-error -X POST http://localhost:8000/api/demo/reset
```

Reset reseeds idempotently, retains existing bank accounts and transfer history,
and preserves current balances within the running ledger. Session receipts use
the new timestamp. Live settlement may still be pending; Nessie account balances
remain authoritative.

## Verification

```sh
pytest -q
pytest -q -m live
```

The live test uses configured credentials, seeds real demo accounts, submits a
USD 1.00 paired payment, and reads back both real resource IDs and statuses.
The observed response was pending for both legs, with unchanged bank balances.
It does not claim settlement. An absent key skips the test; a configured key
with a failing service fails instead of switching modes. Full masked request
and response pairs are saved in `docs/nessie_live_transcript.jsonl`.

To reproduce the three requested transfer-schema attempts (which create an
unrouted diagnostic record when the reduced body is accepted):

```sh
python scripts/diagnose_nessie.py
python scripts/diagnose_nessie.py --docs-probe
```
