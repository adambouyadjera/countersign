import type { Batch, InboxItem } from '@/api/types'
import { Chip, InkButton, InkRow } from '@/components/ui'
import { useAutopilot, useInboxList } from '@/hooks/queries'
import { useOpenRun } from '@/hooks/useOpenRun'
import { cn } from '@/lib/cn'
import { RequestError } from './RequestError'

const STATUS_TONE: Record<InboxItem['status'], 'default' | 'good' | 'untrusted'> = {
  queued: 'default',
  processing: 'untrusted',
  processed: 'good',
}

/** HH:MM:SS of an ISO time, as the API sent it (UTC). */
const clock = (iso: string) => iso.match(/T(\d\d:\d\d:\d\d)/)?.[1] ?? iso

/**
 * The AP mailbox, GET /api/inbox: what arrived, from whom, and where it is
 * (queued → processing → processed; processed means finished, not paid).
 * With autopilot on, offers to follow its long-lived run.
 */
export function MailPanel({ batch, runId }: { batch: Batch | null; runId: string | undefined }) {
  const autopilot = useAutopilot()
  const on = autopilot.data?.enabled === true
  const inbox = useInboxList(on)
  const open = useOpenRun()
  const autoRun = autopilot.data?.run_id ?? null
  const items = [...(inbox.data ?? [])].sort((a, b) => b.received_at.localeCompare(a.received_at))
  const invoiceOf = (docId: string) => batch?.invoices.find((i) => i.document_id === docId)

  return (
    <section aria-label="AP inbox" className="mt-3 flex flex-col gap-1.5 border-t border-dashed border-rule pt-3">
      <div className="flex items-center gap-2 font-mono text-meta text-muted">
        <span className="font-bold tracking-[0.06em] uppercase">mail · {inbox.data ? items.length : '—'}</span>
        <span>autopilot {autopilot.data ? (on ? 'on' : 'off') : '—'}</span>
      </div>
      {autoRun && autoRun !== runId && (
        <InkButton size="sm" variant="secondary" onClick={() => open.mutate({ runId: autoRun })} disabled={open.isPending}>
          {open.isPending ? 'opening…' : 'watch the autopilot run'}
        </InkButton>
      )}
      {open.isError && <RequestError title="Could not open the autopilot run" error={open.error} />}
      {inbox.isPending ? (
        <p className="m-0 text-small text-muted">reading the inbox…</p>
      ) : inbox.isError ? (
        <RequestError title="Could not read the inbox" error={inbox.error} onRetry={() => void inbox.refetch()} retrying={inbox.isFetching} />
      ) : items.length === 0 ? (
        <p className="m-0 text-small text-muted">No mail yet.</p>
      ) : (
        <ol aria-label="Inbox items" className="m-0 flex list-none flex-col gap-1.5 p-0">
          {items.map((it) => {
            const inv = invoiceOf(it.document_id)
            const mine = Boolean(runId) && it.run_id === runId
            return (
              <InkRow
                as="li"
                key={it.item_id}
                data-inbox-item={it.item_id}
                data-status={it.status}
                tone={mine ? 'selected' : 'default'}
                className="flex flex-col gap-0.5 px-2.5 py-1.5 text-meta"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 truncate font-bold">{inv ? inv.invoice_number : it.document_id}</span>
                  <Chip tone={STATUS_TONE[it.status]} className="ml-auto py-0 font-mono text-meta">
                    {it.status}
                  </Chip>
                </div>
                <span className={cn('truncate text-muted')} title={it.sender}>
                  {it.sender} · {clock(it.received_at)}
                </span>
                {it.run_id && <span className="truncate font-mono text-muted">run {it.run_id}</span>}
              </InkRow>
            )
          })}
        </ol>
      )}
    </section>
  )
}
