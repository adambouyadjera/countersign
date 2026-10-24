import { Link, useSearchParams } from 'react-router-dom'
import { SigPlaceholder } from '@/components/sig'

export function AttackLabPage() {
  const [params] = useSearchParams()
  const draft = params.get('draft')
  return (
    <SigPlaceholder
      title="Attack lab"
      expression="cheeky"
      what="Soon you'll write an invoice here and watch both agents read it."
    >
      {draft && (
        <section aria-label="Your draft" className="flex flex-col gap-1.5">
          <div className="eyebrow">your draft, kept for the lab</div>
          <textarea
            readOnly
            value={draft}
            rows={4}
            className="w-full resize-y rounded-[8px] border border-line bg-card p-3 font-mono text-fg"
          />
        </section>
      )}
    </SigPlaceholder>
  )
}

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
