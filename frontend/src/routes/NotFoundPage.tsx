import { Link } from 'react-router-dom'
import { EmptyState } from '@/components/ui'

export function NotFoundPage() {
  return (
    <EmptyState
      className="py-24"
      title="No such folio"
      body="This page is not in the ledger."
      action={
        <Link to="/" className="text-sm text-ink underline underline-offset-4">
          Return to the console
        </Link>
      }
    />
  )
}
