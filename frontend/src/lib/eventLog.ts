import type { RunEvent } from '@/api/types'

/** One line per run event for the audit page's event log: what happened, to which invoice, and a short tag. */
export function eventSummary(e: RunEvent, label: (invoiceId: string) => string = (id) => id): { what: string; tag?: string } {
  switch (e.type) {
    case 'policy.decision':
      return { what: `policy.decision · ${label(e.payload.decision.invoice_id)}`, tag: e.payload.decision.outcome }
    case 'payment.executed':
      return {
        what: `payment.executed · ${label(e.payload.invoice_id)}`,
        tag: e.payload.payment.payee_on_file ? 'on file' : 'not on file',
      }
    case 'payment.held':
      return { what: `payment.held · ${label(e.payload.invoice_id)}`, tag: e.payload.reason_codes.join(', ') }
    case 'approval.resolved':
      return { what: `approval.resolved · ${label(e.payload.invoice_id)}`, tag: e.payload.decision }
    case 'ledger.posted':
      return { what: 'ledger.posted', tag: e.payload.entry.entry_id }
    case 'lane.status':
      return { what: `lane.status · ${e.lane}`, tag: e.payload.status }
    case 'document.loaded':
    case 'fields.extracted':
    case 'agent.message':
    case 'agent.tool_call':
      return { what: `${e.type} · ${label(e.payload.invoice_id)}`, tag: e.lane }
    case 'error':
      return { what: 'error', tag: e.payload.recoverable ? 'recoverable' : 'fatal' }
    default:
      return { what: e.type }
  }
}
