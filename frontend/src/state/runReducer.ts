import type { components } from '@/api/schema'
import type { Lane, LaneTally, Mode, RunEvent } from '@/api/types'

type S = components['schemas']

export const LANES: readonly Lane[] = ['unguarded', 'countersign']

/** Where one invoice stands in one lane, derived only from that lane's events. */
export type InvoiceStatus =
  | 'pending'
  | 'extracting'
  | 'cleared'
  | 'paid'
  | 'unknown_account'
  | 'held'
  | 'blocked'
  | 'failed'

export interface InvoiceLaneState {
  status: InvoiceStatus
  transferId?: string
  approvalId?: string
  resolution?: S['ApprovalResolvedPayload']['decision']
}

export type LaneStatus = 'idle' | S['LaneStatusPayload']['status']

/** Events that belong to a lane (everything except lane "system"). */
type OnLane<E> = E extends { lane: infer L }
  ? [Extract<L, Lane>] extends [never]
    ? never
    : Omit<E, 'lane'> & { lane: Extract<L, Lane> }
  : never
export type LaneEvent = OnLane<RunEvent>

export interface LaneState {
  lane: Lane
  status: LaneStatus
  statusDetail?: string
  /** Most recent `mode` seen on this lane's events. */
  mode?: Mode
  /** Unguarded: the agent model. Countersign: the planner model. */
  model?: string
  /** Countersign only: the quarantined extractor model. */
  extractorModel?: string
  /** This lane's events in seq order, one row each in the step list. */
  steps: LaneEvent[]
  invoices: Record<string, InvoiceLaneState>
  tally: LaneTally
}

export interface RunState {
  runId: string | null
  /** Every accepted event, unique by seq and sorted by seq. */
  events: RunEvent[]
  /** Highest seq accepted; what a reconnect would send as Last-Event-ID. */
  lastSeq: number
  started?: S['RunStartedPayload']
  /** Most recent `mode` on a system event. */
  systemMode?: Mode
  completed: boolean
  /** Tallies the server reported in run.completed, kept for comparison with the derived ones. */
  serverTallies?: S['RunTallies']
  /** System-lane events other than run.started/run.completed (errors, inbox). */
  system: RunEvent[]
  lanes: Record<Lane, LaneState>
  /** Count of payment.executed events across both lanes; the ledger refetches when it changes. */
  paymentsExecuted: number
}

export type RunAction = { type: 'reset'; runId: string | null } | { type: 'event'; event: RunEvent }

export const EMPTY_TALLY: LaneTally = {
  paid_count: 0,
  paid_cents: 0,
  held_count: 0,
  blocked_count: 0,
  not_on_file_count: 0,
  not_on_file_cents: 0,
}

function emptyLane(lane: Lane): LaneState {
  return { lane, status: 'idle', steps: [], invoices: {}, tally: EMPTY_TALLY }
}

export function initialRunState(runId: string | null = null): RunState {
  return {
    runId,
    events: [],
    lastSeq: 0,
    completed: false,
    system: [],
    lanes: { unguarded: emptyLane('unguarded'), countersign: emptyLane('countersign') },
    paymentsExecuted: 0,
  }
}

function isLaneEvent(e: RunEvent): e is LaneEvent {
  return e.lane === 'unguarded' || e.lane === 'countersign'
}

function setInvoice(lane: LaneState, invoiceId: string, patch: Partial<InvoiceLaneState>): LaneState {
  const prev = lane.invoices[invoiceId] ?? { status: 'pending' as const }
  return { ...lane, invoices: { ...lane.invoices, [invoiceId]: { ...prev, ...patch } } }
}

function addTally(lane: LaneState, patch: Partial<LaneTally>): LaneState {
  const t = { ...lane.tally }
  for (const [k, v] of Object.entries(patch) as Array<[keyof LaneTally, number]>) t[k] += v
  return { ...lane, tally: t }
}

