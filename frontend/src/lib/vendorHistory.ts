import type { Person, VendorDetail } from '@/api/types'

export interface VendorHistorySummary {
  accountOnFile: string
  /** Past payments to the account on file. */
  paidCount: number
  /** Earliest of those payments, YYYY-MM-DD. */
  paidSince: string | null
  verifiedAt: string | null
  /** Name from GET /api/people, else the person id as sent. */
  verifiedBy: string | null
}

/** Facts about the account on file, from GET /api/vendors/{id} (and /api/people for names). */
export function summarizeVendor(vendor: VendorDetail, people: readonly Person[] = []): VendorHistorySummary {
  const onFile = vendor.account_on_file_display
  const payments = vendor.payments.filter((p) => p.account_display === onFile)
  const since = payments.map((p) => p.date).sort()[0] ?? null
  const current =
    vendor.account_history.find((h) => h.account_display === onFile && h.valid_to === null) ??
    vendor.account_history.find((h) => h.account_display === onFile)
  const verifier = current ? (people.find((p) => p.person_id === current.verified_by)?.name ?? current.verified_by) : null
  return {
    accountOnFile: onFile,
    paidCount: payments.length,
    paidSince: since,
    verifiedAt: current?.verified_at ?? null,
    verifiedBy: verifier,
  }
}

const MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Mar 12" from an ISO date or timestamp, read as UTC, with no locale surprises. */
export function shortDate(iso: string, withYear = false): string {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return iso
  const month = MONTH[Number(m[2]) - 1] ?? m[2]
  return withYear ? `${month} ${m[1]}` : `${month} ${Number(m[3])}`
}

export function timesText(n: number): string {
  if (n === 0) return 'never'
  if (n === 1) return 'once'
  if (n === 2) return 'twice'
  return `${n} times`
}
