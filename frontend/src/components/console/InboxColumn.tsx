import type { ReactNode } from 'react'
import type { UseMutationResult } from '@tanstack/react-query'
import type { Batch } from '@/api/types'
import { Card, InkRow } from '@/components/ui'
import { cn } from '@/lib/cn'
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
}

const GLYPH: Record<InvoiceStatus | 'none', { glyph: string; tone: string; word: string }> = {
  none: { glyph: '·', tone: 'text-muted', word: 'not started' },
  pending: { glyph: '·', tone: 'text-muted', word: 'queued' },
  extracting: { glyph: '⚔', tone: 'text-accent', word: 'dueling' },
  cleared: { glyph: '⚔', tone: 'text-accent', word: 'cleared, paying' },
  paid: { glyph: '✓', tone: 'text-good', word: 'paid' },
  unknown_account: { glyph: '✗', tone: 'text-bad', word: 'paid to an unknown account' },
  held: { glyph: '⚑', tone: 'text-bad', word: 'held' },
  blocked: { glyph: '✗', tone: 'text-bad', word: 'blocked' },
  failed: { glyph: '!', tone: 'text-bad', word: 'failed' },
}

export function InboxColumn({ batch, newBatch, statuses, selectedId, onSelect, totals, controls }: InboxColumnProps) {
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
          {newBatch.isPending ? 'drawing a batch…' : 'Empty. Draw a batch of five invoices; one is poisoned.'}
        </p>
      ) : (
        <ol aria-label="Invoices in batch" className="m-0 flex list-none flex-col gap-1.5 p-0">
          {batch.invoices.map((inv) => {
            const status = statuses ? (statuses[inv.invoice_id] ?? 'pending') : 'none'
            const g = GLYPH[status]
            const selected = inv.invoice_id === selectedId
            // The picked row is an InkRow with the cobalt outline; the rest are bare until hovered.
            const Row = selected ? InkRow : 'button'
            return (
              <li key={inv.invoice_id} data-invoice={inv.invoice_id} data-status={status}>
                <Row
                  {...(selected ? { as: 'button', tone: 'selected' as const } : {})}
                  type="button"
                  onClick={() => onSelect(inv.invoice_id)}
                  aria-current={selected ? 'true' : undefined}
                  title={`${inv.vendor_name} · due ${inv.due_date}`}
                  className={cn(
                    'flex w-full cursor-pointer items-baseline justify-between gap-2 rounded-[6px] px-2.5 py-2 text-left font-[inherit] text-fg',
                    'focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-accent focus-visible:outline-solid',
                    !selected && 'border-[1.5px] border-transparent bg-transparent hover:bg-inner',
                  )}
                >
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
                </Row>
              </li>
            )
          })}
        </ol>
      )}

      {controls && <div className="mt-3 flex flex-wrap gap-2">{controls}</div>}

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
