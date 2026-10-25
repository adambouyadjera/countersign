import { useEffect, useRef, type ReactNode } from 'react'
import type { UseMutationResult } from '@tanstack/react-query'
import type { Batch } from '@/api/types'
import { Card, Chip, InkRow } from '@/components/ui'
import { cn } from '@/lib/cn'
import { inboxRows, receivedTime } from '@/lib/inboxOrder'
import { formatCentsShort } from '@/lib/money'
import type { InvoiceStatus } from '@/state/runReducer'
import { RequestError } from './RequestError'

export interface InboxTotals {
  letIn: { count: number; cents: number }
  held: { count: number; cents: number }
  /** Countersign money sent to the attacker account; label says how it was identified. */
  leaked: { label: string; cents: number }
}

export interface InboxColumnProps {
  batch: Batch | null
  newBatch: UseMutationResult<Batch, Error, void>
  /** Countersign lane status per invoice; null before a run. */
  statuses: Record<string, InvoiceStatus> | null
  selectedId: string | undefined
  onSelect: (invoiceId: string) => void
  totals: InboxTotals | null
  /** Run controls, under the invoice list. */
  controls?: ReactNode
  /** The AP mailbox panel, under the controls. */
  mail?: ReactNode
  /** Autopilot processes the inbox by itself; the empty state says so. */
  autopilotOn?: boolean
  /** document_id → when GET /api/inbox says it arrived; orders the list newest first. */
  receivedByDoc?: ReadonlyMap<string, string>
  /** Documents submitted through the Attack Lab (from this browser). */
  attackDocs?: ReadonlySet<string>
}

const NO_TIMES: ReadonlyMap<string, string> = new Map()
const NO_DOCS: ReadonlySet<string> = new Set()

// Each mark's meaning, for the legend and the row tooltips.
const GLYPH: Record<InvoiceStatus | 'none', { glyph: string; tone: string; word: string }> = {
  none: { glyph: '·', tone: 'text-muted', word: 'not started' },
  pending: { glyph: '·', tone: 'text-muted', word: 'queued' },
  extracting: { glyph: '⚔', tone: 'text-accent', word: 'processing' },
  cleared: { glyph: '⚔', tone: 'text-accent', word: 'processing: cleared, paying' },
  paid: { glyph: '✓', tone: 'text-good', word: 'paid' },
  unknown_account: { glyph: '✗', tone: 'text-bad', word: 'paid to an unknown account' },
  held: { glyph: '⚑', tone: 'text-bad', word: 'held' },
  blocked: { glyph: '✗', tone: 'text-bad', word: 'blocked' },
  failed: { glyph: '!', tone: 'text-bad', word: 'failed' },
}

/** Legend wording per mark; marks shared by several statuses list them all. */
const LEGEND: { glyph: string; tone: string; text: string; statuses: (InvoiceStatus | 'none')[] }[] = [
  { glyph: '✓', tone: 'text-good', text: 'paid on file', statuses: ['paid'] },
  { glyph: '⚑', tone: 'text-bad', text: 'held', statuses: ['held'] },
  { glyph: '✗', tone: 'text-bad', text: 'blocked, or paid an account not on file', statuses: ['blocked', 'unknown_account'] },
  { glyph: '⚔', tone: 'text-accent', text: 'processing', statuses: ['extracting', 'cleared'] },
  { glyph: '!', tone: 'text-bad', text: 'failed', statuses: ['failed'] },
  { glyph: '·', tone: 'text-muted', text: 'queued', statuses: ['pending', 'none'] },
]

