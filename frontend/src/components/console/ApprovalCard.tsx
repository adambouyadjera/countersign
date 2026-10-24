import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { resolveApproval } from '@/api/client'
import type { Approval, ApprovalDecision, InvoiceSummary, Person, PolicyDecision } from '@/api/types'
import { Card, InkButton } from '@/components/ui'
import { useApprovals, usePeople, useVendor } from '@/hooks/queries'
import { maskAccount } from '@/lib/account'
import { summarizeVendor, timesText } from '@/lib/vendorHistory'
import { RequestError } from './RequestError'

const ROLE: Record<Person['role'], string> = {
  ap_agent: 'AP agent',
  ap_manager: 'AP manager',
  controller: 'controller',
  vendor_admin: 'vendor admin',
}

const RESOLVED: Record<ApprovalDecision['decision'], string> = {
  pay_vendor_on_file: 'Paid the account on file',
  approve_new_account: 'Approved the new account',
  reject: 'Rejected',
}

export interface ApprovalCardProps {
  runId: string
  invoice: InvoiceSummary
  decision: PolicyDecision | undefined
}

/** Sig's note to the approver for a held invoice, with the two safe actions. */
export function ApprovalCard({ runId, invoice, decision }: ApprovalCardProps) {
  const approvals = useApprovals()
  const people = usePeople()
  const vendor = useVendor(invoice.vendor_id)
  const qc = useQueryClient()

  const approval: Approval | undefined = approvals.data?.find(
    (a) => a.run_id === runId && a.invoice_id === invoice.invoice_id,
  )
  const approver = approval && people.data?.find((p) => p.role === approval.required_role)

  const resolve = useMutation({
    mutationFn: (d: ApprovalDecision['decision']) =>
      resolveApproval(approval!.approval_id, { decision: d, actor_id: approver!.person_id }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['approvals'] }),
  })

  const caseHref = `/runs/${encodeURIComponent(runId)}/invoices/${encodeURIComponent(invoice.invoice_id)}`
  const failed = decision?.checks.filter((c) => !c.passed) ?? []
  const history = vendor.data ? summarizeVendor(vendor.data, people.data ?? []) : null

  return (
    <Card as="article" aria-label={`Approval for ${invoice.invoice_number}`} className="mt-auto flex flex-col gap-2.5 p-4">
      {approvals.isError ? (
        <RequestError
          title="Could not read approvals"
          error={approvals.error}
          onRetry={() => void approvals.refetch()}
          retrying={approvals.isFetching}
        />
      ) : approvals.isPending ? (
        <p className="m-0 text-small text-muted">Reading approvals…</p>
      ) : !approval ? (
        <p className="m-0 text-small text-muted">
          {invoice.invoice_number} is held; its approval has not been listed by GET /api/approvals yet.
        </p>
      ) : (
        <>
          <div className="text-small text-muted">
            Sig →{' '}
            {approver ? `${approver.name}, ${ROLE[approver.role]}` : ROLE[approval.required_role]}
          </div>
          <div>
            Held {invoice.invoice_number}. {failed.map((c) => c.message).join(' ')}
            {history &&
              history.accountOnFile === approval.account_on_file_display &&
              ` We've paid ${maskAccount(history.accountOnFile)} ${timesText(history.paidCount)}.`}
          </div>
          {approval.status === 'resolved' && approval.resolution ? (
            <div data-testid="approval-resolution" className="text-small">
              <span className="font-semibold">{RESOLVED[approval.resolution.decision]}</span>
              {approval.resolution.note && <span className="text-muted"> · “{approval.resolution.note}”</span>}
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            {approval.status === 'pending' && (
              <>
                <InkButton
                  onClick={() => resolve.mutate('pay_vendor_on_file')}
                  disabled={!approver || resolve.isPending}
                  title={!approver ? 'No approver with the required role (GET /api/people)' : undefined}
                >
                  Pay account on file
                </InkButton>
                <InkButton variant="secondary" onClick={() => resolve.mutate('reject')} disabled={!approver || resolve.isPending}>
                  Reject
                </InkButton>
              </>
            )}
            <Link to={caseHref} className="self-center text-accent">
              evidence trail
            </Link>
          </div>
          {resolve.isError && <RequestError title="Could not record the decision" error={resolve.error} />}
        </>
      )}
    </Card>
  )
}
