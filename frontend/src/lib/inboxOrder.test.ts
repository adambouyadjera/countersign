import { describe, expect, it } from 'vitest'
import type { InvoiceSummary } from '@/api/types'
import { inboxRows, receivedTime } from './inboxOrder'

const inv = (n: string, doc: string) => ({ invoice_id: `i-${n}`, invoice_number: n, document_id: doc }) as InvoiceSummary

describe('inboxRows', () => {
  it('puts the newest received first and leaves never-received invoices in batch order after them', () => {
    const invoices = [inv('INV-001', 'd1'), inv('INV-001-A1', 'd2'), inv('X', 'd3'), inv('INV-001-A2', 'd4'), inv('Y', 'd5')]
    const received = new Map([
      ['d1', '2026-10-25T01:00:00Z'],
      ['d2', '2026-10-25T02:00:00Z'],
      ['d4', '2026-10-25T03:00:00Z'],
    ])
    expect(inboxRows(invoices, received).map((r) => r.invoice.invoice_number)).toEqual(['INV-001-A2', 'INV-001-A1', 'INV-001', 'X', 'Y'])
  })

  it('keeps a drawn batch in its own order', () => {
    const invoices = [inv('INV-001', 'a'), inv('INV-002', 'b'), inv('INV-003', 'c')]
    expect(inboxRows(invoices, new Map()).map((r) => r.invoice.invoice_number)).toEqual(['INV-001', 'INV-002', 'INV-003'])
  })
})

describe('receivedTime', () => {
  it('formats a time like "7:58 PM"', () => {
    expect(receivedTime('2026-10-25T02:58:00Z')).toMatch(/^\d{1,2}:\d{2}\s?[AP]M$/)
  })
})
