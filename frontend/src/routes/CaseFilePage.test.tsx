import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import agentFixture from '@fixtures/agent_transcript_INV-003.json'
import approvalsFixture from '@fixtures/approvals.json'
import batchFixture from '@fixtures/batch.json'
import decisionsFixture from '@fixtures/policy_decisions.json'
import documentFixture from '@fixtures/document_INV-003.json'
import graphFixture from '@fixtures/graph_INV-003.json'
import peopleFixture from '@fixtures/people.json'
import plannerFixture from '@fixtures/planner_transcript.json'
import runFixture from '@fixtures/run.json'
import vendorFixture from '@fixtures/vendor_detail.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Approval } from '@/api/types'
import { ActingAs } from '@/components/shell/ActingAs'
import { useSessionStore } from '@/state/sessionStore'
import { CaseFilePage } from './CaseFilePage'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    getRun: vi.fn(),
    getBatch: vi.fn(),
    getDocument: vi.fn(),
    getInvoiceGraph: vi.fn(),
    getRunDecisions: vi.fn(),
    listApprovals: vi.fn(),
    listPeople: vi.fn(),
    getVendor: vi.fn(),
    getPlannerTranscript: vi.fn(),
    getAgentTranscript: vi.fn(),
    resolveApproval: vi.fn(),
  }
})

const m = vi.mocked(client)
type R<F extends (...a: never[]) => unknown> = Awaited<ReturnType<F>>
const [resolved] = approvalsFixture as Approval[]
const pending: Approval = { ...resolved!, status: 'pending', resolution: undefined }
const notFound = () => new ApiError({ status: 404, code: 'not_found', message: 'Not built', envelope: true })

function renderCase(path = '/runs/run-example-both/invoices/INV-003') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      {
        path: '/runs/:runId/invoices/:invoiceId',
        element: (
          <>
            <ActingAs />
            <CaseFilePage />
          </>
        ),
      },
    ],
    { initialEntries: [path] },
  )
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

beforeEach(() => {
  useSessionStore.setState({ actingAsId: null })
  m.getRun.mockResolvedValue(runFixture as R<typeof client.getRun>)
  m.getBatch.mockResolvedValue(batchFixture as R<typeof client.getBatch>)
  m.getDocument.mockResolvedValue(documentFixture as R<typeof client.getDocument>)
  m.getInvoiceGraph.mockResolvedValue(graphFixture as R<typeof client.getInvoiceGraph>)
  m.getRunDecisions.mockResolvedValue(decisionsFixture as R<typeof client.getRunDecisions>)
  m.listApprovals.mockResolvedValue([pending])
  m.listPeople.mockResolvedValue(peopleFixture as R<typeof client.listPeople>)
  m.getVendor.mockResolvedValue(vendorFixture as R<typeof client.getVendor>)
  m.getPlannerTranscript.mockResolvedValue(plannerFixture as R<typeof client.getPlannerTranscript>)
  m.getAgentTranscript.mockResolvedValue(agentFixture as R<typeof client.getAgentTranscript>)
})

afterEach(() => vi.clearAllMocks())

