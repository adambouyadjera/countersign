import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import approvalsFixture from '@fixtures/approvals.json'
import auditFixture from '@fixtures/audit_answer.json'
import autopilotFixture from '@fixtures/autopilot.json'
import evalFixture from '@fixtures/eval_job.json'
import healthFixture from '@fixtures/health.json'
import ledgerFixture from '@fixtures/ledger_entries.json'
import peopleFixture from '@fixtures/people.json'
import reconFixture from '@fixtures/reconciliation.json'
import redteamFixture from '@fixtures/redteam_job.json'
import tbFixture from '@fixtures/trial_balance.json'
import * as client from '@/api/client'
import { ApiError } from '@/api/errors'
import type { EvalJob, RedTeamJob } from '@/api/types'
import { AutopilotStatus } from '@/components/shell/AutopilotStatus'
import { useConsoleStore } from '@/state/consoleStore'
import { useProofStore } from '@/state/proofStore'
import { AuditPage } from './AuditPage'
import { BooksPage } from './BooksPage'
import { ProofPage } from './ProofPage'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    getTrialBalance: vi.fn(),
    getReconciliation: vi.fn(),
    listJournalEntries: vi.fn(),
    listPeople: vi.fn(),
    listApprovals: vi.fn(),
    createEvalJob: vi.fn(),
    getEvalJob: vi.fn(),
    createRedTeamJob: vi.fn(),
    getRedTeamJob: vi.fn(),
    askAudit: vi.fn(),
    getHealth: vi.fn(),
    getAutopilot: vi.fn(),
    setAutopilot: vi.fn(),
  }
})

const m = vi.mocked(client)
type R<F extends (...a: never[]) => unknown> = Awaited<ReturnType<F>>
const evalJob = evalFixture as EvalJob
const redteam = redteamFixture as RedTeamJob
const notFound = () => new ApiError({ status: 404, code: 'not_found', message: 'nope', envelope: true })

function renderIt(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  m.getTrialBalance.mockResolvedValue(tbFixture as R<typeof client.getTrialBalance>)
  m.getReconciliation.mockResolvedValue(reconFixture as R<typeof client.getReconciliation>)
  m.listJournalEntries.mockResolvedValue(ledgerFixture as R<typeof client.listJournalEntries>)
  m.listPeople.mockResolvedValue(peopleFixture as R<typeof client.listPeople>)
  m.listApprovals.mockResolvedValue(approvalsFixture as R<typeof client.listApprovals>)
  m.getHealth.mockResolvedValue(healthFixture as R<typeof client.getHealth>)
  useProofStore.setState({ evalJobId: null, redteamJobId: null })
  useConsoleStore.setState({ batch: null, run: null, runNonce: 0, selectedInvoiceId: null, focusedInvoiceId: null })
})

afterEach(() => vi.clearAllMocks())