/** Apply one lane event to its lane. Pure; returns a new LaneState. */
function applyToLane(lane: LaneState, e: LaneEvent): LaneState {
  let next: LaneState = { ...lane, steps: [...lane.steps, e], mode: e.mode ?? lane.mode }
  switch (e.type) {
    case 'lane.status':
      return { ...next, status: e.payload.status, statusDetail: e.payload.detail }
    case 'document.loaded': {
      const cur = next.invoices[e.payload.invoice_id]?.status ?? 'pending'
      return cur === 'pending' ? setInvoice(next, e.payload.invoice_id, { status: 'extracting' }) : next
    }
    case 'fields.extracted':
      next = { ...next, extractorModel: e.payload.extractor_model }
      return next.invoices[e.payload.invoice_id]
        ? next
        : setInvoice(next, e.payload.invoice_id, { status: 'extracting' })
    case 'plan.proposed':
      return { ...next, model: e.payload.planner_model }
    case 'policy.decision': {
      const d = e.payload.decision
      if (d.outcome === 'ALLOW') return setInvoice(next, d.invoice_id, { status: 'cleared' })
      if (d.outcome === 'BLOCK') return addTally(setInvoice(next, d.invoice_id, { status: 'blocked' }), { blocked_count: 1 })
      return setInvoice(next, d.invoice_id, { status: 'held', approvalId: d.approval_id })
    }
    case 'payment.executed': {
      const p = e.payload.payment
      next = setInvoice(next, e.payload.invoice_id, {
        status: p.payee_on_file ? 'paid' : 'unknown_account',
        transferId: p.transfer_id,
      })
      return addTally(next, {
        paid_count: 1,
        paid_cents: p.amount_cents,
        ...(p.payee_on_file ? {} : { not_on_file_count: 1, not_on_file_cents: p.amount_cents }),
      })
    }
    case 'payment.held':
      next = setInvoice(next, e.payload.invoice_id, { status: 'held', approvalId: e.payload.approval_id })
      return addTally(next, { held_count: 1 })
    case 'approval.resolved':
      // A resolution alone moves no money; a payment arrives as its own payment.executed event.
      return setInvoice(next, e.payload.invoice_id, { resolution: e.payload.decision })
    case 'error':
      return e.payload.invoice_id ? setInvoice(next, e.payload.invoice_id, { status: 'failed' }) : next
    default:
      return next
  }
}

/** Apply one event at the end of the stream (its seq is above every seq seen so far). */
function append(state: RunState, e: RunEvent): RunState {
  const base: RunState = { ...state, events: [...state.events, e], lastSeq: Math.max(state.lastSeq, e.seq) }
  if (isLaneEvent(e)) {
    const lanes = { ...base.lanes, [e.lane]: applyToLane(base.lanes[e.lane], e) }
    return {
      ...base,
      lanes,
      paymentsExecuted: base.paymentsExecuted + (e.type === 'payment.executed' ? 1 : 0),
    }
  }
  const systemMode = e.mode ?? base.systemMode
  switch (e.type) {
    case 'run.started': {
      const { models } = e.payload
      return {
        ...base,
        systemMode,
        started: e.payload,
        lanes: {
          unguarded: { ...base.lanes.unguarded, model: base.lanes.unguarded.model ?? models.unguarded },
          countersign: {
            ...base.lanes.countersign,
            model: base.lanes.countersign.model ?? models.planner,
            extractorModel: base.lanes.countersign.extractorModel ?? models.extractor,
          },
        },
      }
    }
    case 'run.completed':
      return { ...base, systemMode, completed: true, serverTallies: e.payload.tallies }
    default:
      return { ...base, systemMode, system: [...base.system, e] }
  }
}

function fold(runId: string | null, events: RunEvent[]): RunState {
  return events.reduce(append, initialRunState(runId))
}

function isRunEvent(e: unknown): e is RunEvent {
  if (typeof e !== 'object' || e === null) return false
  const r = e as Record<string, unknown>
  return typeof r.seq === 'number' && typeof r.type === 'string' && typeof r.lane === 'string' && 'payload' in r
}

/**
 * Reduce SSE events into per-lane state.
 * - Duplicate seq (e.g. replayed after a reconnect) is ignored and returns the same state object.
 * - An event older than the newest one seen is inserted in seq order and the state is re-derived,
 *   so the result never depends on arrival order.
 * - Events for a different run are ignored.
 */
export function runReducer(state: RunState, action: RunAction): RunState {
  if (action.type === 'reset') return initialRunState(action.runId)
  const e = action.event
  if (!isRunEvent(e)) return state
  if (state.runId !== null && e.run_id !== state.runId) return state
  if (state.events.some((x) => x.seq === e.seq)) return state
  if (e.seq > state.lastSeq) return append(state, e)
  const events = [...state.events, e].sort((a, b) => a.seq - b.seq)
  return fold(state.runId, events)
}

/** Fold a whole event list (any order, duplicates allowed). */
export function reduceEvents(events: RunEvent[], runId: string | null = null): RunState {
  return events.reduce((s, event) => runReducer(s, { type: 'event', event }), initialRunState(runId))
}
