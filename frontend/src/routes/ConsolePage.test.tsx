import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import batchFixture from '@fixtures/batch.json'
import balancesFixture from '@fixtures/balances.json'
import healthFixture from '@fixtures/health.json'
import runFixture from '@fixtures/run.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import { setTransportForTests } from '@/api/transport'
import type { Balances, Batch, Health, Run } from '@/api/types'
import { useConsoleStore } from '@/state/consoleStore'
import { ConsolePage } from './ConsolePage'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    createBatch: vi.fn(),
    createRun: vi.fn(),
    getBalances: vi.fn(),
    getHealth: vi.fn(),
  }
})

const mocked = vi.mocked(client)
const batch = batchFixture as Batch
const run = runFixture as Run

function renderConsole() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ConsolePage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  useConsoleStore.setState({ batch: null, run: null, runNonce: 0 })
  mocked.getBalances.mockResolvedValue(balancesFixture as Balances)
  mocked.getHealth.mockResolvedValue(healthFixture as Health)
  mocked.createBatch.mockResolvedValue(batch)
  // A stream that opens and never sends anything: this test is about layout, not events.
  setTransportForTests({
    fetch: () => Promise.reject(new Error('unused')),
    openEventSource: () => ({ readyState: 0, onopen: null, onerror: null, addEventListener() {}, close() {} }),
  })
})

afterEach(() => {
  setTransportForTests(null)
  vi.clearAllMocks()
})

describe('<ConsolePage>', () => {
  it('renders both lanes, an empty batch rail and the ledger', async () => {
    renderConsole()
    const a = screen.getByRole('region', { name: 'Lane A: Unguarded agent' })
    const b = screen.getByRole('region', { name: 'Lane B: Countersign' })
    expect(within(a).getByText('No run yet')).toBeInTheDocument()
    expect(within(b).getByText('No run yet')).toBeInTheDocument()
    expect(screen.getByText('No batch drawn')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Run batch/ })).toBeDisabled()
    const ledger = screen.getByRole('region', { name: 'Ledger balances' })
    expect(within(ledger).getByText('Reading balances…')).toBeInTheDocument()
    expect(await within(ledger).findByText('Fogline Festival')).toBeInTheDocument()
    expect(within(ledger).getByRole('region', { name: /Attacker account/ })).toHaveTextContent('Received this session')
    expect(within(ledger).getByText('bank · offline mode')).toBeInTheDocument()
  })

  it('draws a batch and runs it; a lane the backend did not start shows "not running"', async () => {
    mocked.createRun.mockResolvedValue({ ...run, lanes: [{ lane: 'countersign', status: 'running' }], tallies: undefined })
    const user = userEvent.setup()
    renderConsole()
    await user.click(screen.getByRole('button', { name: 'New batch' }))
    const list = await screen.findByRole('list', { name: 'Invoices in batch' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
    expect(within(list).getByText('Cedar Grove Catering')).toBeInTheDocument()
    expect(within(list).getByLabelText('$640.00')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Run batch/ }))
    expect(mocked.createRun).toHaveBeenCalledWith({ batch_id: batch.batch_id, lanes: ['unguarded', 'countersign'] })

    const a = screen.getByRole('region', { name: 'Lane A: Unguarded agent' })
    const b = screen.getByRole('region', { name: 'Lane B: Countersign' })
    // Both the lane's status label and its body say so.
    await waitFor(() => expect(within(a).getAllByText('Not running')).toHaveLength(2))
    expect(within(a).getByText(/did not start the unguarded lane/)).toBeInTheDocument()
    expect(within(b).queryByText('Not running')).toBeNull()
    expect(within(b).getByText('Waiting for the first event…')).toBeInTheDocument()
    expect(within(list).getAllByLabelText('Unguarded: not running')).toHaveLength(5)
    expect(within(list).getAllByLabelText('Countersign: pending')).toHaveLength(5)
  })

  it('starts a run with the R shortcut', async () => {
    mocked.createRun.mockResolvedValue(run)
    const user = userEvent.setup()
    renderConsole()
    await user.keyboard('r')
    expect(mocked.createRun).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'New batch' }))
    await screen.findByRole('list', { name: 'Invoices in batch' })
    await user.keyboard('r')
    await waitFor(() => expect(mocked.createRun).toHaveBeenCalledTimes(1))
  })

  it('shows the contract error message and retries a failed request', async () => {
    mocked.createBatch
      .mockRejectedValueOnce(
        new ApiError({ status: 503, code: 'service_unavailable', message: 'Nessie is unreachable', envelope: true }),
      )
      .mockResolvedValueOnce(batch)
    const user = userEvent.setup()
    renderConsole()
    await user.click(screen.getByRole('button', { name: 'New batch' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not draw a batch')
    expect(alert).toHaveTextContent('service_unavailable')
    expect(alert).toHaveTextContent('Nessie is unreachable')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('list', { name: 'Invoices in batch' })).toBeInTheDocument()
  })

  it('shows a failed balances request with a retry', async () => {
    mocked.getBalances
      .mockRejectedValueOnce(new ApiError({ status: 500, code: 'internal', message: 'boom', envelope: true }))
      .mockResolvedValue(balancesFixture as Balances)
    const user = userEvent.setup()
    renderConsole()
    const ledger = screen.getByRole('region', { name: 'Ledger balances' })
    const alert = await within(ledger).findByRole('alert')
    expect(alert).toHaveTextContent('boom')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await within(ledger).findByText('Fogline Festival')).toBeInTheDocument()
  })
})
