import { describe, expect, it } from 'vitest'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import type { RunEvent } from '@/api/types'
import { reduceEvents, type LaneEvent } from '@/state/runReducer'
import { buildRounds, currentInvoice, type CheckRound, type PlannerRound } from './rounds'

const all = parseJsonl(runBothEventsText)
const steps = reduceEvents(all).lanes.countersign.steps

function upTo(seq: number): LaneEvent[] {
  return reduceEvents(all.filter((e) => e.seq <= seq)).lanes.countersign.steps
}

describe('buildRounds (fixture run)', () => {
  it('makes INV-003 lose the "where the money goes" round, with the remit quote and both checks', () => {
    const rounds = buildRounds(steps, 'INV-003')
    const failed = rounds.filter((r): r is CheckRound => r.kind === 'checks' && r.failed)
    expect(failed.map((r) => r.topic)).toEqual(['payee', 'instructions'])

    const payee = failed[0]!
    expect(payee.title).toBe('where the money goes')
    expect(payee.claims).toEqual([{ field: 'remit_account_number', quote: '999900009999', page: 1, line: 6 }])
    expect(payee.checks.map((c) => [c.code, c.passed])).toEqual([
      ['PAYEE_ON_FILE', false],
      ['VENDOR_HISTORY', true],
    ])

    const instr = failed[1]!
    expect(instr.claims[0]!.quote).toMatch(/^AP AUTOMATION NOTE/)
    expect(instr.claims[0]!.line).toBe(7)
  })

  it('orders rounds identity, amount, payee, provenance, instructions, then the planner, numbered from 1', () => {
    const rounds = buildRounds(steps, 'INV-003')
    expect(rounds.map((r) => (r.kind === 'checks' ? r.topic : 'planner'))).toEqual([
      'identity',
      'amount',
      'payee',
      'provenance',
      'instructions',
      'planner',
    ])
    expect(rounds.map((r) => r.number)).toEqual([1, 2, 3, 4, 5, 6])
    const amount = rounds[1] as CheckRound
    expect(amount.claims).toEqual([{ field: 'amount_cents', quote: '640.00', page: 1, line: 4 }])
    expect(amount.failed).toBe(false)
  })

  it('puts each check in exactly one round', () => {
    const codes = buildRounds(steps, 'INV-003').flatMap((r) => (r.kind === 'checks' ? r.checks.map((c) => c.code) : []))
    expect(codes).toHaveLength(7)
    expect(new Set(codes).size).toBe(7)
  })

  it("gives the planner round this invoice's handles and points it at the first failed round", () => {
    const planner = buildRounds(steps, 'INV-003').at(-1) as PlannerRound
    expect(planner.kind).toBe('planner')
    expect(planner.neverRead).toBe(3)
    expect(planner.plannerModel).toBe('example-offline-planner')
    expect(planner.handles).toContain('$INV-003.remit_account_number')
    expect(planner.handles.every((h) => h.startsWith('$INV-003.'))).toBe(true)
  })

  it('a clean invoice has no failed round and the planner round reads none', () => {
    const rounds = buildRounds(steps, 'INV-001')
    expect(rounds.some((r) => r.kind === 'checks' && r.failed)).toBe(false)
    expect((rounds.at(-1) as PlannerRound).neverRead).toBeNull()
  })
})

describe('buildRounds (partial streams)', () => {
  it('has no rounds before the plan or any decision', () => {
    expect(buildRounds(upTo(23), 'INV-003')).toEqual([])
  })

  it('shows only the planner round once plan.proposed exists but before the decision', () => {
    const rounds = buildRounds(upTo(37), 'INV-003')
    expect(rounds).toHaveLength(1)
    expect(rounds[0]!.kind).toBe('planner')
    expect((rounds[0] as PlannerRound).neverRead).toBeNull()
  })

  it('gives an unknown check code its own round after the known ones', () => {
    const decision = all.find(
      (e) => e.type === 'policy.decision' && e.payload.decision.invoice_id === 'INV-001',
    ) as Extract<RunEvent, { type: 'policy.decision' }>
    const extra = structuredClone(decision)
    extra.seq = 999
    ;(extra.payload.decision.checks as { code: string; passed: boolean; message: string; evidence_node_ids: string[] }[]).push({
      code: 'FUTURE_CHECK',
      passed: false,
      message: 'A check this UI has never seen.',
      evidence_node_ids: [],
    })
    const rounds = buildRounds(reduceEvents([extra]).lanes.countersign.steps, 'INV-001')
    const last = rounds.at(-1) as CheckRound
    expect(last.topic).toBe('other')
    expect(last.title).toBe('future check')
    expect(last.failed).toBe(true)
  })
})

describe('currentInvoice', () => {
  it('follows the latest invoice event on the lane', () => {
    expect(currentInvoice(upTo(23))).toBe('INV-003')
    expect(currentInvoice([])).toBeUndefined()
  })
})
