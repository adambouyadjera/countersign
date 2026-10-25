import { describe, expect, it } from 'vitest'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import type { RunEvent } from '@/api/types'
import { reduceEvents } from '@/state/runReducer'
import { elapsedText, laneProgress } from './laneProgress'

const events = parseJsonl(runBothEventsText)
const upTo = (pred: (e: RunEvent) => boolean) => events.slice(0, events.findIndex(pred) + 1)

describe('laneProgress', () => {
  it('waits before a lane has any event', () => {
    expect(laneProgress(reduceEvents([]).lanes.countersign)).toEqual({ step: 'waiting', active: false })
  })

  it('follows the countersign lane through its steps', () => {
    const at = (pred: (e: RunEvent) => boolean) => laneProgress(reduceEvents(upTo(pred)).lanes.countersign)
    expect(at((e) => e.lane === 'countersign' && e.type === 'document.loaded').step).toBe('extracting')
    expect(at((e) => e.type === 'fields.extracted').step).toBe('planning')
    expect(at((e) => e.type === 'plan.proposed').step).toBe('checking policy')
    const hold = at((e) => e.type === 'policy.decision' && e.payload.decision.outcome === 'HOLD')
    expect(hold.step).toBe('holding')
    expect(hold.invoiceId).toBe('INV-003')
    expect(hold.active).toBe(true)
  })

  it('is done, and not active, once the lane reports completed', () => {
    const p = laneProgress(reduceEvents(events).lanes.countersign)
    expect(p.step).toBe('done')
    expect(p.active).toBe(false)
  })

  it('only counts the latest slice of a long-lived run', () => {
    const last = events[events.length - 1]!
    const base = { run_id: last.run_id, mode: 'offline' as const, lane: 'countersign' as const }
    const more: RunEvent[] = [
      { ...base, seq: last.seq + 1, ts: '2026-10-23T18:00:00Z', type: 'lane.status', payload: { status: 'running' } },
      { ...base, seq: last.seq + 2, ts: '2026-10-23T18:00:04Z', type: 'document.loaded', payload: { invoice_id: 'INV-9', document_id: 'd', pages: 1, line_count: 3 } },
    ]
    const p = laneProgress(reduceEvents([...events, ...more]).lanes.countersign)
    expect(p).toMatchObject({ step: 'extracting', invoiceId: 'INV-9', sliceStartedAt: '2026-10-23T18:00:00Z', active: true })
  })
})

describe('elapsedText', () => {
  it('formats seconds and minutes, never negative', () => {
    expect(elapsedText(42_400)).toBe('42s')
    expect(elapsedText(65_000)).toBe('1m 05s')
    expect(elapsedText(-500)).toBe('0s')
  })
})
