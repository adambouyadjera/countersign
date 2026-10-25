import type { LaneEvent, LaneState } from '@/state/runReducer'

/** What a lane is doing, in the words the Console shows. */
export type LaneStep =
  | 'waiting'
  | 'received'
  | 'extracting'
  | 'planning'
  | 'checking policy'
  | 'paying'
  | 'holding'
  | 'paid'
  | 'held'
  | 'blocked'
  | 'error'
  | 'done'
  | 'failed'

export interface LaneProgress {
  step: LaneStep
  /** The invoice the step is about, when the event names one. */
  invoiceId?: string
  /** Server time the current processing slice began (its lane.status running, else its first event). */
  sliceStartedAt?: string
  /** Server time of the event that set the step. */
  stepAt?: string
  /** The lane is still working: its latest lane.status is running, or it has events but no final status yet. */
  active: boolean
}

function invoiceOf(e: LaneEvent): string | undefined {
  if (e.type === 'policy.decision') return e.payload.decision.invoice_id
  const p = e.payload as { invoice_id?: unknown }
  return typeof p.invoice_id === 'string' ? p.invoice_id : undefined
}

/** The step an event moves its lane to; undefined for events that don't change it. */
function stepFor(e: LaneEvent): LaneStep | undefined {
  switch (e.type) {
    case 'lane.status':
      return e.payload.status === 'running' ? 'received' : e.payload.status === 'completed' ? 'done' : 'failed'
    case 'document.loaded':
      return 'extracting'
    case 'fields.extracted':
      return 'planning'
    case 'plan.proposed':
      return 'checking policy'
    case 'agent.message':
      return 'planning'
    case 'agent.tool_call':
      return /pay|transfer/i.test(e.payload.tool) ? 'paying' : 'planning'
    case 'policy.decision':
      return e.payload.decision.outcome === 'ALLOW' ? 'paying' : e.payload.decision.outcome === 'HOLD' ? 'holding' : 'blocked'
    case 'payment.executed':
      return 'paid'
    case 'payment.held':
      return 'held'
    case 'error':
      return 'error'
    default:
      return undefined
  }
}

/**
 * A lane's current step from its own SSE events. A long-lived autopilot run reports
 * each processing slice with lane.status running … completed, so only the latest
 * slice counts.
 */
export function laneProgress(lane: LaneState): LaneProgress {
  const steps = lane.steps
  let start = -1
  for (let i = steps.length - 1; i >= 0; i--) {
    const e = steps[i]!
    if (e.type === 'lane.status' && e.payload.status === 'running') {
      start = i
      break
    }
  }
  const slice = start >= 0 ? steps.slice(start) : steps
  if (slice.length === 0) return { step: 'waiting', active: false }

  let step: LaneStep = 'received'
  let at: LaneEvent | undefined
  for (const e of slice) {
    const s = stepFor(e)
    if (s) {
      step = s
      at = e
    }
  }
  const last = [...slice].reverse().find((e) => e.type === 'lane.status')
  const finished = last?.type === 'lane.status' && last.payload.status !== 'running'
  return {
    step,
    invoiceId: at ? invoiceOf(at) : undefined,
    sliceStartedAt: slice[0]!.ts,
    stepAt: at?.ts,
    active: !finished,
  }
}

/** "1m 05s" / "42s" from milliseconds; never negative (server and browser clocks can differ slightly). */
export function elapsedText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
}

/** No event for this long while a lane is working: say so instead of looking frozen. */
export const STALE_AFTER_MS = 15_000
