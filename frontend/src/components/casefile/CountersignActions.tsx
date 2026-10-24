import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { resolveApproval } from '@/api/client'
import { isApiError } from '@/api/errors'
import type { Approval, ApprovalDecision, Person } from '@/api/types'
import { RequestError } from '@/components/console/RequestError'
import { Button } from '@/components/ui'
import { lastFour, maskAccount } from '@/lib/account'
import { ROLE_LABEL } from '@/lib/roles'

const RESOLVED: Record<ApprovalDecision['decision'], string> = {
  pay_vendor_on_file: 'Paid the account on file',
  approve_new_account: 'Approved the new account',
  reject: 'Rejected',
}

export interface CountersignActionsProps {
  approval: Approval
  /** Who the header says we are acting as; actions are sent with this actor_id. */
  actor: Person | undefined
  people: readonly Person[]
}

/** POST /api/approvals/{id}: pay the account on file, approve the new account (with its last 4), or reject. */
export function CountersignActions({ approval, actor, people }: CountersignActionsProps) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [last4, setLast4] = useState('')
  const resolve = useMutation({
    mutationFn: (body: Omit<ApprovalDecision, 'actor_id'>) =>
      resolveApproval(approval.approval_id, { ...body, actor_id: actor!.person_id }),
    onSuccess: () => {
      setConfirming(false)
      void qc.invalidateQueries({ queryKey: ['approvals'] })
    },
  })

  const requester = people.find((p) => p.person_id === approval.requested_by)
  const requesterName = requester?.name ?? approval.requested_by
  const err = resolve.error
  const sod = isApiError(err) && err.code === 'sod_violation'

  if (approval.status === 'resolved' && approval.resolution) {
    const r = approval.resolution
    return (
      <div data-testid="approval-resolution" className="text-small">
        <p className="m-0 font-bold">{RESOLVED[r.decision]}</p>
        {r.note && <p className="m-0 text-muted">“{r.note}”</p>}
        <p className="m-0 font-mono text-meta text-muted">
          {r.resolved_at}
          {r.transfer_id && ` · transfer ${r.transfer_id}`}
        </p>
      </div>
    )
  }

  const disabled = !actor || resolve.isPending
  return (
    <div className="flex flex-col gap-2.5">
      <Button onClick={() => resolve.mutate({ decision: 'pay_vendor_on_file' })} disabled={disabled}>
        Pay {maskAccount(approval.account_on_file_display)}, the account on file
      </Button>
      {!confirming ? (
        <Button
          variant="quiet"
          className="!border-bad-line !bg-card !text-bad"
          onClick={() => setConfirming(true)}
          disabled={disabled}
        >
          Approve the new account…
        </Button>
      ) : (
        <form
          className="flex flex-col gap-2 rounded-[8px] border border-bad-line bg-bad-bg p-3"
          onSubmit={(e) => {
            e.preventDefault()
            resolve.mutate({ decision: 'approve_new_account', confirm_last4: last4 })
          }}
        >
          <label htmlFor="confirm-last4" className="text-small text-bad">
            Type the last 4 digits of {maskAccount(approval.requested_account_display)} to approve it
          </label>
          <div className="flex gap-2">
            <input
              id="confirm-last4"
              inputMode="numeric"
              maxLength={4}
              value={last4}
              onChange={(e) => setLast4(e.target.value.replace(/\D/g, ''))}
              className="w-[8ch] rounded-[6px] border border-bad-line bg-card px-2 font-mono text-fg"
            />
            <Button size="sm" type="submit" disabled={disabled || last4.length !== 4}>
              approve
            </Button>
            <Button size="sm" variant="quiet" onClick={() => setConfirming(false)}>
              cancel
            </Button>
          </div>
          {last4.length === 4 && last4 !== lastFour(approval.requested_account_display) && (
            <p className="m-0 text-meta text-bad">That doesn't match the requested account.</p>
          )}
        </form>
      )}
      <Button variant="quiet" className="!bg-card" onClick={() => resolve.mutate({ decision: 'reject' })} disabled={disabled}>
        Reject
      </Button>
      <p className="m-0 text-meta text-muted">
        Needs {/^[aeiou]/i.test(ROLE_LABEL[approval.required_role]) ? 'an' : 'a'} {ROLE_LABEL[approval.required_role]}.{' '}
        {requesterName} requested this payment, so {requesterName} can't approve it.
        {actor && <> Acting as {actor.name}.</>}
      </p>
      {sod ? (
        <div role="alert" data-testid="sod-banner" className="rounded-[8px] border border-bad-line bg-bad-bg px-3.5 py-2.5">
          <p className="m-0 font-bold text-bad">Segregation of duties: not allowed</p>
          <p className="m-0 text-small">{err.message}</p>
        </div>
      ) : (
        err && <RequestError title="Could not record the decision" error={err} />
      )}
    </div>
  )
}
