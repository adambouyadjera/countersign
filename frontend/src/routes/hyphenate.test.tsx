import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import approvalsFixture from '@fixtures/approvals.json'
import peopleFixture from '@fixtures/people.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Approval, Person, VendorDetail } from '@/api/types'
import { CountersignActions } from '@/components/casefile/CountersignActions'
import { isArchivedAccount, isArchivedPayment } from '@/lib/vendorArchive'
import { VendorPage } from './VendorPage'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, getVendor: vi.fn(), listPeople: vi.fn(), resolveApproval: vi.fn() }
})

const m = vi.mocked(client)
const people = peopleFixture as Person[]

// The shape GET /api/vendors/VEN-003 returns from the real backend: a seeded archive plus one real payment.
const vendor: VendorDetail = {
  vendor_id: 'VEN-003',
  name: 'Golden Gate Sanitation',
  account_on_file_display: '300300003333',
  account_history: [
    { account_display: '300300003333', valid_from: '2026-01-01T00:00:00Z', valid_to: null, verified_by: 'person-vendor-admin', verified_at: '2026-03-12T18:00:00Z' },
  ],
  payments: [
    { date: '2026-01-15', amount_cents: 64000, account_display: '300300003333', journal_entry_id: 'example-history-VEN-003-2026-01' },
    { date: '2026-10-24', amount_cents: 64000, account_display: '300300003333', journal_entry_id: 'je-real-1' },
  ],
}

function renderAt(ui: ReactNode, path = '/vendors/VEN-003') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/vendors/:vendorId" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  m.listPeople.mockResolvedValue(people)
})
afterEach(() => vi.clearAllMocks())

describe('<VendorPage>', () => {
  it('shows the account on file, who verified each account and when, and past payments', async () => {
    m.getVendor.mockResolvedValue(vendor)
    renderAt(<VendorPage />)
    expect(await screen.findByTestId('account-on-file')).toHaveTextContent('300300003333')
    expect(m.getVendor).toHaveBeenCalledWith('VEN-003')
    const history = screen.getByRole('region', { name: 'Account history' })
    expect(await within(history).findByTestId('verified')).toHaveTextContent('verified by Morgan Diaz · vendor admin · 2026-03-12T18:00:00Z')
    expect(within(history).getByTestId('demo-history')).toBeInTheDocument()
    const payments = screen.getByRole('region', { name: 'Past payments' })
    const archived = payments.querySelector('[data-payment="example-history-VEN-003-2026-01"]')!
    const real = payments.querySelector('[data-payment="je-real-1"]')!
    expect(within(archived as HTMLElement).getByTestId('demo-history')).toHaveTextContent('demo history')
    expect(within(real as HTMLElement).queryByTestId('demo-history')).toBeNull()
  })

  it('says why when the vendor is unknown', async () => {
    m.getVendor.mockRejectedValue(new ApiError({ status: 404, code: 'not_found', message: 'Unknown vendor', envelope: true }))
    renderAt(<VendorPage />, '/vendors/VEN-404')
    expect(await screen.findByTestId('not-available')).toHaveTextContent('GET /api/vendors/VEN-404 → 404 not_found')
  })
})

describe('vendor archive tags', () => {
  it('tags only the seeded January–June 2026 archive', () => {
    expect(isArchivedPayment(vendor.payments[0]!)).toBe(true)
    expect(isArchivedPayment(vendor.payments[1]!)).toBe(false)
    expect(isArchivedAccount(vendor.account_history[0]!)).toBe(true)
    expect(isArchivedAccount({ ...vendor.account_history[0]!, valid_from: '2026-10-24T00:00:00Z', verified_at: '2026-10-24T00:00:00Z' })).toBe(false)
    expect(isArchivedAccount({ ...vendor.account_history[0]!, valid_from: '2025-01-01T00:00:00Z', verified_at: '2025-01-01T00:00:00Z' })).toBe(false)
  })
})

describe('<CountersignActions>', () => {
  const base = { ...(approvalsFixture as Approval[])[0]!, status: 'pending' as const, resolution: undefined }

  it('shows an OVER_AUTONOMY_LIMIT hold with the agent limit from GET /api/people', () => {
    const a: Approval = { ...base, reason_codes: ['OVER_AUTONOMY_LIMIT'], amount_cents: 3_000_000 }
    const withLimit = people.map((p) => (p.role === 'ap_agent' ? { ...p, approval_limit_cents: 2_500_000 } : p))
    renderAt(<CountersignActions approval={a} people={withLimit} />)
    expect(screen.getByTestId('over-limit')).toHaveTextContent("Over the AP agent's autonomy limit of $25,000.00: $30,000.00")
  })

  it('offers only the AP manager, controller and vendor admin to act as', () => {
    renderAt(<CountersignActions approval={base} people={people} />)
    const options = within(screen.getByRole('combobox', { name: 'Act as' })).getAllByRole('option').map((o) => o.getAttribute('value'))
    expect(options).toEqual(['person-ap-manager', 'person-controller', 'person-vendor-admin'])
    expect(screen.queryByTestId('over-limit')).toBeNull()
  })
})
