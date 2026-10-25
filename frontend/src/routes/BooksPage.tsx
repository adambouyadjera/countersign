import { useState } from 'react'
import type { Approval, JournalEntry, Person } from '@/api/types'
import { QueryView } from '@/components/casefile/Unavailable'
import { SigSays } from '@/components/sig'
import { Card, Chip, InkRow } from '@/components/ui'
import { useApprovals, useJournal, usePeople, useReconciliation, useTrialBalance } from '@/hooks/queries'
import { booksVerdict, journalNewestFirst, reconSummary, trialTotals } from '@/lib/books'
import { formatCents, formatCentsShort } from '@/lib/money'
import { ROLE_LABEL } from '@/lib/roles'

const SHOWN = 3

/** Amount without the "$", as in the mockup's ledger columns. */
const amt = (c: number) => (c === 0 ? '' : formatCents(c).replace('$', ''))

function personName(people: readonly Person[], id: string): string {
  const p = people.find((x) => x.person_id === id)
  if (!p) return id
  // The AP agent in /api/people is Sig itself.
  return p.role === 'ap_agent' ? 'Sig' : p.name
}

function Badge({ ok, children }: { ok: boolean; children: string }) {
  return (
    <Chip tone={ok ? 'good' : 'bad'} className="py-0.5 font-mono">
      {children}
    </Chip>
  )
}

function EntryCard({ e, people }: { e: JournalEntry; people: readonly Person[] }) {
  const w = Math.max(...e.lines.map((l) => `${l.account_code} ${l.account_name}`.length))
  return (
    <InkRow as="li" data-entry={e.entry_id} className="p-3 font-mono text-small">
      <div className="flex justify-between gap-3 font-prose">
        <strong className="min-w-0">{e.memo}</strong>
        <span className="shrink-0 text-muted">by {personName(people, e.created_by)}</span>
      </div>
      <div className="mt-1.5">
        {e.lines.map((l, i) => {
          const dr = l.debit_cents > 0
          const name = `${l.account_code} ${l.account_name}`
          return (
            <div key={i} className="whitespace-pre">
              {dr ? 'Dr ' : '  Cr '}
              {name.padEnd(w + (dr ? 2 : 0))} {amt(dr ? l.debit_cents : l.credit_cents)}
            </div>
          )
        })}
      </div>
      <div className="mt-1.5 text-muted">
        {e.source.kind === 'payment' ? 'nessie' : e.source.kind} {e.source.id} · {e.date}
      </div>
    </InkRow>
  )
}

