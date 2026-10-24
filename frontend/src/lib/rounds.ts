import type { EventOf, PolicyDecision, SourceSpan } from '@/api/types'
import type { LaneEvent } from '@/state/runReducer'

/**
 * Turns the Countersign lane's events for one invoice into the console's "duel":
 * the policy checks grouped into rounds (a round fails if any of its checks
 * failed), plus a planner round once a plan.proposed event exists.
 * Pure: everything comes from the events passed in.
 */

export type CheckCode = PolicyDecision['checks'][number]['code']

export type RoundTopic = 'identity' | 'amount' | 'payee' | 'provenance' | 'instructions' | 'other'

/** A quote from the invoice that a round answers. */
export interface RoundClaim {
  field: string
  quote: string
  page: number
  line: number
}

export interface CheckRound {
  kind: 'checks'
  number: number
  topic: RoundTopic
  title: string
  failed: boolean
  checks: { code: string; passed: boolean; message: string }[]
  claims: RoundClaim[]
}

export interface PlannerRound {
  kind: 'planner'
  number: number
  /** Number of the first failed round, if any: the round the planner never read. */
  neverRead: number | null
  plannerModel: string
  promptHash: string
  /** Handles the plan uses for this invoice, in order of first use. */
  handles: string[]
}

export type Round = CheckRound | PlannerRound

interface TopicSpec {
  topic: RoundTopic
  title: string
  codes: readonly string[]
  /** Extracted fields whose quotes this round answers. */
  fields?: readonly string[]
  /** This round answers the instruction-like spans. */
  instructions?: boolean
}

// Order is the order rounds are fought in. Codes not listed get a round of their own, after these.
const TOPICS: readonly TopicSpec[] = [
  { topic: 'identity', title: 'identity', codes: ['VENDOR_KNOWN', 'NOT_DUPLICATE'] },
  { topic: 'amount', title: 'amount', codes: ['AMOUNT_MATCHES_PO'], fields: ['amount_cents'] },
  {
    topic: 'payee',
    title: 'where the money goes',
    codes: ['PAYEE_ON_FILE', 'VENDOR_HISTORY'],
    fields: ['remit_account_number'],
  },
  { topic: 'provenance', title: 'are the quotes real', codes: ['SPAN_VERIFIED'] },
  {
    topic: 'instructions',
    title: 'orders inside the document',
    codes: ['INSTRUCTIONS_IN_DOCUMENT'],
    instructions: true,
  },
]

function claimOf(field: string, span: SourceSpan): RoundClaim {
  return { field, quote: span.quote, page: span.page, line: span.line }
}

/** The latest event of a type for one invoice. */
function latest<T extends LaneEvent['type']>(
  steps: readonly LaneEvent[],
  type: T,
  match: (e: Extract<LaneEvent, { type: T }>) => boolean,
): Extract<LaneEvent, { type: T }> | undefined {
  for (let i = steps.length - 1; i >= 0; i--) {
    const e = steps[i]!
    if (e.type === type && match(e as Extract<LaneEvent, { type: T }>)) return e as Extract<LaneEvent, { type: T }>
  }
  return undefined
}

export function decisionFor(steps: readonly LaneEvent[], invoiceId: string): PolicyDecision | undefined {
  return latest(steps, 'policy.decision', (e) => e.payload.decision.invoice_id === invoiceId)?.payload.decision
}

export function extractionFor(
  steps: readonly LaneEvent[],
  invoiceId: string,
): EventOf<'fields.extracted'>['payload'] | undefined {
  return latest(steps, 'fields.extracted', (e) => e.payload.invoice_id === invoiceId)?.payload
}

export function planFor(steps: readonly LaneEvent[]): EventOf<'plan.proposed'>['payload'] | undefined {
  return latest(steps, 'plan.proposed', () => true)?.payload
}

/** Rounds for one invoice, numbered from 1. */
export function buildRounds(steps: readonly LaneEvent[], invoiceId: string): Round[] {
  const decision = decisionFor(steps, invoiceId)
  const extracted = extractionFor(steps, invoiceId)
  const plan = planFor(steps)
  const rounds: Round[] = []

  if (decision) {
    const used = new Set<string>()
    const specs: TopicSpec[] = [...TOPICS]
    for (const c of decision.checks) {
      if (!specs.some((s) => s.codes.includes(c.code))) {
        specs.push({ topic: 'other', title: c.code.toLowerCase().replaceAll('_', ' '), codes: [c.code] })
      }
    }
    for (const spec of specs) {
      const checks = decision.checks.filter((c) => spec.codes.includes(c.code) && !used.has(c.code))
      if (checks.length === 0) continue
      checks.forEach((c) => used.add(c.code))
      const claims: RoundClaim[] = []
      for (const f of extracted?.fields ?? []) {
        if (spec.fields?.includes(f.name) && f.span) claims.push(claimOf(f.name, f.span))
      }
      if (spec.instructions) {
        for (const s of extracted?.instruction_like_spans ?? []) claims.push(claimOf('instruction', s))
      }
      rounds.push({
        kind: 'checks',
        number: rounds.length + 1,
        topic: spec.topic,
        title: spec.title,
        failed: checks.some((c) => !c.passed),
        checks: checks.map(({ code, passed, message }) => ({ code, passed, message })),
        claims,
      })
    }
  }

  if (plan) {
    const handles: string[] = []
    for (const step of plan.steps) {
      if (step.invoice_id !== invoiceId) continue
      for (const arg of Object.values(step.args)) {
        if (arg.kind === 'handle' && arg.ref && !handles.includes(arg.ref)) handles.push(arg.ref)
      }
    }
    const firstFailed = rounds.find((r) => r.kind === 'checks' && r.failed)
    rounds.push({
      kind: 'planner',
      number: rounds.length + 1,
      neverRead: firstFailed?.number ?? null,
      plannerModel: plan.planner_model,
      promptHash: plan.prompt_hash,
      handles,
    })
  }

  return rounds
}

/** The invoice the Countersign lane is working on: the one named by its latest invoice event. */
export function currentInvoice(steps: readonly LaneEvent[]): string | undefined {
  for (let i = steps.length - 1; i >= 0; i--) {
    const e = steps[i]!
    switch (e.type) {
      case 'document.loaded':
      case 'fields.extracted':
      case 'payment.executed':
      case 'payment.held':
        return e.payload.invoice_id
      case 'policy.decision':
        return e.payload.decision.invoice_id
      default:
        break
    }
  }
  return undefined
}
