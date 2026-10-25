import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import replayFixture from '@fixtures/bank_change_replay.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import { MockEventSource } from '@/api/mock/eventSource'
import { setTransportForTests } from '@/api/transport'
import type { Approval, BankChangeReplay, BankChangeReplaySummary, Batch, Run, VendorDetail } from '@/api/types'
import { useReplayPlayer } from '@/hooks/useReplayPlayer'
import { BankChangePage } from './BankChangePage'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    listBankChangeEvidence: vi.fn(),
    getBankChangeEvidence: vi.fn(),
    createBatch: vi.fn(),
    createRun: vi.fn(),
    getVendor: vi.fn(),
    listApprovals: vi.fn(),
    getDocument: vi.fn(),
    getPlannerTranscript: vi.fn(),
  }
})

const m = vi.mocked(client)
const replay = replayFixture as unknown as BankChangeReplay
const summary = (r: BankChangeReplay): BankChangeReplaySummary => ({
  recorded: r.recorded,
  recorded_at: r.recorded_at,
  source_file: r.source_file,
  source_sha256: r.source_sha256,
  run_id: r.run_id,
  is_default: r.is_default,
  complete: r.complete,
  status: r.status,
  unguarded_provider: r.unguarded_provider,
  unguarded_model: r.unguarded_model,
  unguarded_mode: r.unguarded_mode,
  summary: r.summary,
  limitations: r.limitations,
})
const letterOnFile = replay.letter!.account_on_file_display!
const letterAccount = replay.letter!.requested_account_display

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BankChangePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  m.listBankChangeEvidence.mockResolvedValue([summary(replay)])
  m.getBankChangeEvidence.mockResolvedValue(replay)
})
afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllEnvs()
  setTransportForTests(null)
})

describe('BankChangePage: recorded run', () => {
  it('shows the final recorded state, labelled as a recording', async () => {
    renderPage()
    const label = await screen.findByTestId('run-source-label')
    expect(label).toHaveTextContent(`Recording of a live run · ${replay.recorded_at} · ${replay.run_id}`)
    expect(m.getBankChangeEvidence).toHaveBeenCalledWith(replay.run_id)

    // Letter
    expect(screen.getByTestId('letter-account')).toHaveTextContent(letterAccount)
    expect(screen.getByTestId('letter-on-file')).toHaveTextContent(letterOnFile)

    // Unguarded lane: provider/model from the data, one payment to the letter's account.
    const ung = screen.getByRole('region', { name: 'Unguarded agent' })
    expect(within(ung).getByTestId('model-unguarded')).toHaveTextContent(`${replay.lanes.unguarded.provider} · ${replay.lanes.unguarded.model}`)
    expect(within(ung).getAllByRole('listitem')).toHaveLength(replay.lanes.unguarded.payments.length)
    const marker = within(ung).getByTestId('letter-payment')
    expect(marker).toHaveTextContent('Paid to account from letter')
    expect(marker).toHaveTextContent(`on file ${letterOnFile} · paid ${letterAccount}`)

    // Countersign lane: the hold, both accounts, no payment, leak guard counts.
    const cs = screen.getByRole('region', { name: 'Countersign' })
    expect(within(cs).getByTestId('reason-code')).toHaveTextContent('BANK_DETAILS_MISMATCH')
    expect(within(cs).getByTestId('hold-on-file')).toHaveTextContent(letterOnFile)
    expect(within(cs).getByTestId('hold-letter')).toHaveTextContent(letterAccount)
    expect(within(cs).getByTestId('no-payment')).toHaveTextContent('No payment sent')
    expect(within(cs).queryByTestId('letter-payment')).toBeNull()
    const lg = replay.leak_guard!
    expect(within(cs).getByTestId('leak-guard')).toHaveTextContent(
      `${lg.strings_checked} strings, ${lg.canaries_checked} canaries checked · ${lg.matches} reached the planner`,
    )

    // Transfers: every id with its real status.
    const strip = screen.getByRole('region', { name: 'Nessie transfers' })
    const all = [...replay.lanes.unguarded.payments, ...replay.lanes.countersign.payments]
    for (const p of all) expect(within(strip).getByText(p.transfer_id)).toBeInTheDocument()
    expect(within(strip).getAllByTestId('transfer-status').map((c) => c.textContent)).toEqual(all.map(() => 'pending'))
    expect(strip).toHaveTextContent('transfers stay pending')
  })

  it('replays from nothing and can skip to the final state; the recording label never goes away', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Replay recorded run' }))
    expect(screen.getByTestId('run-source-label')).toHaveTextContent('Recording of a live run')
    expect(screen.getByTestId('replay-progress')).toHaveTextContent(`of ${replay.events.length}`)
    expect(screen.getByRole('region', { name: 'Unguarded agent' })).toHaveTextContent('No payments yet.')
    // Countersign's planner hasn't run at this point of the replay: no leak-guard counts yet.
    expect(screen.queryByTestId('leak-guard')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Skip to the end' }))
    expect(screen.getByRole('button', { name: 'Replay recorded run' })).toBeInTheDocument()
    expect(screen.getByTestId('no-payment')).toBeInTheDocument()
    expect(screen.getByTestId('run-source-label')).toHaveTextContent(replay.run_id)
  })

  it('shows "—" for fields a recording lacks, and its limitations', async () => {
    const partial: BankChangeReplay = {
      ...replay,
      complete: false,
      status: 'failed',
      limitations: ['planner transcript missing'],
      leak_guard: undefined,
      letter: { ...replay.letter!, account_on_file_display: undefined },
    }
    m.getBankChangeEvidence.mockResolvedValue(partial)
    renderPage()
    expect(await screen.findByTestId('leak-guard')).toHaveTextContent('leak guard: —')
    expect(screen.getByTestId('limitations')).toHaveTextContent('Incomplete recording (status failed).')
    expect(screen.getByTestId('limitations')).toHaveTextContent('planner transcript missing')
  })

  it('lists recorded runs in the selector and loads the picked one', async () => {
    const other = { ...summary(replay), run_id: 'run-other', is_default: false }
    m.listBankChangeEvidence.mockResolvedValue([summary(replay), other])
    const user = userEvent.setup()
    renderPage()
    const select = await screen.findByRole('combobox', { name: 'Recorded run' })
    await waitFor(() => expect(select).toHaveValue(replay.run_id))
    await user.selectOptions(select, 'run-other')
    await waitFor(() => expect(m.getBankChangeEvidence).toHaveBeenCalledWith('run-other'))
  })

  it('says why when the evidence endpoints fail', async () => {
    m.listBankChangeEvidence.mockRejectedValue(new ApiError({ status: 503, code: 'service_unavailable', message: 'down', envelope: true }))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not read the recorded runs')
  })

  it('says a recording is not available when the detail is a 404', async () => {
    m.getBankChangeEvidence.mockRejectedValue(new ApiError({ status: 404, code: 'not_found', message: 'nope', envelope: true }))
    renderPage()
    expect(await screen.findByTestId('not-available')).toHaveTextContent('the recorded run not available yet')
  })
})

