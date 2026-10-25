import type { LaneEvent } from '@/state/runReducer'

/**
 * What one lane did with one invoice, read off that lane's events:
 * the process words that have happened so far ("read it → believed it → paid")
 * and where the money ended up.
 */
export type OutcomeKind =
  | 'not_running'
  | 'waiting'
  | 'in_progress'
  | 'paid_on_file'
  | 'paid_off_file'
  | 'held'
  | 'blocked'
  | 'failed'

export interface LaneOutcome {
  kind: OutcomeKind
  words: string[]
  /** Held or blocked: the policy decision's reason codes. */
  reasonCodes?: string[]
  amountCents?: number
  toAccountId?: string
}

function forInvoice(steps: readonly LaneEvent[], invoiceId: string): LaneEvent[] {
  return steps.filter((e) => {
    switch (e.type) {
      case 'policy.decision':
        return e.payload.decision.invoice_id === invoiceId
      case 'document.loaded':
      case 'fields.extracted':
      case 'agent.message':
      case 'agent.tool_call':
      case 'payment.executed':
      case 'payment.held':
      case 'approval.resolved':
        return e.payload.invoice_id === invoiceId
      case 'error':
        return e.payload.invoice_id === invoiceId
      default:
        return false
    }
  })
}

/** Unguarded lane: "read it → believed it → paid" when it paid an account not on file. */
export function unguardedOutcome(steps: readonly LaneEvent[], invoiceId: string, running: boolean): LaneOutcome {
  if (!running) return { kind: 'not_running', words: [] }
  const mine = forInvoice(steps, invoiceId)
  const words: string[] = []
  if (mine.some((e) => e.type === 'document.loaded')) words.push('read it')
  const paid = mine.find((e) => e.type === 'payment.executed')
  if (paid) {
    const p = paid.payload.payment
    if (p.payee_on_file) return { kind: 'paid_on_file', words: [...words, 'paid on file'], amountCents: p.amount_cents, toAccountId: p.to_account_id }
    return {
      kind: 'paid_off_file',
      words: [...words, 'believed it', 'paid'],
      amountCents: p.amount_cents,
      toAccountId: p.to_account_id,
    }
  }
  if (mine.some((e) => e.type === 'error')) return { kind: 'failed', words: [...words, 'failed'] }
  return { kind: mine.length > 0 ? 'in_progress' : 'waiting', words }
}

/** Countersign lane: "tagged untrusted → checked → held". */
export function countersignOutcome(steps: readonly LaneEvent[], invoiceId: string, running: boolean): LaneOutcome {
  if (!running) return { kind: 'not_running', words: [] }
  const mine = forInvoice(steps, invoiceId)
  const words: string[] = []
  const extracted = mine.find((e) => e.type === 'fields.extracted')
  if (extracted && extracted.payload.handles.some((h) => h.trust === 'untrusted')) words.push('tagged untrusted')
  const decision = mine.find((e) => e.type === 'policy.decision')
  if (decision) words.push('checked')

  const paid = mine.find((e) => e.type === 'payment.executed')
  if (paid) {
    const p = paid.payload.payment
    return {
      kind: p.payee_on_file ? 'paid_on_file' : 'paid_off_file',
      words: [...words, p.payee_on_file ? 'paid on file' : 'paid'],
      amountCents: p.amount_cents,
      toAccountId: p.to_account_id,
    }
  }
  const held = mine.find((e) => e.type === 'payment.held')
  const reasonCodes = held?.type === 'payment.held' ? held.payload.reason_codes : decision?.payload.decision.reason_codes
  if (held) return { kind: 'held', words: [...words, 'held'], amountCents: 0, reasonCodes }
  if (decision?.payload.decision.outcome === 'BLOCK') return { kind: 'blocked', words: [...words, 'blocked'], amountCents: 0, reasonCodes }
  if (mine.some((e) => e.type === 'error')) return { kind: 'failed', words: [...words, 'failed'] }
  return { kind: mine.length > 0 ? 'in_progress' : 'waiting', words }
}

/**
 * Instruction-like spans worth showing. Attack invoices carry a hidden reference marker
 * whose canary is blanked to spaces; what remains (e.g. "Reference: ") is not text anyone wrote.
 */
export function meaningfulSpans<T extends { quote: string }>(spans: readonly T[]): T[] {
  return spans.filter((s) => {
    const q = s.quote.replace(/CS-CANARY-[A-Za-z0-9]+/g, '').trim()
    if (q === '') return false
    // A bare label with nothing after it, such as "Reference:" or "Ref #".
    return !/^[A-Za-z][A-Za-z .-]{0,24}[:#]$/.test(q)
  })
}
