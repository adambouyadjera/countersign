import type { UseMutationResult } from '@tanstack/react-query'
import { Button, EmptyState, Money, Panel } from '@/components/ui'
import type { Batch, Lane } from '@/api/types'
import { LANES, type InvoiceLaneState, type RunState } from '@/state/runReducer'
import { InvoiceStatusChip, type ChipStatus } from './InvoiceStatusChip'
import { RequestError } from './RequestError'

export interface BatchRailProps {
  batch: Batch | null
  newBatch: UseMutationResult<Batch, Error, void>
  /** Lanes the current run started; null when there is no run. */
  runningLanes: Lane[] | null
  runState: RunState
}

export function BatchRail({ batch, newBatch, runningLanes, runState }: BatchRailProps) {
  const chipFor = (lane: Lane, invoiceId: string): { status: ChipStatus; resolution?: InvoiceLaneState['resolution'] } => {
    if (!runningLanes) return { status: 'idle' }
    if (!runningLanes.includes(lane)) return { status: 'not_running' }
    const inv = runState.lanes[lane].invoices[invoiceId]
    return { status: inv?.status ?? 'pending', resolution: inv?.resolution }
  }

  return (
    <Panel
      dense
      eyebrow={batch ? `Batch · ${batch.kind}` : 'Batch'}
      title="Invoices"
      aria-label="Invoice batch"
      className="flex min-h-0 flex-col"
      bodyClassName="flex min-h-0 flex-1 flex-col"
    >
      {batch && (
        <p className="m-0 mb-1.5 truncate font-mono text-[0.6875rem] text-ink-soft" title={batch.batch_id}>
          {batch.batch_id}
        </p>
      )}
      <div className="relative min-h-0 flex-1 overflow-y-auto">
        {newBatch.isError ? (
          <RequestError
            title="Could not draw a batch"
            error={newBatch.error}
            onRetry={() => newBatch.mutate()}
            retrying={newBatch.isPending}
          />
        ) : newBatch.isPending && !batch ? (
          <p role="status" className="m-0 py-6 text-center text-sm text-ink-soft">
            Drawing a batch…
          </p>
        ) : !batch ? (
          <EmptyState
            title="No batch drawn"
            body="Draw a fresh batch of five invoices from the backend. One of them is poisoned; the batch does not say which."
          />
        ) : batch.invoices.length === 0 ? (
          <EmptyState title="Empty batch" body="The backend returned a batch with no invoices." />
        ) : (
          <ol aria-label="Invoices in batch" className="m-0 list-none space-y-1.5 p-0">
            {batch.invoices.map((inv) => (
              <li
                key={inv.invoice_id}
                data-invoice={inv.invoice_id}
                className="border border-rule-strong border-l-[3px] border-l-ink bg-paper px-2 py-1.5"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-[0.8125rem] font-medium text-ink">{inv.invoice_number}</span>
                  <Money cents={inv.amount_cents} currency={inv.currency} className="text-[0.9375rem] text-ink" />
                </div>
                <div className="flex items-baseline justify-between gap-2 text-[0.8125rem]">
                  <span className="min-w-0 truncate text-ink" title={inv.vendor_name}>
                    {inv.vendor_name}
                  </span>
                  <span className="shrink-0 font-mono tabular text-[0.75rem] text-ink-soft">
                    due {inv.due_date}
                  </span>
                </div>
                <div className="mt-1 grid grid-cols-2 gap-1">
                  {LANES.map((lane) => {
                    const c = chipFor(lane, inv.invoice_id)
                    return (
                      <InvoiceStatusChip key={lane} lane={lane} status={c.status} resolution={c.resolution} />
                    )
                  })}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div className="mt-2 border-t border-rule-strong pt-2">
        <Button
          size="sm"
          className="w-full"
          variant={batch ? 'quiet' : 'primary'}
          onClick={() => newBatch.mutate()}
          disabled={newBatch.isPending}
        >
          {newBatch.isPending ? 'Drawing…' : 'New batch'}
        </Button>
      </div>
    </Panel>
  )
}