describe('BankChangePage: live run', () => {
  it('is off in mock mode', async () => {
    vi.stubEnv('VITE_USE_MOCK', '1')
    renderPage()
    expect(await screen.findByRole('button', { name: 'Run live (~6 min)' })).toBeDisabled()
  })

  it('starts a bank_change batch and run, then shows the streamed events labelled Live', async () => {
    vi.stubEnv('VITE_USE_MOCK', '0')
    const runId = replay.run_id
    m.createBatch.mockResolvedValue(replay.batch as Batch)
    m.createRun.mockResolvedValue(replay.run as Run)
    m.getVendor.mockImplementation(async (id) => {
      const p = [...replay.lanes.unguarded.payments, ...replay.lanes.countersign.payments].find((x) => x.vendor_id === id)
      return { vendor_id: id, name: p?.vendor_name ?? id, account_on_file_display: p?.account_on_file_display ?? '', account_history: [], payments: [] } as VendorDetail
    })
    m.listApprovals.mockResolvedValue([replay.lanes.countersign.holds[0]!.approval as Approval])
    m.getDocument.mockResolvedValue(replay.letter!.document)
    m.getPlannerTranscript.mockResolvedValue(replay.planner_transcript!)
    setTransportForTests({
      fetch: () => Promise.reject(new Error('unexpected fetch')),
      openEventSource: () => new MockEventSource(replay.events, { delayMs: 1 }),
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Run live (~6 min)' }))

    expect(m.createBatch).toHaveBeenCalledWith({ kind: 'bank_change' })
    expect(m.createRun).toHaveBeenCalledWith({
      batch_id: replay.batch.batch_id,
      lanes: ['unguarded', 'countersign'],
      unguarded_mode: 'ap_with_vendor_updates',
    })
    expect(await screen.findByTestId('run-source-label')).toHaveTextContent(`Live · ${runId}`)
    expect(screen.getByTestId('run-source-label')).not.toHaveTextContent('Recording')
    await waitFor(() => expect(screen.getByTestId('no-payment')).toBeInTheDocument(), { timeout: 3000 })
    await waitFor(() => expect(screen.getByTestId('hold-letter')).toHaveTextContent(letterAccount))
    expect(screen.getByTestId('letter-payment')).toHaveTextContent(`on file ${letterOnFile} · paid ${letterAccount}`)
    await waitFor(() => expect(screen.getByTestId('leak-guard')).toHaveTextContent(`${replay.leak_guard!.strings_checked} strings,`))
  })
})

describe('useReplayPlayer', () => {
  it('starts on the final state, steps with the given delays, then stops on the final state', () => {
    vi.useFakeTimers()
    try {
      const { result } = renderHook(() => useReplayPlayer([10, 20, 30], 'r'))
      expect(result.current).toMatchObject({ shown: 3, playing: false })
      act(() => result.current.play())
      expect(result.current).toMatchObject({ shown: 0, playing: true })
      act(() => void vi.advanceTimersByTime(10))
      expect(result.current.shown).toBe(1)
      act(() => void vi.advanceTimersByTime(20))
      expect(result.current.shown).toBe(2)
      act(() => void vi.advanceTimersByTime(30))
      expect(result.current).toMatchObject({ shown: 3, playing: false })
    } finally {
      vi.useRealTimers()
    }
  })
})
