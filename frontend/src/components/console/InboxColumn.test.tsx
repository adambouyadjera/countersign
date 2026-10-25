import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { UseMutationResult } from '@tanstack/react-query'
import type { Batch, InvoiceSummary } from '@/api/types'
import type { InvoiceStatus } from '@/state/runReducer'
import { InboxColumn } from './InboxColumn'

const inv = (n: string, doc: string) =>
  ({ invoice_id: `i-${n}`, invoice_number: n, document_id: doc, vendor_id: 'VEN-001', vendor_name: 'Harbor Light Rentals', amount_cents: 125000, currency: 'USD', due_date: '2026-11-08' }) as InvoiceSummary
// The number format is whatever the backend sends (today INV-001, later INV-001-A1 …).
const batch = { batch_id: 'b', kind: 'attack', created_at: 'x', invoices: [inv('INV-001', 'd1'), inv('INV-001-A1', 'd2'), inv('INV-001-A2', 'd3')] } as Batch
const newBatch = { isError: false, isPending: false, mutate: vi.fn() } as unknown as UseMutationResult<Batch, Error, void>
const received = new Map([
  ['d1', '2026-10-25T00:58:00Z'],
  ['d2', '2026-10-25T01:58:00Z'],
  ['d3', '2026-10-25T02:58:00Z'],
])

function renderIt(props: Partial<Parameters<typeof InboxColumn>[0]> = {}) {
  const statuses: Record<string, InvoiceStatus> = { 'i-INV-001': 'blocked', 'i-INV-001-A1': 'paid', 'i-INV-001-A2': 'extracting' }
  return render(
    <InboxColumn
      batch={batch}
      newBatch={newBatch}
      statuses={statuses}
      selectedId={undefined}
      onSelect={() => {}}
      totals={null}
      receivedByDoc={received}
      attackDocs={new Set(['d2', 'd3'])}
      {...props}
    />,
  )
}

describe('<InboxColumn>', () => {
  it('lists newest first, tags Attack Lab submissions with the time received, and shows the backend numbers', () => {
    renderIt()
    const items = within(screen.getByRole('list', { name: 'Invoices in batch' })).getAllByRole('listitem')
    expect(items.map((li) => li.querySelector('button')!.textContent)).toEqual([
      expect.stringMatching(/^⚔INV-001-A2, processing\$1,250attack· \d{1,2}:\d{2}\s?[AP]M$/),
      expect.stringMatching(/^✓INV-001-A1, paid\$1,250attack· /),
      expect.stringMatching(/^✗INV-001, blocked\$1,250received /),
    ])
    expect(items[2]!.querySelector('[data-testid=attack-tag]')).toBeNull()
  })

  it('explains the marks in a legend and in each row tooltip', () => {
    renderIt()
    const legend = screen.getByRole('list', { name: 'What the marks mean' })
    expect(legend).toHaveTextContent('✓ paid on file')
    expect(legend).toHaveTextContent('✗ blocked, or paid an account not on file')
    expect(legend).toHaveTextContent('⚔ processing')
    // Only marks that are on screen.
    expect(legend).not.toHaveTextContent('held')
    expect(screen.getByRole('button', { name: /INV-001-A1/ })).toHaveAttribute('title', 'paid · Harbor Light Rentals · due 2026-11-08')
  })

  it('scrolls the selected invoice into view', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    // INV-001 is the oldest, so the last row: scrolled to.
    const { rerender } = renderIt({ selectedId: 'i-INV-001' })
    expect(scroll).toHaveBeenCalledWith({ block: 'nearest' })
    rerender(
      <InboxColumn batch={batch} newBatch={newBatch} statuses={null} selectedId="i-INV-001-A1" onSelect={() => {}} totals={null} />,
    )
    expect(scroll).toHaveBeenCalledTimes(2)
    // The newest (top) row scrolls the whole card to the top instead, keeping its label visible.
    const toTop = vi.fn()
    Element.prototype.scrollTo = toTop as unknown as typeof Element.prototype.scrollTo
    rerender(
      <InboxColumn
        batch={batch}
        newBatch={newBatch}
        statuses={null}
        selectedId="i-INV-001-A2"
        onSelect={() => {}}
        totals={null}
        receivedByDoc={received}
      />,
    )
    expect(toTop).toHaveBeenCalledWith({ top: 0 })
  })
})