describe('<BooksPage>', () => {
  it('is pleased when the API reports balanced and a zero difference', async () => {
    renderIt(<BooksPage />)
    expect(await screen.findByRole('heading', { level: 1, name: 'The books agree with the bank' })).toBeInTheDocument()
    expect(screen.getByText('BALANCED')).toBeInTheDocument()
    expect(screen.getByText('RECONCILED · $0.00 DIFFERENCE')).toBeInTheDocument()
    expect(screen.getByTestId('books-verdict')).toHaveTextContent('Every dollar I moved has a journal entry')
    expect(document.querySelector('[data-expression="pleased"]')).toBeInTheDocument()
    expect(screen.getByTestId('tb-total')).toHaveTextContent('Total26,040.0026,040.00')
    expect(screen.getByTestId('recon-bank')).toHaveTextContent('9 · $6,040')
    expect(screen.getByTestId('recon-ledger')).toHaveTextContent('9 · $6,040')
  })

  it('is not pleased when the reconciliation has a difference, and lists unmatched items', async () => {
    m.getReconciliation.mockResolvedValue({
      ...(reconFixture as R<typeof client.getReconciliation>),
      difference_cents: -18000,
      items: [{ transfer_id: 't-extra', journal_entry_id: null, amount_cents: 18000, status: 'bank_only', note: 'no entry' }],
    })
    renderIt(<BooksPage />)
    expect(await screen.findByText('DIFFERENCE · $180.00')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent("The books and the bank don't agree yet")
    expect(document.querySelector('[data-expression="pleased"]')).toBeNull()
    expect(document.querySelector('[data-expression="serious"]')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Unmatched items' })).toHaveTextContent('bank only: t-extra · $180.00 · no entry')
  })

  it('is not pleased when the trial balance is not balanced', async () => {
    m.getTrialBalance.mockResolvedValue({ ...(tbFixture as R<typeof client.getTrialBalance>), balanced: false })
    renderIt(<BooksPage />)
    expect(await screen.findByText('NOT BALANCED')).toBeInTheDocument()
    expect(document.querySelector('[data-expression="pleased"]')).toBeNull()
  })

  it('shows the newest three journal entries, then the rest on request', async () => {
    const user = userEvent.setup()
    renderIt(<BooksPage />)
    const list = await screen.findByRole('list', { name: 'Journal entries' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('INV-005')
    expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('by Sig')
    await user.click(screen.getByRole('button', { name: '7 more entries' }))
    expect(within(list).getAllByRole('listitem')).toHaveLength(10)
  })

  it('builds the controls log from people and approvals', async () => {
    renderIt(<BooksPage />)
    const log = await screen.findByRole('region', { name: 'Bank reconciliation and controls' })
    await waitFor(() => expect(log).toHaveTextContent('Sig requested INV-003 → needs an AP manager'))
    expect(log).toHaveTextContent('Avery Chen (AP manager) approves up to $2,000')
    expect(log).toHaveTextContent('Jordan Patel (controller) approves with no limit')
  })

  it('says "not available yet" for ledger endpoints that 404, and Sig stays unconvinced', async () => {
    m.getTrialBalance.mockRejectedValue(notFound())
    m.getReconciliation.mockRejectedValue(notFound())
    m.listJournalEntries.mockRejectedValue(notFound())
    renderIt(<BooksPage />)
    await waitFor(() => expect(screen.getAllByTestId('not-available')).toHaveLength(3))
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Books and controls')
    expect(document.querySelector('[data-expression="pleased"]')).toBeNull()
  })
})

describe('<ProofPage>', () => {
  it('shows dashes until a job reports numbers, then fills the tiles from the API', async () => {
    m.createEvalJob.mockResolvedValue({ ...evalJob, status: 'queued', progress: { completed: 0, total: 5 }, metrics: undefined, items: [] })
    m.getEvalJob.mockResolvedValue(evalJob)
    m.createRedTeamJob.mockResolvedValue(redteam)
    m.getRedTeamJob.mockResolvedValue(redteam)
    const user = userEvent.setup()
    renderIt(<ProofPage />)
    expect(screen.getByTestId('kpi-matches')).toHaveTextContent('—')
    expect(screen.getByTestId('kpi-fooled')).toHaveTextContent('—')
    expect(document.querySelector('[data-expression="honest"]')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'run the answer-key eval' }))
    expect(m.createEvalJob).toHaveBeenCalledWith({ suite: 'planted_errors_v1' })
    await waitFor(() => expect(screen.getByTestId('kpi-matches')).toHaveTextContent('5 / 5'))
    expect(screen.getByTestId('kpi-unauthorized')).toHaveTextContent('0')
    expect(screen.getByTestId('kpi-false-holds')).toHaveTextContent('0 / 1')

    await user.clear(screen.getByLabelText('variants'))
    await user.type(screen.getByLabelText('variants'), '12')
    await user.click(screen.getByRole('button', { name: 'Run 12 new variants' }))
    expect(m.createRedTeamJob).toHaveBeenCalledWith({ variants: 12 })
    await waitFor(() => expect(screen.getByTestId('kpi-fooled')).toHaveTextContent('8 / 10'))
    expect(screen.getByTestId('distinct-prompts')).toHaveTextContent('1')
    expect(screen.getByRole('img', { name: /Unguarded: 8 paid the attacker/ }).querySelectorAll('[data-tone="bad"]')).toHaveLength(8)
  })

  it('polls a running job and shows real progress', async () => {
    useProofStore.setState({ evalJobId: evalJob.job_id })
    m.getEvalJob
      .mockResolvedValueOnce({ ...evalJob, status: 'running', progress: { completed: 2, total: 5 }, items: evalJob.items.slice(0, 2), metrics: undefined })
      .mockResolvedValue(evalJob)
    renderIt(<ProofPage />)
    expect(await screen.findByText('running · 2 / 5')).toBeInTheDocument()
    expect(await screen.findByText('completed · 5 / 5', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(m.getEvalJob.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('lists misses instead of hiding them', async () => {
    useProofStore.setState({ evalJobId: evalJob.job_id })
    m.getEvalJob.mockResolvedValue({
      ...evalJob,
      items: evalJob.items.map((i) =>
        i.error_type === 'amount_mismatch' ? { ...i, passed: false, actual_outcome: 'PAY' as const, actual_reason_codes: [] } : i,
      ),
      metrics: { ...evalJob.metrics!, by_error_type: { ...evalJob.metrics!.by_error_type, amount_mismatch: { caught: 0, missed: 1 } } },
    })
    renderIt(<ProofPage />)
    const misses = await screen.findByRole('list', { name: 'Misses' })
    expect(misses).toHaveTextContent('EVAL-003 · amount_mismatch · expected HOLD (AMOUNT_MISMATCH) · got PAY')
    expect(screen.getByRole('link', { name: '1 · view' })).toHaveAttribute('href', '#miss-amount_mismatch')
    expect(screen.getByTestId('kpi-matches')).toHaveTextContent('4 / 5')
  })

  it('says "not available yet" when the eval endpoint is a 404', async () => {
    m.createEvalJob.mockRejectedValue(notFound())
    const user = userEvent.setup()
    renderIt(<ProofPage />)
    await user.click(screen.getByRole('button', { name: 'run the answer-key eval' }))
    expect(await screen.findByTestId('not-available')).toHaveTextContent('POST /api/eval → 404 not_found')
    expect(screen.getByTestId('kpi-matches')).toHaveTextContent('—')
  })
})

describe('<AuditPage>', () => {
  it('asks the auditor and shows the answer with its ES|QL steps', async () => {
    m.askAudit.mockResolvedValue(auditFixture as R<typeof client.askAudit>)
    const user = userEvent.setup()
    renderIt(<AuditPage />)
    expect(document.querySelector('[data-expression="calm"]')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Summarize the latest run' }))
    expect(m.askAudit).toHaveBeenCalledWith({ question: 'Summarize the latest run' })
    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(answer).toHaveTextContent('INV-003 was held')
    expect(answer).toHaveTextContent('step 1 · example-elastic-tool-policy-search · 3 rows')
    expect(within(answer).getByTestId('esql')).toHaveTextContent('FROM countersign-events')
    expect(answer).toHaveTextContent('agent example-elastic-audit-agent')
  })

  it('a 503 shows that Elastic is offline, with no answer of its own', async () => {
    m.askAudit.mockRejectedValue(
      new ApiError({ status: 503, code: 'service_unavailable', message: 'Elastic is offline', envelope: true }),
    )
    const user = userEvent.setup()
    renderIt(<AuditPage />)
    await user.type(screen.getByLabelText('Question'), 'Which payments were held?')
    await user.click(screen.getByRole('button', { name: 'Ask' }))
    const alert = await screen.findByTestId('elastic-offline')
    expect(alert).toHaveTextContent('Elastic is offline')
    expect(alert).toHaveTextContent('POST /api/audit/ask → 503 service_unavailable')
    expect(screen.queryByRole('article', { name: 'Answer' })).toBeNull()
  })

  it("labels Elastic's health and says when there is no run for the event log", async () => {
    renderIt(<AuditPage />)
    expect(await screen.findByTestId('elastic-health')).toHaveTextContent('elastic offline')
    expect(screen.getByRole('complementary', { name: 'Event log' })).toHaveTextContent('Run a batch on the console')
  })
})

describe('<AutopilotStatus interactive>', () => {
  it('is a switch that POSTs the opposite state, keeping the lanes', async () => {
    m.getAutopilot.mockResolvedValue({ ...autopilotFixture, enabled: false } as R<typeof client.getAutopilot>)
    m.setAutopilot.mockResolvedValue({ ...autopilotFixture, enabled: true } as R<typeof client.setAutopilot>)
    const user = userEvent.setup()
    renderIt(<AutopilotStatus interactive />)
    const sw = await screen.findByRole('switch', { name: 'autopilot' })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    await user.click(sw)
    expect(m.setAutopilot).toHaveBeenCalledWith({ enabled: true, lanes: ['countersign'] })
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'))
  })

  it('shows a failed switch instead of pretending', async () => {
    m.getAutopilot.mockResolvedValue({ ...autopilotFixture, enabled: false } as R<typeof client.getAutopilot>)
    m.setAutopilot.mockRejectedValue(new ApiError({ status: 503, code: 'service_unavailable', message: 'down', envelope: true }))
    const user = userEvent.setup()
    renderIt(<AutopilotStatus interactive />)
    const sw = await screen.findByRole('switch', { name: 'autopilot' })
    await user.click(sw)
    expect(await screen.findByRole('alert')).toHaveTextContent("couldn't switch · service_unavailable")
    expect(sw).toHaveAttribute('aria-checked', 'false')
  })

  it('is plain text off the console', async () => {
    m.getAutopilot.mockResolvedValue({ ...autopilotFixture, enabled: true } as R<typeof client.getAutopilot>)
    renderIt(<AutopilotStatus />)
    expect(await screen.findByTestId('autopilot')).toHaveTextContent('autopilot on')
    expect(screen.queryByRole('switch')).toBeNull()
  })
})
