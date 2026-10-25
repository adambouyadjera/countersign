import { useParams } from 'react-router-dom'
import type { Person } from '@/api/types'
import { QueryView } from '@/components/casefile/Unavailable'
import { Card, Chip, DemoHistoryTag } from '@/components/ui'
import { usePeople, useVendor } from '@/hooks/queries'
import { formatCents } from '@/lib/money'
import { ROLE_LABEL } from '@/lib/roles'
import { isArchivedAccount, isArchivedPayment } from '@/lib/vendorArchive'

function who(people: readonly Person[], id: string): string {
  const p = people.find((x) => x.person_id === id)
  return p ? `${p.name} · ${ROLE_LABEL[p.role]}` : id
}

const dash = (v: string | null | undefined) => (v ? v : '—')

/** GET /api/vendors/{id}: the account on file, who verified each account and when, and past payments. */
export function VendorPage() {
  const { vendorId = '' } = useParams()
  const vendor = useVendor(vendorId)
  const people = usePeople()
  const list = people.data ?? []

  return (
    <div className="flex flex-col font-prose">
      <div className="flex flex-wrap items-baseline gap-4 px-7 pt-5 pb-1.5">
        <h1 className="display m-0 text-[2rem]">{vendor.data?.name ?? `Vendor ${vendorId}`}</h1>
        <span className="font-mono text-small text-muted">{vendorId}</span>
      </div>
      <div className="px-7 pt-3 pb-7">
        <QueryView query={vendor} what="the vendor record" endpoint={`GET /api/vendors/${vendorId}`}>
          {(v) => (
            <div className="grid grid-cols-[minmax(0,26rem)_minmax(0,1fr)] items-start gap-[18px]">
              <div className="flex flex-col gap-[18px]">
                <Card as="section" aria-label="Account on file" label="account on file" className="flex flex-col p-[18px]">
                  <span data-testid="account-on-file" className="font-mono text-[1.6rem] font-bold tabular">
                    {dash(v.account_on_file_display)}
                  </span>
                  <span className="text-small text-muted">From the vendor master. Invoice text can&apos;t change it.</span>
                </Card>
                <Card as="section" aria-label="Account history" label="account history" className="flex flex-col gap-2 p-[18px]">
                  {v.account_history.length === 0 ? (
                    <p className="m-0 text-small text-muted">No account history.</p>
                  ) : (
                    <ol className="m-0 flex list-none flex-col gap-2 p-0">
                      {v.account_history.map((a, i) => (
                        <li key={i} data-account={a.account_display} className="rule-dashed pb-2 text-small">
                          <div className="flex items-center">
                            <span className="font-mono font-bold">{a.account_display}</span>
                            {isArchivedAccount(a) && <DemoHistoryTag className="ml-2" />}
                            {a.valid_to === null && (
                              <Chip tone="good" className="ml-auto py-0 font-mono text-meta">
                                current
                              </Chip>
                            )}
                          </div>
                          <div className="font-mono text-meta text-muted">
                            valid {a.valid_from} → {a.valid_to ?? 'now'}
                          </div>
                          <div data-testid="verified">
                            verified by {who(list, a.verified_by)} · {a.verified_at}
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </Card>
              </div>
              <Card as="section" aria-label="Past payments" label={`past payments · ${v.payments.length}`} className="flex flex-col gap-2 overflow-x-auto p-[18px]">
                {v.payments.length === 0 ? (
                  <p className="m-0 text-small text-muted">No payments to this vendor yet.</p>
                ) : (
                  <table className="w-full border-collapse font-mono text-small">
                    <thead>
                      <tr className="border-b-2 border-ink text-left text-muted">
                        <th className="px-1.5 py-2 font-medium">Date</th>
                        <th className="px-1.5 py-2 text-right font-medium">Amount</th>
                        <th className="px-1.5 py-2 font-medium">Paid to</th>
                        <th className="px-1.5 py-2 font-medium">Journal entry</th>
                      </tr>
                    </thead>
                    <tbody className="tabular">
                      {v.payments.map((p) => (
                        <tr key={p.journal_entry_id} data-payment={p.journal_entry_id} className="rule-dashed">
                          <td className="px-1.5 py-2">{p.date}</td>
                          <td className="px-1.5 py-2 text-right">{formatCents(p.amount_cents)}</td>
                          <td className={p.account_display === v.account_on_file_display ? 'px-1.5 py-2' : 'px-1.5 py-2 text-bad'}>
                            {p.account_display}
                          </td>
                          <td className="px-1.5 py-2">
                            {p.journal_entry_id}
                            {isArchivedPayment(p) && <DemoHistoryTag className="ml-2" />}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <p className="m-0 text-small text-muted">
                  &ldquo;demo history&rdquo; marks the seeded January–June archive: example records, not Nessie transactions.
                </p>
              </Card>
            </div>
          )}
        </QueryView>
      </div>
    </div>
  )
}
