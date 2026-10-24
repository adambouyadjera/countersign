import { Link } from 'react-router-dom'
import { SigPlaceholder } from '@/components/sig'

export function BooksPage() {
  return (
    <SigPlaceholder
      title="Books"
      expression="pleased"
      what="Journal entries, the trial balance and the bank reconciliation will live here."
    />
  )
}

export function ProofPage() {
  return (
    <SigPlaceholder
      title="Proof"
      expression="honest"
      what="The planted-errors eval and the red team results will be here, misses included."
    />
  )
}

export function AuditPage() {
  return (
    <SigPlaceholder
      title="Audit"
      expression="calm"
      what="You'll ask the audit agent about the event log here, and see the queries it ran."
    />
  )
}

export function NotFoundPage() {
  return (
    <SigPlaceholder title="Not found" expression="honest" what="There's no page at this address.">
      <Link to="/" className="text-accent">
        back to the console
      </Link>
    </SigPlaceholder>
  )
}
