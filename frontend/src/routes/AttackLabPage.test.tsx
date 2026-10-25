import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
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

  it('autopilot off: sends the attack, says so, and leaves the batch for the Console to run', async () => {
    const user = userEvent.setup()
    const router = renderLab()
    await screen.findByText('Autopilot is off: it waits in the inbox until you run the batch from the Console')
    await user.selectOptions(screen.getByLabelText('Pretend to be'), 'VEN-005')
    await user.click(screen.getByLabelText(/Footer/))
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    expect(await screen.findByTestId('autopilot-off')).toHaveTextContent(
      'Autopilot is off: turn it on or run the batch from the Console.',
    )
    expect(m.createAttack).toHaveBeenCalledWith({ vendor_id: 'VEN-005', attack_text: TEXT, placement: 'footer' })
    // The sending state ended and nothing started a run behind the user's back.
    expect(screen.getByRole('button', { name: 'Send it to the AP inbox' })).toBeEnabled()
    expect(m.createRun).not.toHaveBeenCalled()
    expect(router.state.location.pathname).toBe('/attack')
    expect(useConsoleStore.getState().batch?.batch_id).toBe('batch-attack-1')
  })

  it('autopilot on: does not start a run, waits for the inbox item to name one, then opens it', async () => {
    m.getAutopilot.mockResolvedValue({ enabled: true, lanes: ['unguarded', 'countersign'], run_id: null, last_checked_at: null })
    // Only the chosen vendor's invoice (the first, VEN-001 by default) is queued.
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
    expect(await screen.findByTestId('inbox-item-status')).toHaveAttribute('data-status', 'queued')
    await waitFor(() => expect(router.state.location.pathname).toBe('/console'), { timeout: 5000 })
    expect(m.createRun).not.toHaveBeenCalled()
    expect(m.getRun).toHaveBeenCalledWith('run-auto-1')
    expect(useConsoleStore.getState().run?.run_id).toBe('run-auto-1')
    // The console gets the autopilot run's aggregate batch, not the five-invoice attack batch.
    expect(m.getBatch).toHaveBeenCalledWith('batch-autopilot')
    expect(useConsoleStore.getState().batch?.batch_id).toBe('batch-autopilot')
    // …focused on the invoice made from the sent document.
    expect(useConsoleStore.getState().selectedInvoiceId).toBe(aggregate.invoices[0]!.invoice_id)
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

  it('shows a 404 with the server message as-is (unknown vendor), and offers Retry', async () => {
    m.createAttack.mockRejectedValue(new ApiError({ status: 404, code: 'not_found', message: 'Unknown vendor VEN-404', envelope: true }))
    const user = userEvent.setup()
    renderLab()
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Unknown vendor VEN-404')
    expect(within(alert).getByRole('button', { name: 'retry' })).toBeInTheDocument()
  })

  it('shows a 422 message verbatim', async () => {
    const msg = 'attack_text must contain 1 to 600 characters after control characters are stripped.'
    m.createAttack.mockRejectedValue(new ApiError({ status: 422, code: 'validation_error', message: msg, envelope: true }))
    const user = userEvent.setup()
    renderLab()
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(msg)
  })

  it('ends the sending state on a timeout and retries', async () => {
    m.createAttack
      .mockRejectedValueOnce(new ApiError({ status: 0, code: 'timeout', message: 'No response from POST /api/attacks after 20 s.', envelope: false }))
      .mockResolvedValueOnce(attackBatch)
    const user = userEvent.setup()
    renderLab()
    await user.type(screen.getByLabelText('Your text'), TEXT)
    await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('No response from POST /api/attacks after 20 s.')
    expect(screen.getByRole('button', { name: 'Send it to the AP inbox' })).toBeEnabled()
    await user.click(within(alert).getByRole('button', { name: 'retry' }))
    expect(await screen.findByTestId('autopilot-off')).toBeInTheDocument()
    expect(m.createAttack).toHaveBeenCalledTimes(2)
  })

  it('stops waiting for autopilot after 20 s and offers Retry', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      m.getAutopilot.mockResolvedValue({ enabled: true, lanes: ['unguarded', 'countersign'], run_id: null, last_checked_at: null })
      m.listInboxItems.mockResolvedValue([])
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      renderLab()
      await screen.findByText('Autopilot picks it up, no click')
      await user.type(screen.getByLabelText('Your text'), TEXT)
      await user.click(screen.getByRole('button', { name: 'Send it to the AP inbox' }))
      expect(await screen.findByRole('status')).toHaveTextContent('Sent. Waiting for autopilot to pick it up')
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_500)
      })
      expect(screen.getByRole('status')).toHaveTextContent("Sent, but autopilot hasn't picked it up after 20 s.")
      expect(screen.getByRole('button', { name: 'Send it to the AP inbox' })).toBeEnabled()
      await user.click(screen.getByRole('button', { name: 'Retry' }))
      expect(screen.getByRole('status')).toHaveTextContent('Sent. Waiting for autopilot to pick it up')
    } finally {
      vi.useRealTimers()
    }
  })

})
