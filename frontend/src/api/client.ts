import { apiBaseUrl } from '@/lib/env'
import { ApiError, networkError, parseApiError } from './errors'
import { getTransport } from './transport'
import type { BodyJson, OkJson } from './types'

export { ApiError, isApiError } from './errors'

type Method = 'GET' | 'POST'

function url(path: string): string {
  return `${apiBaseUrl().replace(/\/+$/, '')}${path}`
}

function seg(id: string): string {
  return encodeURIComponent(id)
}

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) q.set(k, v)
  const s = q.toString()
  return s ? `?${s}` : ''
}

async function send(method: Method, path: string, body?: unknown, accept = 'application/json'): Promise<Response> {
  const t = await getTransport()
  const headers: Record<string, string> = { Accept: accept }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await t.fetch(url(path), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    throw networkError(e)
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw parseApiError(res.status, text, res.statusText)
  }
  return res
}

async function json<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const res = await send(method, path, body)
  const text = await res.text()
  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError({
      status: res.status,
      code: 'internal',
      message: `Response from ${path} was not valid JSON`,
      envelope: false,
    })
  }
}

// ── Session ──────────────────────────────────────────────────────────────────
export const getHealth = () => json<OkJson<'get_health'>>('GET', '/api/health')
export const resetDemo = () => json<OkJson<'reset_demo'>>('POST', '/api/demo/reset')

// ── Batches and documents ────────────────────────────────────────────────────
export const createBatch = (body: BodyJson<'create_batch'> = { kind: 'standard' }) =>
  json<OkJson<'create_batch'>>('POST', '/api/batches', body)
export const getBatch = (batchId: string) => json<OkJson<'get_batch'>>('GET', `/api/batches/${seg(batchId)}`)
export const getDocument = (documentId: string) =>
  json<OkJson<'get_document'>>('GET', `/api/documents/${seg(documentId)}`)
/** The original PDF. Use documentPdfUrl for an <iframe>/<a> instead when possible. */
export const getDocumentPdf = async (documentId: string): Promise<Blob> =>
  (await send('GET', `/api/documents/${seg(documentId)}/pdf`, undefined, 'application/pdf')).blob()
export const documentPdfUrl = (documentId: string) => url(`/api/documents/${seg(documentId)}/pdf`)
export const createAttack = (body: BodyJson<'create_attack'>) =>
  json<OkJson<'create_attack'>>('POST', '/api/attacks', body)

// ── Runs ─────────────────────────────────────────────────────────────────────
export const createRun = (body: BodyJson<'create_run'>) => json<OkJson<'create_run'>>('POST', '/api/runs', body)
export const getRun = (runId: string) => json<OkJson<'get_run'>>('GET', `/api/runs/${seg(runId)}`)
/** SSE stream URL; consumed by useRunEvents through the transport's EventSource. */
export const runEventsUrl = (runId: string) => url(`/api/runs/${seg(runId)}/events`)
export const getRunDecisions = (runId: string) =>
  json<OkJson<'get_run_decisions'>>('GET', `/api/runs/${seg(runId)}/decisions`)
export const getInvoiceGraph = (runId: string, invoiceId: string) =>
  json<OkJson<'get_invoice_graph'>>('GET', `/api/runs/${seg(runId)}/invoices/${seg(invoiceId)}/graph`)
export const getPlannerTranscript = (runId: string) =>
  json<OkJson<'get_planner_transcript'>>('GET', `/api/runs/${seg(runId)}/planner_transcript`)
export const getAgentTranscript = (runId: string, invoiceId: string) =>
  json<OkJson<'get_agent_transcript'>>(
    'GET',
    `/api/runs/${seg(runId)}/invoices/${seg(invoiceId)}/agent_transcript`,
  )

// ── Approvals ────────────────────────────────────────────────────────────────
export const listApprovals = (status?: 'pending' | 'resolved') =>
  json<OkJson<'list_approvals'>>('GET', `/api/approvals${query({ status })}`)
export const resolveApproval = (approvalId: string, body: BodyJson<'resolve_approval'>) =>
  json<OkJson<'resolve_approval'>>('POST', `/api/approvals/${seg(approvalId)}`, body)

// ── Bank ─────────────────────────────────────────────────────────────────────
export const getBalances = () => json<OkJson<'get_balances'>>('GET', '/api/balances')

// ── Recorded bank-change evidence (read-only, no live calls) ────────────────
export const listBankChangeEvidence = () =>
  json<OkJson<'list_bank_change_evidence'>>('GET', '/api/evidence/bank-change')
export const getBankChangeEvidence = (runId: string) =>
  json<OkJson<'get_bank_change_evidence'>>('GET', `/api/evidence/bank-change/${seg(runId)}`)

// ── Red team and audit ───────────────────────────────────────────────────────
export const createRedTeamJob = (body: BodyJson<'create_redteam_job'>) =>
  json<OkJson<'create_redteam_job'>>('POST', '/api/redteam', body)
export const getRedTeamJob = (jobId: string) =>
  json<OkJson<'get_redteam_job'>>('GET', `/api/redteam/${seg(jobId)}`)
export const askAudit = (body: BodyJson<'ask_audit'>) => json<OkJson<'ask_audit'>>('POST', '/api/audit/ask', body)

// ── Hyphenate track: people, vendors, ledger, inbox, autopilot, eval ─────────
export const listPeople = () => json<OkJson<'list_people'>>('GET', '/api/people')
export const getVendor = (vendorId: string) => json<OkJson<'get_vendor'>>('GET', `/api/vendors/${seg(vendorId)}`)
export const changeVendorBank = (vendorId: string, body: BodyJson<'change_vendor_bank'>) =>
  json<OkJson<'change_vendor_bank'>>('POST', `/api/vendors/${seg(vendorId)}/bank_change`, body)
export const listJournalEntries = (filters: { run_id?: string; invoice_id?: string } = {}) =>
  json<OkJson<'list_journal_entries'>>('GET', `/api/ledger/entries${query(filters)}`)
export const getTrialBalance = () => json<OkJson<'get_trial_balance'>>('GET', '/api/ledger/trial_balance')
export const getReconciliation = () => json<OkJson<'get_reconciliation'>>('GET', '/api/reconciliation')
export const listInboxItems = () => json<OkJson<'list_inbox_items'>>('GET', '/api/inbox')
export const createInboxItem = (body: BodyJson<'create_inbox_item'>) =>
  json<OkJson<'create_inbox_item'>>('POST', '/api/inbox', body)
export const getAutopilot = () => json<OkJson<'get_autopilot'>>('GET', '/api/autopilot')
export const setAutopilot = (body: BodyJson<'set_autopilot'>) =>
  json<OkJson<'set_autopilot'>>('POST', '/api/autopilot', body)
export const createEvalJob = (body: BodyJson<'create_eval_job'>) =>
  json<OkJson<'create_eval_job'>>('POST', '/api/eval', body)
export const getEvalJob = (jobId: string) => json<OkJson<'get_eval_job'>>('GET', `/api/eval/${seg(jobId)}`)