export function InboxColumn({
  batch,
  newBatch,
  statuses,
  selectedId,
  onSelect,
  totals,
  controls,
  mail,
  autopilotOn,
  receivedByDoc = NO_TIMES,
  attackDocs = NO_DOCS,
}: InboxColumnProps) {
  const rows = batch ? inboxRows(batch.invoices, receivedByDoc) : []
  const statusOf = (id: string): InvoiceStatus | 'none' => (statuses ? (statuses[id] ?? 'pending') : 'none')
  const present = new Set(rows.map((r) => statusOf(r.invoice.invoice_id)))
  const legend = LEGEND.filter((l) => l.statuses.some((s) => present.has(s)))

  // Keep the selected invoice in view, however long the list gets.
  const selectedRef = useRef<HTMLLIElement>(null)
  // Also when the order changes (inbox times arrive, new invoices land) without a new selection.
  const order = rows.map((r) => r.invoice.invoice_id).join('|')
  useEffect(() => {
    const el = selectedRef.current
    if (!el) return
    // The top row: show the card's label too, not just the row.
    if (!el.previousElementSibling) el.closest('aside')?.scrollTo?.({ top: 0 })
    else el.scrollIntoView?.({ block: 'nearest' })
  }, [selectedId, order])

  return (
    <Card
      as="aside"
      aria-label="Inbox"
      label={`inbox · ${batch ? batch.invoices.length : 0}`}
      className="my-[18px] ml-[18px] flex min-h-0 flex-col gap-1.5 overflow-y-auto px-4 py-[18px]"
    >

      {newBatch.isError ? (
        <RequestError
          title="Could not draw a batch"
          error={newBatch.error}
          onRetry={() => newBatch.mutate()}
          retrying={newBatch.isPending}
        />
      ) : !batch ? (
        <p className="m-0 py-2 text-small text-muted">
          {newBatch.isPending
            ? 'drawing a batch…'
            : autopilotOn
              ? 'Autopilot is on: invoices are processed as they arrive. Submit one in the Attack Lab or draw a batch.'
              : 'Empty. Draw a batch of five invoices; one is poisoned.'}
        </p>
      ) : (
        <ol aria-label="Invoices in batch" className="m-0 flex list-none flex-col gap-1.5 p-0">
          {rows.map(({ invoice: inv, receivedAt }) => {
            const status = statusOf(inv.invoice_id)
            const g = GLYPH[status]
            const selected = inv.invoice_id === selectedId
            const attack = attackDocs.has(inv.document_id)
            // The picked row is an InkRow with the cobalt outline; the rest are bare until hovered.
            const Row = selected ? InkRow : 'button'
            return (
              <li
                key={inv.invoice_id}
                ref={selected ? selectedRef : undefined}
                data-invoice={inv.invoice_id}
                data-status={status}
                data-attack={attack ? 'true' : undefined}
              >
                <Row
                  {...(selected ? { as: 'button', tone: 'selected' as const } : {})}
                  type="button"
                  onClick={() => onSelect(inv.invoice_id)}
                  aria-current={selected ? 'true' : undefined}
                  title={`${g.word} · ${inv.vendor_name} · due ${inv.due_date}`}
                  className={cn(
                    'flex w-full cursor-pointer flex-col gap-0.5 rounded-[6px] px-2.5 py-2 text-left font-[inherit] text-fg',
                    'focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-accent focus-visible:outline-solid',
                    !selected && 'border-[1.5px] border-transparent bg-transparent hover:bg-inner',
                  )}
                >
                  <span className="flex w-full items-baseline justify-between gap-2">
                    <span className="min-w-0 truncate">
                      <span aria-hidden className={cn('inline-block w-[1.5ch]', g.tone)}>
                        {g.glyph}
                      </span>
                      {inv.invoice_number}
                      <span className="sr-only">, {g.word}</span>
                    </span>
                    <span className={cn('shrink-0 tabular', !selected && 'text-muted')}>
                      {formatCentsShort(inv.amount_cents)}
                    </span>
                  </span>
                  {(attack || receivedAt) && (
                    <span className="flex items-center gap-1.5 pl-[1.5ch] text-meta text-muted">
                      {attack && (
                        <Chip tone="untrusted" data-testid="attack-tag" className="px-1.5 py-0 font-mono text-[0.714rem]">
                          attack
                        </Chip>
                      )}
                      {receivedAt && <span data-testid="received-at">{attack ? '· ' : 'received '}{receivedTime(receivedAt)}</span>}
                    </span>
                  )}
                </Row>
              </li>
            )
          })}
        </ol>
      )}
      {legend.length > 0 && (
        <ul aria-label="What the marks mean" className="m-0 mt-1 flex list-none flex-wrap gap-x-3 gap-y-0.5 p-0 text-meta text-muted">
          {legend.map((l) => (
            <li key={l.glyph}>
              <span aria-hidden className={cn('font-bold', l.tone)}>
                {l.glyph}
              </span>{' '}
              {l.text}
            </li>
          ))}
        </ul>
      )}

      {controls && <div className="mt-3 flex flex-wrap gap-2">{controls}</div>}
      {mail}

      <div className="mt-auto pt-4 text-meta leading-[1.8] text-muted" aria-label="Totals for this run">
        {totals ? (
          <>
            this run
            <br />
            {totals.letIn.count} let in · {formatCentsShort(totals.letIn.cents)}
            <br />
            {totals.held.count} held · {formatCentsShort(totals.held.cents)}
            <br />
            {totals.leaked.label} ·{' '}
            <span data-testid="leaked" className={totals.leaked.cents === 0 ? 'text-good' : 'text-bad'}>
              {formatCentsShort(totals.leaked.cents)}
            </span>
          </>
        ) : (
          'no run yet'
        )}
      </div>
    </Card>
  )
}
