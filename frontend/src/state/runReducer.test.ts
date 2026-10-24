import { describe, expect, it } from 'vitest'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import type { EventOf, RunEvent } from '@/api/types'
import { initialRunState, reduceEvents, runReducer, type RunState } from './runReducer'

const events = parseJsonl(runBothEventsText)
const RUN_ID = events[0]!.run_id

/** A deterministic shuffle so the test is reproducible. */
function shuffled<T>(xs: T[], seed = 7): T[] {
  const out = [...xs]
  let s = seed
  for (let i = out.length - 1; i > 0; i--) {
    s = (s * 1103515245 + 12345) % 2 ** 31
    const j = s % (i + 1)
    ;[out[i], out[j]] = [out[j]!, out[i]!]
  }
  return out
}

/** The parts of the state the UI renders, without the arrival-order-dependent event array identity. */
function view(s: RunState) {
  return {
    seqs: s.events.map((e) => e.seq),
    lastSeq: s.lastSeq,
    completed: s.completed,
    serverTallies: s.serverTallies,
    system: s.system.map((e) => e.seq),
    paymentsExecuted: s.paymentsExecuted,
    lanes: Object.fromEntries(
      Object.entries(s.lanes).map(([k, l]) => [k, { ...l, steps: l.steps.map((e) => e.seq) }]),
    ),
  }
}

describe('runReducer: fixture replay (run_both.events.jsonl)', () => {
  const state = reduceEvents(events, RUN_ID)
  const completed = events.find((e): e is EventOf<'run.completed'> => e.type === 'run.completed')!

  it('derives per-lane tallies that equal the run.completed tallies', () => {
    expect(state.lanes.countersign.tally).toEqual(completed.payload.tallies.countersign)
    expect(state.lanes.unguarded.tally).toEqual(completed.payload.tallies.unguarded)
  })

  it('counts Countersign as 4 paid, 1 held, 0 to unknown accounts', () => {
    const t = state.lanes.countersign.tally
    expect([t.paid_count, t.held_count, t.not_on_file_count]).toEqual([4, 1, 0])
  })

  it('counts the unguarded lane as 5 paid with 1 sent to an unknown account', () => {
    const t = state.lanes.unguarded.tally
    expect([t.paid_count, t.held_count, t.not_on_file_count, t.not_on_file_cents]).toEqual([5, 0, 1, 64000])
  })

  it('tracks per-invoice status in each lane without revealing anything the events do not say', () => {
    expect(state.lanes.unguarded.invoices['INV-003']).toMatchObject({
      status: 'unknown_account',
      transferId: 'example-nessie-transfer-3',
    })
    expect(state.lanes.countersign.invoices['INV-003']).toMatchObject({
      status: 'held',
      approvalId: 'approval-INV-003',
      resolution: 'reject',
    })
    for (const id of ['INV-001', 'INV-002', 'INV-004', 'INV-005']) {
      expect(state.lanes.countersign.invoices[id]?.status).toBe('paid')
      expect(state.lanes.unguarded.invoices[id]?.status).toBe('paid')
    }
  })

  it('takes lane status, models and mode from events', () => {
    expect(state.lanes.unguarded).toMatchObject({ status: 'completed', model: 'example-offline-unguarded', mode: 'offline' })
    expect(state.lanes.countersign).toMatchObject({
      status: 'completed',
      model: 'example-offline-planner',
      extractorModel: 'example-offline-extractor',
      mode: 'offline',
    })
    expect(state.completed).toBe(true)
    expect(state.lastSeq).toBe(54)
    expect(state.paymentsExecuted).toBe(9)
  })

  it('routes events to their lane and keeps system events apart', () => {
    expect(state.lanes.unguarded.steps.every((e) => e.lane === 'unguarded')).toBe(true)
    expect(state.lanes.countersign.steps.every((e) => e.lane === 'countersign')).toBe(true)
    expect(state.system.map((e) => e.type)).toEqual(['inbox.received', 'error'])
  })

  it('shows intermediate statuses mid-run', () => {
    const mid = reduceEvents(events.filter((e) => e.seq <= 23), RUN_ID)
    expect(mid.lanes.countersign.invoices['INV-003']?.status).toBe('extracting')
    expect(mid.lanes.unguarded.invoices['INV-003']?.status).toBe('unknown_account')
    expect(mid.lanes.unguarded.invoices['INV-004']).toBeUndefined()
    expect(mid.lanes.countersign.tally.paid_count).toBe(0)
  })
})

describe('runReducer: delivery guarantees', () => {
  it('ignores a duplicate seq and returns the same state object', () => {
    const once = reduceEvents(events.slice(0, 20), RUN_ID)
    const again = runReducer(once, { type: 'event', event: events[9]! })
    expect(again).toBe(once)
  })

  it('ignores a full replay after a reconnect', () => {
    const once = reduceEvents(events, RUN_ID)
    const twice = events.reduce((s, event) => runReducer(s, { type: 'event', event }), once)
    expect(twice).toBe(once)
  })

  it('ignores a duplicate seq even if its payload differs', () => {
    const s = reduceEvents(events, RUN_ID)
    const forged = { ...events[20]!, payload: { ...events[20]!.payload } } as RunEvent
    expect(runReducer(s, { type: 'event', event: forged })).toBe(s)
  })

  it('produces the same state from out-of-order delivery', () => {
    const ordered = reduceEvents(events, RUN_ID)
    for (const seed of [1, 7, 42]) {
      expect(view(reduceEvents(shuffled(events, seed), RUN_ID))).toEqual(view(ordered))
    }
  })

  it('re-derives status when a late event slots in before newer ones', () => {
    // payment.executed (seq 9) arrives before document.loaded (seq 6) for INV-001.
    const late = events.filter((e) => e.seq <= 9 && e.seq !== 6)
    const s = reduceEvents([...late, events[5]!], RUN_ID)
    expect(events[5]!.seq).toBe(6)
    expect(s.lanes.unguarded.invoices['INV-001']?.status).toBe('paid')
    expect(s.lanes.unguarded.steps.map((e) => e.seq)).toEqual([4, 6, 7, 8, 9])
    expect(s.lastSeq).toBe(9)
  })

  it('ignores events for another run', () => {
    const s = initialRunState(RUN_ID)
    const other = { ...events[0]!, run_id: 'run-other' } as RunEvent
    expect(runReducer(s, { type: 'event', event: other })).toBe(s)
  })

  it('ignores malformed events', () => {
    const s = initialRunState(RUN_ID)
    expect(runReducer(s, { type: 'event', event: { nope: true } as unknown as RunEvent })).toBe(s)
  })

  it('resets', () => {
    const s = reduceEvents(events, RUN_ID)
    expect(runReducer(s, { type: 'reset', runId: 'x' })).toEqual(initialRunState('x'))
  })
})
