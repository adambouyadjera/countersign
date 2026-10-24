import type { ErrorCode } from '../types'
import type { Transport } from '../transport'
import { MockEventSource } from './eventSource'
import { fixtures, parseJsonl, runBothEventsText } from './fixtures'

/** Small fixed latency so loading states are visible in mock mode. */
const MOCK_LATENCY_MS = 120

type Reply = { status: number; body: unknown }
type Handler = (m: RegExpMatchArray, body: unknown, query?: URLSearchParams) => Reply

const ok = (body: unknown): Reply => ({ status: 200, body })
const fail = (status: number, code: ErrorCode, message: string): Reply => ({
  status,
  body: { error: { code, message } },
})
const noFixture = (what: string) => fail(404, 'not_found', `Mock mode has no fixture for ${what}.`)

/** Serve the fixture when the id matches the one it was recorded with; 404 otherwise. */
function byId(id: string | undefined, fixtureId: string, body: unknown, what: string): Reply {
  return id === fixtureId ? ok(body) : noFixture(`${what} ${id}`)
}

const RUN_ID = fixtures.run.run_id
const BATCH_ID = fixtures.batch.batch_id

const routes: Array<[method: string, pattern: RegExp, handler: Handler]> = [
  ['GET', /^\/api\/health$/, () => ok(fixtures.health)],
  ['POST', /^\/api\/demo\/reset$/, () => ok(fixtures.demoReset)],
  ['POST', /^\/api\/batches$/, () => ok(fixtures.batch)],
  ['GET', /^\/api\/batches\/([^/]+)$/, (m) => byId(m[1], BATCH_ID, fixtures.batch, 'batch')],
  [
    'GET',
    /^\/api\/documents\/([^/]+)$/,
    (m) => byId(m[1], fixtures.documentInv003.document_id, fixtures.documentInv003, 'document'),
  ],
  ['GET', /^\/api\/documents\/([^/]+)\/pdf$/, (m) => noFixture(`the PDF of ${m[1]}`)],
  [
    'POST',
    /^\/api\/attacks$/,
    (_m, body) => {
      // Validates like the contract (vendor in the batch, 1..600 chars, known placement) and
      // replays the fixture batch as kind "attack". There is no recorded attack response fixture.
      const b = body as { vendor_id?: unknown; attack_text?: unknown; placement?: unknown } | undefined
      const text = typeof b?.attack_text === 'string' ? b.attack_text : ''
      if ([...text].length < 1 || [...text].length > 600) {
        return fail(422, 'validation_error', 'attack_text must be 1 to 600 characters.')
      }
      if (!['visible_note', 'hidden_white_text', 'footer'].includes(String(b?.placement))) {
        return fail(422, 'validation_error', 'placement must be visible_note, hidden_white_text or footer.')
      }
      if (!fixtures.batch.invoices.some((i) => i.vendor_id === b?.vendor_id)) {
        return fail(422, 'validation_error', `Unknown vendor_id ${String(b?.vendor_id)}.`)
      }
      return ok({ ...fixtures.batch, kind: 'attack' })
    },
  ],
  [
    'POST',
    /^\/api\/runs$/,
    (_m, body) => {
      const b = body as { batch_id?: unknown; lanes?: unknown } | undefined
      if (typeof b?.batch_id !== 'string' || !Array.isArray(b.lanes) || b.lanes.length === 0) {
        return fail(422, 'validation_error', 'batch_id and a non-empty lanes list are required.')
      }
      return ok(fixtures.run)
    },
  ],
  ['GET', /^\/api\/runs\/([^/]+)$/, (m) => byId(m[1], RUN_ID, fixtures.run, 'run')],
  ['GET', /^\/api\/runs\/([^/]+)\/decisions$/, (m) => byId(m[1], RUN_ID, fixtures.policyDecisions, 'run')],
  [
    'GET',
    /^\/api\/runs\/([^/]+)\/invoices\/([^/]+)\/graph$/,
    (m) => byId(`${m[1]}/${m[2]}`, `${RUN_ID}/${fixtures.graphInv003.invoice_id}`, fixtures.graphInv003, 'graph'),
  ],
  [
    'GET',
    /^\/api\/runs\/([^/]+)\/planner_transcript$/,
    (m) => byId(m[1], RUN_ID, fixtures.plannerTranscript, 'run'),
  ],
  [
    'GET',
    /^\/api\/runs\/([^/]+)\/invoices\/([^/]+)\/agent_transcript$/,
    (m) =>
      byId(
        `${m[1]}/${m[2]}`,
        `${RUN_ID}/${fixtures.agentTranscriptInv003.invoice_id}`,
        fixtures.agentTranscriptInv003,
        'agent transcript',
      ),
  ],
  ['GET', /^\/api\/approvals$/, (_m, _b, q) => ok(fixtures.approvals.filter((a) => !q?.get('status') || a.status === q.get('status')))],
  [
    'POST',
    /^\/api\/approvals\/([^/]+)$/,
    (m) => {
      const a = fixtures.approvals.find((x) => x.approval_id === m[1])
      return a ? ok(a) : noFixture(`approval ${m[1]}`)
    },
  ],
  ['GET', /^\/api\/balances$/, () => ok(fixtures.balances)],
  ['POST', /^\/api\/redteam$/, () => ok(fixtures.redteamJob)],
  ['GET', /^\/api\/redteam\/([^/]+)$/, (m) => byId(m[1], fixtures.redteamJob.job_id, fixtures.redteamJob, 'job')],
  ['POST', /^\/api\/audit\/ask$/, () => ok(fixtures.auditAnswer)],
  ['GET', /^\/api\/people$/, () => ok(fixtures.people)],
  [
    'GET',
    /^\/api\/vendors\/([^/]+)$/,
    (m) => byId(m[1], fixtures.vendorDetail.vendor_id, fixtures.vendorDetail, 'vendor'),
  ],
  [
    'POST',
    /^\/api\/vendors\/([^/]+)\/bank_change$/,
    (m) => byId(m[1], fixtures.vendorDetail.vendor_id, fixtures.vendorDetail, 'vendor'),
  ],
  ['GET', /^\/api\/ledger\/entries$/, () => ok(fixtures.ledgerEntries)],
  ['GET', /^\/api\/ledger\/trial_balance$/, () => ok(fixtures.trialBalance)],
  ['GET', /^\/api\/reconciliation$/, () => ok(fixtures.reconciliation)],
  ['GET', /^\/api\/inbox$/, () => ok(fixtures.inbox)],
  ['POST', /^\/api\/inbox$/, () => (fixtures.inbox[0] ? ok(fixtures.inbox[0]) : noFixture('POST /api/inbox'))],
  ['GET', /^\/api\/autopilot$/, () => ok(fixtures.autopilot)],
  ['POST', /^\/api\/autopilot$/, () => ok(fixtures.autopilot)],
  ['POST', /^\/api\/eval$/, () => ok(fixtures.evalJob)],
  ['GET', /^\/api\/eval\/([^/]+)$/, (m) => byId(m[1], fixtures.evalJob.job_id, fixtures.evalJob, 'eval job')],
]

