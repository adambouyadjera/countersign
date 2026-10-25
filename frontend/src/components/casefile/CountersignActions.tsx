import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { resolveApproval } from '@/api/client'
import { isApiError } from '@/api/errors'
import type { Approval, ApprovalDecision, Person } from '@/api/types'
import { RequestError } from '@/components/console/RequestError'
import { InkButton, InkRow } from '@/components/ui'
import { lastFour, maskAccount } from '@/lib/account'
import { formatCents } from '@/lib/money'
import { ROLE_LABEL } from '@/lib/roles'

const RESOLVED: Record<ApprovalDecision['decision'], string> = {
  pay_vendor_on_file: 'Paid the account on file',
  approve_new_account: 'Approved the new account',
  reject: 'Rejected',
}

/** People who can be picked to act on an approval. The server decides who may; this only offers the humans. */
const ACTOR_ROLES: readonly Person['role'][] = ['ap_manager', 'controller', 'vendor_admin']

export interface CountersignActionsProps {
  approval: Approval
  people: readonly Person[]
  /** Preselected actor (e.g. the header's "acting as"); used only if it has one of the actor roles. */
  defaultActorId?: string
}

/**
 * POST /api/approvals/{id} as a picked person: pay the account on file, approve the new
 * account (with its last 4), or reject. The server enforces segregation of duties and
 * its 403 message (naming who can approve instead) is shown as-is.
 */
export function CountersignActions({ approval, people, defaultActorId }: CountersignActionsProps) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [last4, setLast4] = useState('')
  const actors = people.filter((p) => ACTOR_ROLES.includes(p.role))
  const [actorId, setActorId] = useState<string | null>(null)
  const actor =
    actors.find((p) => p.person_id === actorId) ??
    actors.find((p) => p.person_id === defaultActorId) ??
    actors.find((p) => p.role === 'ap_manager') ??
    actors[0]
  const agentLimit = people.find((p) => p.role === 'ap_agent')?.approval_limit_cents
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
      {approval.reason_codes.includes('OVER_AUTONOMY_LIMIT') && (
        <p data-testid="over-limit" className="m-0 text-small font-semibold text-bad">
          Over the AP agent&apos;s autonomy limit of{' '}
          {agentLimit === undefined ? '—' : agentLimit === null ? 'no limit' : formatCents(agentLimit)}: {formatCents(approval.amount_cents)}
        </p>
      )}
      <label className="flex flex-wrap items-center gap-2 text-small">
        <span className="text-muted">act as</span>
        <select
          aria-label="Act as"
          value={actor?.person_id ?? ''}
          onChange={(e) => {
            setActorId(e.target.value)
            resolve.reset()
          }}
          disabled={actors.length === 0}
          className="ink-field px-2 py-1 font-[inherit] font-bold text-fg"
        >
          {actors.length === 0 && <option value="">— (GET /api/people has nobody to act as)</option>}
          {actors.map((p) => (
            <option key={p.person_id} value={p.person_id}>
              {p.name} · {ROLE_LABEL[p.role]}
            </option>
          ))}
        </select>
      </label>
      <InkButton onClick={() => resolve.mutate({ decision: 'pay_vendor_on_file' })} disabled={disabled}>
        Pay {maskAccount(approval.account_on_file_display)}, the account on file
      </InkButton>
      {!confirming ? (
        <InkButton
          variant="danger"
          onClick={() => setConfirming(true)}
          disabled={disabled}
        >
          Approve the new account…
        </InkButton>
      ) : (
        <InkRow
          as="form"
          tone="untrusted"
          className="flex flex-col gap-2 p-3"
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
              className="ink-field w-[8ch] px-2 font-mono text-fg"
            />
            <InkButton size="sm" type="submit" disabled={disabled || last4.length !== 4}>
              approve
            </InkButton>
            <InkButton size="sm" variant="secondary" onClick={() => setConfirming(false)}>
              cancel
            </InkButton>
          </div>
          {last4.length === 4 && last4 !== lastFour(approval.requested_account_display) && (
            <p className="m-0 text-meta text-bad">That doesn't match the requested account.</p>
          )}
        </InkRow>
      )}
      <InkButton variant="secondary" onClick={() => resolve.mutate({ decision: 'reject' })} disabled={disabled}>
        Reject
      </InkButton>
      <p className="m-0 text-meta text-muted">
        Needs {/^[aeiou]/i.test(ROLE_LABEL[approval.required_role]) ? 'an' : 'a'} {ROLE_LABEL[approval.required_role]}.{' '}
        {requesterName} requested this payment, so {requesterName} can't approve it.
      </p>
      {sod ? (
        <InkRow tone="failed" role="alert" data-testid="sod-banner" className="px-3.5 py-2.5">
          <p className="m-0 font-bold text-bad">Segregation of duties: not allowed</p>
          <p className="m-0 text-small">{err.message}</p>
        </InkRow>
      ) : (
        err && <RequestError title="Could not record the decision" error={err} />
      )}
    </div>
  )
}
