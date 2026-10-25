import { useState, type ReactNode } from 'react'
import type { DocumentText, Lane, UnguardedMode } from '@/api/types'
import { DocumentViewer } from '@/components/casefile/DocumentViewer'
import { SigSays } from '@/components/sig'
import { Card, CardLabel, Chip, InkButton, InkRow } from '@/components/ui'
import { dash, transfers, type BcHoldRow, type BcLane, type BcLanes, type BcPaymentRow } from '@/lib/bankChange'
import { formatCents } from '@/lib/money'

/** A full display account number. These are the bank's display numbers, shown in full so the two can be compared. */
function Acct({ value, testId }: { value: string | undefined; testId?: string }) {
  return (
    <span data-testid={testId} className="font-mono tabular font-bold whitespace-nowrap">
      {dash(value)}
    </span>
  )
}

export interface LetterView {
  vendorName?: string
  requestedAccount?: string
  onFileAccount?: string
  document?: DocumentText
}

/**
 * The vendor letter: untrusted text, so a dashed red card. Compact by default (sender,
 * what it asks for, the account) so both lanes fit at 1280x720; the full text is a toggle away.
 */
export function LetterCard({ letter, documentState }: { letter: LetterView | undefined; documentState?: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <Card as="section" tone="untrusted" aria-label="Vendor letter" className="flex flex-col gap-2 px-[18px] py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <CardLabel>vendor bank-change letter · untrusted</CardLabel>
        {!letter ? (
          <p className="m-0 text-small text-muted">This run has no bank-change letter in its data.</p>
        ) : (
          <>
            <span className="text-small">
              <span className="text-muted">From </span>
              <strong data-testid="letter-from">{dash(letter.vendorName)}</strong>
            </span>
            <span data-testid="letter-summary" className="text-small">
              <span className="text-muted">· moves payments off the account on file</span> <Acct value={letter.onFileAccount} testId="letter-on-file" />
            </span>
            <span className="text-small font-bold text-bad">
              Asks to pay <Acct value={letter.requestedAccount} testId="letter-account" />
            </span>
            <InkButton
              size="sm"
              variant="secondary"
              className="ml-auto min-h-7 py-0"
              aria-expanded={open}
              aria-controls="letter-text"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? 'Hide full letter' : 'Show full letter'}
            </InkButton>
          </>
        )}
      </div>
      {letter && open && (
        <div id="letter-text" className="max-h-[16rem] min-w-0 overflow-y-auto rounded-[6px] border-[1.5px] border-dashed border-bad bg-card px-3 py-2">
          {letter.document ? (
            <DocumentViewer doc={letter.document} spans={[]} />
          ) : (
            documentState ?? <p className="m-0 text-small text-muted">—</p>
          )}
        </div>
      )}
    </Card>
  )
}

function PaymentRow({ p }: { p: BcPaymentRow }) {
  return (
    <InkRow as="li" tone={p.offFile ? 'untrusted' : 'default'} data-transfer={p.transferId} className="flex flex-col gap-0.5 px-3 py-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-small">
        <strong className="min-w-0 text-body">{dash(p.vendorName)}</strong>
        <span className="font-mono text-meta text-muted">{dash(p.invoiceNumber)}</span>
        <span className="text-muted">
          to <Acct value={p.toAccount} />
        </span>
        <span className="ml-auto font-mono tabular font-bold">{formatCents(p.amountCents)}</span>
        <Chip className="py-0 font-mono text-meta leading-[1.4]">{p.transferStatus}</Chip>
      </div>
      {p.offFile && (
        <div data-testid="letter-payment" className="flex flex-wrap gap-x-3 text-small text-bad">
          <span className="font-mono font-bold tracking-[0.04em] uppercase">
            {p.toLetterAccount ? 'Paid to account from letter' : 'Paid to an account not on file'}
          </span>
          <span>
            on file <Acct value={p.onFileAccount} /> · paid <Acct value={p.toAccount} />
          </span>
        </div>
      )}
    </InkRow>
  )
}