function ControlsLog({ people, approvals }: { people: readonly Person[]; approvals: readonly Approval[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0">
      {approvals.map((a) => (
        <li key={a.approval_id}>
          {personName(people, a.requested_by)} requested {a.invoice_number} →{' '}
          <strong>
            needs {/^[aeiou]/i.test(ROLE_LABEL[a.required_role]) ? 'an' : 'a'} {ROLE_LABEL[a.required_role]}; the requester
            can&apos;t approve it
          </strong>
          {a.resolution && <span className="text-muted"> · {a.resolution.decision.replaceAll('_', ' ')}</span>}
        </li>
      ))}
      {people
        .filter((p) => p.role !== 'vendor_admin')
        .map((p) => (
          <li key={p.person_id}>
            {personName(people, p.person_id)} ({ROLE_LABEL[p.role]}){' '}
            {p.approval_limit_cents === null
              ? 'approves with no limit'
              : p.approval_limit_cents === 0
                ? "can't approve payments"
                : `approves up to ${formatCentsShort(p.approval_limit_cents)}`}
          </li>
        ))}
      <li>Vendor bank changes: vendor admin only</li>
    </ul>
  )
}

export function BooksPage() {
  const tb = useTrialBalance()
  const rec = useReconciliation()
  const journal = useJournal()
  const people = usePeople()
  const approvals = useApprovals()
  const [showAll, setShowAll] = useState(false)
  const verdict = booksVerdict(tb.data, rec.data)
  const peopleList = people.data ?? []

  return (
    <div className="flex flex-col font-prose">
      <div className="flex flex-wrap items-baseline gap-4 px-7 pt-5.5 pb-1.5">
        <h1 className="display m-0 text-[2rem]">
          {verdict.agree
            ? 'The books agree with the bank'
            : tb.data && rec.data
              ? "The books and the bank don't agree yet"
              : 'Books and controls'}
        </h1>
        {tb.data && <Badge ok={tb.data.balanced}>{tb.data.balanced ? 'BALANCED' : 'NOT BALANCED'}</Badge>}
        {rec.data && (
          <Badge ok={rec.data.difference_cents === 0}>
            {rec.data.difference_cents === 0
              ? 'RECONCILED · $0.00 DIFFERENCE'
              : `DIFFERENCE · ${formatCents(Math.abs(rec.data.difference_cents))}`}
          </Badge>
        )}
      </div>

      <div className="grid grid-cols-[minmax(0,30rem)_minmax(0,1fr)_clamp(18rem,25vw,25.72rem)] items-start gap-[18px] px-7 pt-4 pb-7">
        <Card as="section" aria-label="Trial balance" label="trial balance" className="flex flex-col overflow-x-auto p-[18px]">
          <QueryView query={tb} what="the trial balance" endpoint="GET /api/ledger/trial_balance">
            {(t) => {
              const tot = trialTotals(t)
              return (
                <>
                  <table className="w-full border-collapse font-mono text-small">
                    <thead>
                      <tr className="border-b-2 border-ink text-left text-muted">
                        <th className="px-1.5 py-2 font-medium">Account</th>
                        <th className="px-1.5 py-2 text-right font-medium">Debit</th>
                        <th className="px-1.5 py-2 text-right font-medium">Credit</th>
                      </tr>
                    </thead>
                    <tbody className="tabular">
                      {t.accounts.map((a) => (
                        <tr key={a.account_code} className="rule-dashed">
                          <td className="px-1.5 py-2">
                            {a.account_code} {a.account_name}
                          </td>
                          <td className="px-1.5 py-2 text-right">{amt(a.debit_cents)}</td>
                          <td className="px-1.5 py-2 text-right">{amt(a.credit_cents)}</td>
                        </tr>
                      ))}
                      <tr className="border-t-2 border-fg font-bold" data-testid="tb-total">
                        <td className="px-1.5 py-2.5">Total</td>
                        <td className="px-1.5 py-2.5 text-right">{amt(tot.debit)}</td>
                        <td className="px-1.5 py-2.5 text-right">{amt(tot.credit)}</td>
                      </tr>
                    </tbody>
                  </table>
                  <p className="m-0 mt-3 text-small text-muted">As of {t.as_of}. Opening balances included.</p>
                </>
              )
            }}
          </QueryView>
        </Card>

        <Card as="section" aria-label="Journal" label="journal · newest first" className="flex min-w-0 flex-col gap-2.5 p-[18px]">
          <QueryView query={journal} what="the journal" endpoint="GET /api/ledger/entries">
            {(entries) => {
              const sorted = journalNewestFirst(entries)
              const shown = showAll ? sorted : sorted.slice(0, SHOWN)
              return sorted.length === 0 ? (
                <p className="m-0 text-small text-muted">No journal entries yet.</p>
              ) : (
                <>
                  <ol aria-label="Journal entries" className="m-0 flex list-none flex-col gap-2.5 p-0">
                    {shown.map((e) => (
                      <EntryCard key={e.entry_id} e={e} people={peopleList} />
                    ))}
                  </ol>
                  {sorted.length > SHOWN && (
                    <button
                      type="button"
                      onClick={() => setShowAll((v) => !v)}
                      className="cursor-pointer self-start border-0 bg-transparent p-0 font-[inherit] text-small text-accent underline"
                    >
                      {showAll ? 'show the newest three' : `${sorted.length - SHOWN} more entries`}
                    </button>
                  )}
                </>
              )
            }}
          </QueryView>
        </Card>

        <section aria-label="Bank reconciliation and controls" className="flex flex-col gap-4">
          <SigSays expression={verdict.expression}>
            <span data-testid="books-verdict">{verdict.line}</span>
          </SigSays>

          <Card label="bank reconciliation" className="flex flex-col p-[18px]">
            {/* The backend reconciles the submitted-payment register, pending items included; it is not settlement. */}
            <p data-testid="recon-basis" className="m-0 mb-2 text-small text-muted">
              Reconciled against submitted transfers (Nessie sandbox transfers stay pending)
            </p>
            <QueryView query={rec} what="the reconciliation" endpoint="GET /api/reconciliation">
              {(r) => {
                const s = reconSummary(r)
                return (
                  <>
                    <div className="mt-3 grid grid-cols-2 gap-2.5">
                      <div>
                        <div className="text-meta text-muted">Submitted transfers</div>
                        <div className="font-mono text-[1.286rem] font-bold" data-testid="recon-bank">
                          {s.bank.count} · {formatCentsShort(s.bank.cents)}
                        </div>
                      </div>
                      <div>
                        <div className="text-meta text-muted">Ledger cash out</div>
                        <div className="font-mono text-[1.286rem] font-bold" data-testid="recon-ledger">
                          {s.ledger.count} · {formatCentsShort(s.ledger.cents)}
                        </div>
                      </div>
                    </div>
                    {s.unmatched.length === 0 ? (
                      <p className="m-0 mt-3 font-semibold text-good">Every withdrawal matches a journal entry by id.</p>
                    ) : (
                      <ul aria-label="Unmatched items" className="m-0 mt-3 list-none p-0 text-small text-bad">
                        {s.unmatched.map((i, n) => (
                          <li key={n}>
                            {i.status === 'bank_only' ? `bank only: ${i.transfer_id}` : `ledger only: ${i.journal_entry_id}`} ·{' '}
                            {formatCents(i.amount_cents)}
                            {i.note && ` · ${i.note}`}
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )
              }}
            </QueryView>
          </Card>

          <Card label="controls log" className="flex flex-col gap-2 p-[18px]">
            <QueryView query={people} what="people and limits" endpoint="GET /api/people">
              {(p) => <ControlsLog people={p} approvals={approvals.data ?? []} />}
            </QueryView>
            <div className="text-small text-muted">From GET /api/people and /api/approvals.</div>
          </Card>
        </section>
      </div>
    </div>
  )
}
