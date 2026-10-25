import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { isApiError } from '@/api/errors'
import { RequestError } from '@/components/console/RequestError'
import { Card, InkButton, InkRow } from '@/components/ui'
import { chipClass } from '@/components/ui/chipStyle'
import { CountersignActions } from '@/components/casefile/CountersignActions'
import { usePendingApprovals } from '@/hooks/queries'
import { useActingAs } from '@/hooks/useActingAs'
import { maskAccount } from '@/lib/account'
import { cn } from '@/lib/cn'
import { formatCents } from '@/lib/money'
import { ROLE_LABEL } from '@/lib/roles'

/** Header button with the pending-approval count, and the drawer listing them (GET /api/approvals?status=pending). */
export function QueueDrawer() {
  const q = usePendingApprovals()
  const { people, person } = useActingAs()
  const [open, setOpen] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  // Filtered here too, in case a server ignores ?status=.
  const pending = (q.data ?? []).filter((a) => a.status === 'pending')
  const notBuilt = isApiError(q.error) && q.error.status === 404

  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    const button = buttonRef.current
    return () => {
      window.removeEventListener('keydown', onKey)
      button?.focus()
    }
  }, [open])

  const count = q.data ? String(pending.length) : q.isError ? '?' : '…'
  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Countersign queue: ${q.data ? `${pending.length} pending` : q.isError ? 'unavailable' : 'loading'}`}
        className={cn(chipClass({ button: true }), 'py-0.5')}
      >
        queue
        <span
          data-testid="queue-count"
          className={cn(
            'min-w-5 rounded-[10px] px-1.5 text-center text-meta font-bold',
            pending.length > 0 ? 'bg-bad text-on-bad' : 'bg-panel text-muted',
          )}
        >
          {count}
        </span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 bg-fg/25" onClick={() => setOpen(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="queue-title"
            onClick={(e) => e.stopPropagation()}
            className="absolute inset-y-0 right-0 flex w-[min(30rem,100vw)] flex-col gap-3 overflow-y-auto border-l-2 border-ink bg-[image:var(--page-ground)] bg-bg p-5 font-prose"
          >
            <div className="flex items-baseline gap-3">
              <h2 id="queue-title" className="display m-0 text-lead">
                Countersign queue
              </h2>
              <span className="font-mono text-meta text-muted">pending</span>
              <InkButton ref={closeRef} variant="secondary" size="sm" onClick={() => setOpen(false)} className="ml-auto font-mono">
                close
              </InkButton>
            </div>

            {q.isPending ? (
              <p className="m-0 text-small text-muted">reading approvals…</p>
            ) : notBuilt ? (
              <InkRow data-testid="not-available" className="px-3.5 py-3 text-small">
                <p className="m-0 font-bold">approvals not available yet</p>
                <p className="m-0 font-mono text-meta text-muted">GET /api/approvals?status=pending → 404 not_found</p>
              </InkRow>
            ) : q.isError ? (
              <RequestError title="Could not read approvals" error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
            ) : pending.length === 0 ? (
              <p className="m-0 text-muted">Nothing waiting for a countersign.</p>
            ) : (
              <ul aria-label="Pending approvals" className="m-0 flex list-none flex-col gap-2.5 p-0">
                {pending.map((a) => (
                  <Card as="li" key={a.approval_id} className="p-3.5">
                    <Link
                      to={`/runs/${encodeURIComponent(a.run_id)}/invoices/${encodeURIComponent(a.invoice_id)}`}
                      onClick={() => setOpen(false)}
                      className="font-bold text-accent"
                    >
                      {a.invoice_number} · {a.vendor_name} · {formatCents(a.amount_cents)}
                    </Link>
                    <p className="m-0 text-small">
                      asks for <span className="font-mono text-bad">{maskAccount(a.requested_account_display)}</span> · on
                      file <span className="font-mono">{maskAccount(a.account_on_file_display)}</span>
                    </p>
                    <p className="m-0 font-mono text-meta text-muted">
                      {a.reason_codes.join(', ')} · needs {ROLE_LABEL[a.required_role]} · {a.created_at}
                    </p>
                    {/* The vendor id isn't on Approval; the case file and console link to the vendor record. */}
                    <div className="mt-2.5">
                      <CountersignActions approval={a} people={people.data ?? []} defaultActorId={person?.person_id} />
                    </div>
                  </Card>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </>
  )
}