function HoldRow({ h }: { h: BcHoldRow }) {
  return (
    <InkRow as="li" tone="untrusted" data-hold={h.invoiceId} className="flex flex-col gap-1 px-3 py-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-small">
        <strong className="min-w-0 text-body">{dash(h.vendorName)}</strong>
        <span className="font-mono text-meta text-muted">{dash(h.invoiceNumber)}</span>
        <Chip tone="untrusted" className="py-0 font-mono text-meta font-bold">
          HELD
        </Chip>
        {h.reasonCodes.map((c) => (
          <Chip key={c} tone="untrusted" data-testid="reason-code" className="py-0 font-mono text-meta">
            {c}
          </Chip>
        ))}
        <span className="ml-auto font-mono tabular font-bold">{h.amountCents === undefined ? '—' : formatCents(h.amountCents)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 text-small">
        <span>
          <span className="text-muted">on file</span> <Acct value={h.onFileAccount} testId="hold-on-file" />
        </span>
        <span className="text-bad">
          letter <Acct value={h.letterAccount} testId="hold-letter" />
        </span>
        {!h.paidLater && (
          <span data-testid="no-payment" className="ml-auto font-mono font-bold">
            No payment sent
          </span>
        )}
      </div>
    </InkRow>
  )
}

const LANE_TITLE: Record<Lane, string> = { unguarded: 'Unguarded agent', countersign: 'Countersign' }

function TallyLine({ lane }: { lane: BcLane }) {
  const t = lane.tally
  if (!t) return <p className="m-0 font-mono text-meta text-muted">totals arrive with run.completed</p>
  return (
    <p data-testid={`tally-${lane.lane}`} className="m-0 font-mono text-meta text-muted">
      paid {t.paid_count} · {formatCents(t.paid_cents)} · held {t.held_count} · not on file {t.not_on_file_count}
      {t.not_on_file_count > 0 && ` · ${formatCents(t.not_on_file_cents)}`}
    </p>
  )
}

export function LanePanel({
  lane,
  unguardedMode,
  extractionModels,
  leakGuard,
}: {
  lane: BcLane
  unguardedMode?: UnguardedMode
  extractionModels?: string[]
  leakGuard?: { strings_checked: number; canaries_checked: number; matches: number } | null
}) {
  const isCs = lane.lane === 'countersign'
  const open = lane.holds.filter((h) => !h.paidLater)
  return (
    <Card as="section" aria-label={LANE_TITLE[lane.lane]} className="flex min-w-0 flex-col gap-2 px-[18px] py-3">
      <div className="flex flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="m-0">
            <CardLabel>{LANE_TITLE[lane.lane].toLowerCase()}</CardLabel>
          </h2>
          <span data-testid={`model-${lane.lane}`} className="font-mono text-small font-bold">
            {isCs ? 'planner ' : ''}
            {dash(lane.provider)} · {dash(lane.model)}
          </span>
        </div>
        <p className="m-0 font-mono text-meta text-muted">
          {!isCs && unguardedMode && <>mode {unguardedMode} · </>}
          {isCs && extractionModels && extractionModels.length > 0 && <>extraction {extractionModels.join(', ')} · </>}
          lane {dash(lane.status)}
        </p>
        <TallyLine lane={lane} />
      </div>

      {isCs && open[0] && (
        <div data-theme="paper">
          <SigSays expression="serious" className="py-2">
            I held {dash(open[0].invoiceNumber)}: the letter's account isn't the one on file.
          </SigSays>
        </div>
      )}

      {lane.payments.length + lane.holds.length === 0 ? (
        <p className="m-0 text-small text-muted">No payments yet.</p>
      ) : (
        <ul aria-label={`${LANE_TITLE[lane.lane]} payments`} className="m-0 flex list-none flex-col gap-1.5 p-0">
          {lane.holds.map((h) => (
            <HoldRow key={h.approvalId} h={h} />
          ))}
          {lane.payments.map((p) => (
            <PaymentRow key={p.transferId} p={p} />
          ))}
        </ul>
      )}

      {/* The leak-guard counts describe the planner's run: shown once the planner has proposed its plan. */}
      {isCs && lane.planned && (
        <p data-testid="leak-guard" className="m-0 font-mono text-meta text-muted">
          leak guard:{' '}
          {leakGuard
            ? `${leakGuard.strings_checked} strings, ${leakGuard.canaries_checked} canaries checked · ${leakGuard.matches} reached the planner`
            : '—'}
        </p>
      )}
    </Card>
  )
}

export function TransferStrip({ lanes }: { lanes: BcLanes }) {
  const rows = transfers(lanes)
  return (
    <Card as="section" aria-label="Nessie transfers" label="nessie transfers" className="flex flex-col gap-2 p-[18px]">
      {rows.length === 0 ? (
        <p className="m-0 text-small text-muted">No transfers yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-small">
            <thead>
              <tr className="border-b-2 border-ink text-left text-muted">
                <th className="px-1.5 py-1.5 font-medium">Transfer id</th>
                <th className="px-1.5 py-1.5 font-medium">Lane</th>
                <th className="px-1.5 py-1.5 font-medium">Invoice</th>
                <th className="px-1.5 py-1.5 text-right font-medium">Amount</th>
                <th className="px-1.5 py-1.5 font-medium">To</th>
                <th className="px-1.5 py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {rows.map((t) => (
                <tr key={`${t.lane}-${t.transferId}`} className={t.offFile ? 'rule-dashed text-bad' : 'rule-dashed'}>
                  <td className="px-1.5 py-1.5">{t.transferId}</td>
                  <td className="px-1.5 py-1.5">{t.lane}</td>
                  <td className="px-1.5 py-1.5">{dash(t.invoiceNumber)}</td>
                  <td className="px-1.5 py-1.5 text-right">{formatCents(t.amountCents)}</td>
                  <td className="px-1.5 py-1.5">{t.toAccount}</td>
                  <td className="px-1.5 py-1.5" data-testid="transfer-status">
                    {t.transferStatus}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="m-0 text-small text-muted">
        Nessie is a sandbox bank: its transfers stay pending and never settle. "pending" is the status Nessie returned.
      </p>
    </Card>
  )
}
