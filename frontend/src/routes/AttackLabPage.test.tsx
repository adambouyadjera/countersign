import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import autopilotFixture from '@fixtures/autopilot.json'
import batchFixture from '@fixtures/batch.json'
import runFixture from '@fixtures/run.json'
import vendorFixture from '@fixtures/vendor_detail.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import type { Batch, Run } from '@/api/types'
import { useConsoleStore } from '@/state/consoleStore'
import { AttackLabPage } from './AttackLabPage'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    createAttack: vi.fn(),
    createBatch: vi.fn(),
    createRun: vi.fn(),
    getRun: vi.fn(),
    getBatch: vi.fn(),
    getAutopilot: vi.fn(),
    listInboxItems: vi.fn(),
    getVendor: vi.fn(),
  }
})

const m = vi.mocked(client)
const batch = batchFixture as Batch
const attackBatch: Batch = { ...batch, batch_id: 'batch-attack-1', kind: 'attack' }
const run = runFixture as Run
// Neutral filler: the tests check plumbing, not wording.
const TEXT = 'neutral test text'

function renderLab(path = '/attack') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/attack', element: <AttackLabPage /> },
      { path: '/console', element: <p>console</p> },
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
  useConsoleStore.setState({ batch, run: null, runNonce: 0, selectedInvoiceId: null, focusedInvoiceId: null })
  m.getAutopilot.mockResolvedValue({ ...autopilotFixture, enabled: false } as Awaited<ReturnType<typeof client.getAutopilot>>)
  m.getVendor.mockResolvedValue(vendorFixture as Awaited<ReturnType<typeof client.getVendor>>)
  m.createAttack.mockResolvedValue(attackBatch)
  m.createRun.mockResolvedValue(run)
  m.createBatch.mockResolvedValue(batch)
})

afterEach(() => vi.clearAllMocks())

