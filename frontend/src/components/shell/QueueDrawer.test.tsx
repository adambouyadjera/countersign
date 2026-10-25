import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import approvalsFixture from '@fixtures/approvals.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Approval } from '@/api/types'
import { QueueDrawer } from './QueueDrawer'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, listApprovals: vi.fn(), listPeople: vi.fn() }
})

const m = vi.mocked(client)
const [resolved] = approvalsFixture as Approval[]
const pending: Approval = { ...resolved!, status: 'pending', resolution: undefined }

function renderDrawer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/', element: <QueueDrawer /> },
      { path: '/runs/:runId/invoices/:invoiceId', element: <p>case file</p> },
    ],
    { initialEntries: ['/'] },
  )
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

afterEach(() => vi.clearAllMocks())

describe('<QueueDrawer>', () => {
  it('asks for pending approvals and shows the count on the badge', async () => {
    m.listApprovals.mockResolvedValue([pending, { ...pending, approval_id: 'a2', invoice_id: 'INV-005', invoice_number: 'INV-005' }])
    renderDrawer()
    expect(await screen.findByRole('button', { name: 'Countersign queue: 2 pending' })).toBeInTheDocument()
    expect(screen.getByTestId('queue-count')).toHaveTextContent('2')
    expect(m.listApprovals).toHaveBeenCalledWith('pending')
  })

  it('lists each pending approval and links to its case file', async () => {
    m.listApprovals.mockResolvedValue([pending])
    const user = userEvent.setup()
    const router = renderDrawer()
    await user.click(await screen.findByRole('button', { name: /Countersign queue/ }))
    const dialog = screen.getByRole('dialog', { name: 'Countersign queue' })
    const item = within(dialog).getByRole('listitem')
    expect(item).toHaveTextContent('asks for •••• 9999 · on file •••• 3333')
    expect(item).toHaveTextContent('BANK_DETAILS_MISMATCH · needs AP manager')
    await user.click(within(item).getByRole('link', { name: 'INV-003 · Cedar Grove Catering · $640.00' }))
    expect(router.state.location.pathname).toBe('/runs/run-example-both/invoices/INV-003')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('ignores resolved approvals even if the server sends them, and closes on Escape', async () => {
    m.listApprovals.mockResolvedValue([resolved!])
    const user = userEvent.setup()
    renderDrawer()
    expect(await screen.findByRole('button', { name: 'Countersign queue: 0 pending' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Countersign queue/ }))
    expect(screen.getByText('Nothing waiting for a countersign.')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says "not available yet" on a 404', async () => {
    m.listApprovals.mockRejectedValue(new ApiError({ status: 404, code: 'not_found', message: 'nope', envelope: true }))
    const user = userEvent.setup()
    renderDrawer()
    expect(await screen.findByRole('button', { name: 'Countersign queue: unavailable' })).toBeInTheDocument()
    expect(screen.getByTestId('queue-count')).toHaveTextContent('?')
    await user.click(screen.getByRole('button', { name: /Countersign queue/ }))
    expect(screen.getByTestId('not-available')).toHaveTextContent('GET /api/approvals?status=pending → 404 not_found')
  })

  it('lists newest first with each card\'s actions collapsed until expanded', async () => {
    const older: Approval = { ...pending, approval_id: 'a-old', invoice_number: 'INV-OLD', created_at: '2026-10-24T01:00:00Z' }
    const newer: Approval = { ...pending, approval_id: 'a-new', invoice_number: 'INV-NEW', created_at: '2026-10-25T01:00:00Z' }
    m.listApprovals.mockResolvedValue([older, newer])
    m.listPeople.mockResolvedValue([])
    const user = userEvent.setup()
    renderDrawer()
    await user.click(await screen.findByRole('button', { name: 'Countersign queue: 2 pending' }))
    const list = screen.getByRole('list', { name: 'Pending approvals' })
    const cards = within(list).getAllByRole('listitem')
    expect(cards[0]).toHaveTextContent('INV-NEW')
    expect(cards[1]).toHaveTextContent('INV-OLD')
    expect(within(list).queryByRole('combobox', { name: 'Act as' })).toBeNull()
    const decide = within(cards[0]!).getByRole('button', { name: 'Decide…' })
    expect(decide).toHaveAttribute('aria-expanded', 'false')
    await user.click(decide)
    expect(within(cards[0]!).getByRole('combobox', { name: 'Act as' })).toBeInTheDocument()
    expect(within(cards[1]!).queryByRole('combobox', { name: 'Act as' })).toBeNull()
  })
})