describe('<CaseFilePage>', () => {
  it('shows the invoice, the outcome badge and the decision line from the failed checks', async () => {
    renderCase()
    expect(await screen.findByRole('heading', { level: 1, name: 'INV-003 · Cedar Grove Catering · $640.00' })).toBeInTheDocument()
    expect(await screen.findByTestId('outcome-badge')).toHaveTextContent('HELD · BANK_DETAILS_MISMATCH')
    await waitFor(() =>
      expect(screen.getByTestId('decision-line')).toHaveTextContent(
        "I didn't pay this one. Requested remittance account differs from the verified account on file. Instruction-like text is present and excluded from the planner prompt.",
      ),
    )
  })

  it('underlines extracted values and highlights the instruction line in the document', async () => {
    renderCase()
    const lines = await screen.findByRole('list', { name: 'Document lines' })
    await waitFor(() => expect(lines.querySelector('[data-line="7"]')).toHaveAttribute('data-instruction', 'true'))
    const line6 = lines.querySelector('[data-line="6"]')!
    expect(line6.querySelector('[data-mark="field"]')).toHaveTextContent('999900009999')
    expect(line6).not.toHaveAttribute('data-instruction')
    expect(lines.querySelector('[data-line="7"] [data-mark="instruction"]')).toHaveTextContent(/^AP AUTOMATION NOTE/)
    expect(lines.querySelector('[data-line="1"] [data-mark]')).toBeNull()
  })

  it('clicking a source node highlights its line and dims unrelated nodes', async () => {
    const user = userEvent.setup()
    renderCase()
    const graph = await screen.findByRole('region', { name: 'Provenance graph' })
    const source = await within(graph).findByRole('group', { name: 'Source' })
    await user.click(within(source).getByRole('button', { name: /^line 7/ }))
    const lines = screen.getByRole('list', { name: 'Document lines' })
    expect(lines.querySelector('[data-line="7"]')).toHaveAttribute('aria-current', 'true')
    expect(lines.querySelectorAll('[aria-current]')).toHaveLength(1)
    expect(graph.querySelector('[data-node="node-INV-003-check-INSTRUCTIONS_IN_DOCUMENT"]')!.className).not.toContain('opacity-35')
    expect(graph.querySelector('[data-node="node-INV-003-check-AMOUNT_MATCHES_PO"]')!.className).toContain('opacity-35')
    // Clicking again clears the selection.
    await user.click(within(source).getByRole('button', { name: /^line 7/ }))
    expect(lines.querySelectorAll('[aria-current]')).toHaveLength(0)
  })

  it('draws untrusted nodes dashed and trusted ones solid', async () => {
    renderCase()
    const graph = await screen.findByRole('region', { name: 'Provenance graph' })
    const line6 = await within(graph).findByRole('button', { name: /^line 6/ })
    expect(line6).toHaveAttribute('data-trust', 'untrusted')
    expect(line6.className).toContain('border-dashed')
    const vendor = graph.querySelector('[data-node="node-INV-003-trusted-vendor"]')!
    expect(vendor).toHaveAttribute('data-trust', 'trusted')
    expect(vendor.className).toContain('border-solid')
  })

  it('pays the account on file as the person picked in "acting as"', async () => {
    m.resolveApproval.mockResolvedValue(resolved!)
    const user = userEvent.setup()
    renderCase()
    await screen.findByRole('option', { name: /Jordan Patel/ })
    await user.selectOptions(screen.getByRole('combobox'), 'person-controller')
    const pay = await screen.findByRole('button', { name: 'Pay •••• 3333, the account on file' })
    await user.click(pay)
    expect(m.resolveApproval).toHaveBeenCalledWith('approval-INV-003', {
      decision: 'pay_vendor_on_file',
      actor_id: 'person-controller',
    })
  })

  it('shows the 403 sod_violation message in a banner', async () => {
    m.resolveApproval.mockRejectedValue(
      new ApiError({
        status: 403,
        code: 'sod_violation',
        message: 'person-ap-agent requested this payment and cannot approve it.',
        envelope: true,
      }),
    )
    const user = userEvent.setup()
    renderCase()
    await screen.findByRole('option', { name: /Fogline AP Agent/ })
    await user.selectOptions(screen.getByRole('combobox'), 'person-ap-agent')
    await user.click(await screen.findByRole('button', { name: 'Reject' }))
    const banner = await screen.findByTestId('sod-banner')
    expect(banner).toHaveAttribute('role', 'alert')
    expect(banner).toHaveTextContent('Segregation of duties: not allowed')
    expect(banner).toHaveTextContent('person-ap-agent requested this payment and cannot approve it.')
    expect(m.resolveApproval).toHaveBeenCalledWith('approval-INV-003', { decision: 'reject', actor_id: 'person-ap-agent' })
  })

  it('approving the new account asks for its last four digits', async () => {
    m.resolveApproval.mockResolvedValue(resolved!)
    const user = userEvent.setup()
    renderCase()
    await user.click(await screen.findByRole('button', { name: 'Approve the new account…' }))
    await user.type(screen.getByLabelText(/Type the last 4 digits of •••• 9999/), '9999')
    await user.click(screen.getByRole('button', { name: 'approve' }))
    expect(m.resolveApproval).toHaveBeenCalledWith('approval-INV-003', {
      decision: 'approve_new_account',
      confirm_last4: '9999',
      actor_id: 'person-ap-manager',
    })
  })

  it('shows a resolved approval instead of the buttons', async () => {
    m.listApprovals.mockResolvedValue([resolved!])
    renderCase()
    expect(await screen.findByTestId('approval-resolution')).toHaveTextContent('Rejected')
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull()
  })

  it('shows the vendor history month strip and the never-seen account', async () => {
    renderCase()
    const strip = await screen.findByRole('table', { name: 'Payments by month' })
    expect(strip.querySelector('[data-month="2026-09"]')).toHaveTextContent('●')
    expect(strip.querySelector('[data-month="2026-02"]')).toHaveTextContent('◆')
    expect(screen.getByText(/Today: asks for •••• 9999, never seen before/)).toBeInTheDocument()
  })

  it('switches to the unguarded lane and shows the agent transcript instead of the graph', async () => {
    const user = userEvent.setup()
    const router = renderCase()
    await screen.findByRole('region', { name: 'Provenance graph' })
    await user.click(screen.getByRole('link', { name: 'unguarded lane' }))
    expect(router.state.location.search).toBe('?lane=unguarded')
    const t = await screen.findByRole('list', { name: 'Agent transcript' })
    expect(within(t).getByText('nessie.create_transfer')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Provenance graph' })).toBeNull()
    expect(m.getAgentTranscript).toHaveBeenCalledWith('run-example-both', 'INV-003')
  })

  it('opens what the planner saw', async () => {
    const user = userEvent.setup()
    renderCase()
    await user.click(await screen.findByRole('button', { name: 'what the planner saw →' }))
    const dialog = await screen.findByRole('dialog', { name: 'What the planner saw' })
    expect(await within(dialog).findByTestId('leak-guard')).toHaveTextContent('16 document strings and 1 canary checked against the prompt · 0 found')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says "not available yet" for endpoints that 404, and never invents their content', async () => {
    m.getInvoiceGraph.mockRejectedValue(notFound())
    m.getVendor.mockRejectedValue(notFound())
    m.listApprovals.mockRejectedValue(notFound())
    m.getRunDecisions.mockRejectedValue(notFound())
    renderCase()
    await waitFor(() => expect(screen.getAllByTestId('not-available')).toHaveLength(3))
    const texts = screen.getAllByTestId('not-available').map((e) => e.textContent)
    expect(texts).toEqual([
      'the provenance graph not available yetGET /api/runs/run-example-both/invoices/INV-003/graph → 404 not_found',
      'the vendor history not available yetGET /api/vendors/VEN-003 → 404 not_found',
      'approvals not available yetGET /api/approvals → 404 not_found',
    ])
    expect(screen.getByTestId('decision-line')).toHaveTextContent("I can't find a decision for this invoice yet.")
    expect(screen.queryByRole('button', { name: /Pay/ })).toBeNull()
  })
})