describe('<AttackLabPage>', () => {
  it('lists one option per vendor in the batch and prefills the draft from the console', async () => {
    renderLab(`/attack?draft=${encodeURIComponent(TEXT)}`)
    const select = screen.getByLabelText('Pretend to be')
    expect(within(select).getAllByRole('option')).toHaveLength(5)
    expect(within(select).getByRole('option', { name: 'Cedar Grove Catering · INV-003 · $640.00' })).toBeInTheDocument()
    expect(screen.getByLabelText('Your text')).toHaveValue(TEXT)
    expect(screen.getByText(`${TEXT.length} / 600`)).toBeInTheDocument()
    expect(screen.getByLabelText(/White text/)).toBeChecked()
  })

  it('offers to draw a batch when there are no vendors yet', async () => {
    useConsoleStore.setState({ batch: null })
    const user = userEvent.setup()
    renderLab()
    await user.click(screen.getByRole('button', { name: 'draw a batch' }))
    expect(await screen.findByLabelText('Pretend to be')).toBeInTheDocument()
    expect(m.createBatch).toHaveBeenCalledWith({ kind: 'standard' })
  })

  it('inserts the attacker-account token at the caret', async () => {
    const user = userEvent.setup()
    renderLab()
    const box = screen.getByLabelText('Your text')
    await user.type(box, 'ab')
    await user.click(screen.getByRole('button', { name: '+ attacker account' }))
    expect(box).toHaveValue('ab{{ATTACKER_ACCOUNT}}')
  })

  it('blocks empty and over-long text', async () => {
    const user = userEvent.setup()
    renderLab()
    const send = screen.getByRole('button', { name: 'Send it to the AP inbox' })
    expect(send).toBeDisabled()
    await user.click(screen.getByLabelText('Your text'))
    await user.paste('x'.repeat(601))
    expect(send).toBeDisabled()
    expect(screen.getByText('Too long: 601 of 600 characters.')).toBeInTheDocument()
  })

  it('autopilot off: sends the attack, starts the run itself and opens the console on it', async () => {
    const user = userEvent.setup()
    const router = renderLab()
    await screen.findByText('Autopilot is off, so sending also starts the run')
    await user.selectOptions(screen.getByLabelText('Pretend to be'), 'VEN-005')
    await user.click(screen.getByLabelText(/Footer/))
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/console'))
    expect(m.createAttack).toHaveBeenCalledWith({ vendor_id: 'VEN-005', attack_text: TEXT, placement: 'footer' })
    expect(m.createRun).toHaveBeenCalledWith({ batch_id: 'batch-attack-1', lanes: ['unguarded', 'countersign'] })
    expect(useConsoleStore.getState().batch?.batch_id).toBe('batch-attack-1')
    expect(useConsoleStore.getState().run?.run_id).toBe(run.run_id)
  })

  it('autopilot on: does not start a run, waits for the inbox item to name one, then opens it', async () => {
    m.getAutopilot.mockResolvedValue({ enabled: true, lanes: ['unguarded', 'countersign'], run_id: null, last_checked_at: null })
    const doc = attackBatch.invoices[0]!.document_id
    m.listInboxItems
      .mockResolvedValueOnce([{ item_id: 'inbox-9', document_id: doc, sender: 'x', received_at: '2026-10-24T00:00:00Z', status: 'queued' }])
      .mockResolvedValue([
        { item_id: 'inbox-9', document_id: doc, sender: 'x', received_at: '2026-10-24T00:00:00Z', status: 'processing', run_id: 'run-auto-1' },
      ])
    m.getRun.mockResolvedValue({ ...run, run_id: 'run-auto-1', batch_id: 'batch-autopilot' })
    const aggregate: Batch = { ...attackBatch, batch_id: 'batch-autopilot', kind: 'standard', invoices: attackBatch.invoices.slice(0, 1) }
    m.getBatch.mockResolvedValue(aggregate)
    const user = userEvent.setup()
    const router = renderLab()
    await screen.findByText('Autopilot picks it up, no click')
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Sent. Waiting for autopilot to pick it up')
    await waitFor(() => expect(router.state.location.pathname).toBe('/console'), { timeout: 5000 })
    expect(m.createRun).not.toHaveBeenCalled()
    expect(m.getRun).toHaveBeenCalledWith('run-auto-1')
    expect(useConsoleStore.getState().run?.run_id).toBe('run-auto-1')
    // The console gets the autopilot run's aggregate batch, not the five-invoice attack batch.
    expect(m.getBatch).toHaveBeenCalledWith('batch-autopilot')
    expect(useConsoleStore.getState().batch?.batch_id).toBe('batch-autopilot')
  })

  it('says when autopilot leaves the unguarded lane out', async () => {
    m.getAutopilot.mockResolvedValue({ enabled: true, lanes: ['countersign'], run_id: null, last_checked_at: null })
    renderLab()
    expect(await screen.findByText(/won.t see this one/)).toBeInTheDocument()
    expect(screen.getByText(/Autopilot runs countersign only\./)).toBeInTheDocument()
  })

  it('shows the contract error for a rate-limited send', async () => {
    m.createAttack.mockRejectedValue(
      new ApiError({ status: 429, code: 'rate_limited', message: 'Ten attacks a minute.', envelope: true }),
    )
    const user = userEvent.setup()
    renderLab()
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('rate_limited')
    expect(alert).toHaveTextContent('Ten attacks a minute.')
    expect(m.createRun).not.toHaveBeenCalled()
  })

  it('says "not available yet" when POST /api/attacks is a 404', async () => {
    m.createAttack.mockRejectedValue(new ApiError({ status: 404, code: 'not_found', message: 'nope', envelope: true }))
    const user = userEvent.setup()
    renderLab()
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    expect(await screen.findByTestId('not-available')).toHaveTextContent('Attacks are not available yet · POST /api/attacks → 404 not_found')
  })
})
