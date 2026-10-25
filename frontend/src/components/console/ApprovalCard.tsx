import { Link } from 'react-router-dom'
import type { Approval, InvoiceSummary, Person, PolicyDecision } from '@/api/types'
import { CountersignActions } from '@/components/casefile/CountersignActions'
import { Card } from '@/components/ui'
import { useApprovals, usePeople, useVendor } from '@/hooks/queries'
import { useActingAs } from '@/hooks/useActingAs'
import { maskAccount } from '@/lib/account'
import { summarizeVendor, timesText } from '@/lib/vendorHistory'
import { RequestError } from './RequestError'

const ROLE: Record<Person['role'], string> = {
  ap_agent: 'AP agent',
  ap_manager: 'AP manager',
  controller: 'controller',
  vendor_admin: 'vendor admin',
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
  const { person: actingAs } = useActingAs()

  const approval: Approval | undefined = approvals.data?.find(
    (a) => a.run_id === runId && a.invoice_id === invoice.invoice_id,
  )
  const approver = approval && people.data?.find((p) => p.role === approval.required_role)


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
          <CountersignActions approval={approval} people={people.data ?? []} defaultActorId={actingAs?.person_id} />
          <Link to={caseHref} className="self-start text-accent">
            evidence trail
          </Link>
        </>
      )}
    </Card>
  )
}
