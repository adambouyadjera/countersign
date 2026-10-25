import { Link } from 'react-router-dom'
import { isApiError } from '@/api/errors'
import { usePeople, useVendor } from '@/hooks/queries'
import { maskAccount } from '@/lib/account'
import { shortDate, summarizeVendor } from '@/lib/vendorHistory'

/** "on file •••• 3333 · paid 1× since Sep 2026 · verified Feb 1 by Morgan Diaz", from GET /api/vendors/{id}. */
export function VendorHistoryLine({ vendorId }: { vendorId: string }) {
  const vendor = useVendor(vendorId)
  const people = usePeople()
  if (vendor.isPending) return <span className="text-muted">reading the vendor record…</span>
  if (vendor.isError) {
    return (
      <span className="text-muted" data-testid="vendor-history-unavailable">
        vendor history unavailable · GET /api/vendors/{vendorId}
        {isApiError(vendor.error) ? ` → ${vendor.error.code}` : ''}
      </span>
    )
  }
  const s = summarizeVendor(vendor.data, people.data ?? [])
  return (
    <span data-testid="vendor-history">
      on file {maskAccount(s.accountOnFile)} ·{' '}
      {s.paidCount === 0 ? 'never paid' : `paid ${s.paidCount}× since ${shortDate(s.paidSince!, true)}`}
      {s.verifiedAt && ` · verified ${shortDate(s.verifiedAt)}${s.verifiedBy ? ` by ${s.verifiedBy}` : ''}`} ·{' '}
      <Link to={`/vendors/${encodeURIComponent(vendorId)}`} className="text-accent">
        vendor record
      </Link>
    </span>
  )
}
