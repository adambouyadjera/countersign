import type { InvoiceSummary } from '@/api/types'

export interface InboxRow {
  invoice: InvoiceSummary
  /** When the AP inbox received its document (GET /api/inbox), if it came through the inbox. */
  receivedAt?: string
}

/**
 * Newest first by the time the inbox received each document. Invoices that never came
 * through the inbox (a drawn batch) keep their batch order, after the received ones.
 */
export function inboxRows(invoices: readonly InvoiceSummary[], receivedByDoc: ReadonlyMap<string, string>): InboxRow[] {
  const rows = invoices.map((invoice, index) => ({ invoice, index, receivedAt: receivedByDoc.get(invoice.document_id) }))
  rows.sort((a, b) => {
    if (a.receivedAt && b.receivedAt) return b.receivedAt.localeCompare(a.receivedAt) || b.index - a.index
    if (a.receivedAt) return -1
    if (b.receivedAt) return 1
    return a.index - b.index
  })
  return rows.map(({ invoice, receivedAt }) => ({ invoice, receivedAt }))
}

const clock = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' })

/** "7:58 PM" in the viewer's time zone. */
export function receivedTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : clock.format(d)
}
