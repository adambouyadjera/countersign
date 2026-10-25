import type { VendorDetail } from '@/api/types'

/**
 * The backend seeds a January–June 2026 vendor archive (backend/docs/finance_employee.md).
 * Archived payments say so in their id: "example-history-*" journal references.
 */
export function isArchivedPayment(p: VendorDetail['payments'][number]): boolean {
  return p.journal_entry_id.startsWith('example-history-')
}

/** The seeded archive window. Account-history rows carry no archive flag, so this window is the only signal. */
export const ARCHIVE_START = '2026-01-01'
export const ARCHIVE_END = '2026-07-01'

const inArchive = (iso: string) => iso >= ARCHIVE_START && iso < ARCHIVE_END

/** An account-history row that started and was verified inside the seeded January–June 2026 archive. */
export function isArchivedAccount(a: VendorDetail['account_history'][number]): boolean {
  return inArchive(a.valid_from) && inArchive(a.verified_at)
}