function pathOf(url: string): string {
  // Accept absolute URLs (VITE_API_BASE_URL set) and same-origin paths.
  const u = new URL(url, 'http://mock.invalid')
  return u.pathname
}

function queryOf(url: string): URLSearchParams {
  return new URL(url, 'http://mock.invalid').searchParams
}

export function route(method: string, url: string, body: unknown): Reply {
  const path = pathOf(url)
  for (const [m, re, handler] of routes) {
    if (m !== method) continue
    const match = path.match(re)
    if (match) return handler(match, body, queryOf(url))
  }
  return noFixture(`${method} ${path}`)
}

export function createMockTransport(): Transport {
  const events = parseJsonl(runBothEventsText)
  return {
    async fetch(url, init) {
      const method = (init?.method ?? 'GET').toUpperCase()
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
      const reply = route(method, url, body)
      await new Promise((r) => setTimeout(r, MOCK_LATENCY_MS))
      return new Response(JSON.stringify(reply.body), {
        status: reply.status,
        headers: { 'Content-Type': 'application/json' },
      })
    },
    openEventSource(url, opts) {
      const m = pathOf(url).match(/^\/api\/runs\/([^/]+)\/events$/)
      return new MockEventSource(m?.[1] === RUN_ID ? events : null, { lastEventId: opts?.lastEventId })
    },
  }
}
