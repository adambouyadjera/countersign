import { describe, expect, it } from 'vitest'
import replayFixture from '@fixtures/bank_change_replay.json'
import type { Approval, BankChangeReplay, RunEvent } from '@/api/types'
import { lanesFromEvents, liveContext, replayContext, replayDelays, transfers, REPLAY_MAX_GAP_MS, REPLAY_MIN_GAP_MS } from './bankChange'

const replay = replayFixture as unknown as BankChangeReplay
const ctx = replayContext(replay)

describe('lanesFromEvents on the recorded replay', () => {
  const all = lanesFromEvents(replay.events, ctx)

  it('reproduces the normalized lane payments and holds from the events alone', () => {
    for (const lane of ['unguarded', 'countersign'] as const) {
      expect(all[lane].payments.map((p) => [p.transferId, p.toAccount, p.amountCents])).toEqual(
        replay.lanes[lane].payments.map((p) => [p.transfer_id, p.to_display_number, p.amount_cents]),
      )
      expect(all[lane].holds.map((h) => h.invoiceId)).toEqual(replay.lanes[lane].holds.map((h) => h.invoice_id))
      expect(all[lane].tally).toEqual(replay.lanes[lane].tally)
      expect(all[lane].provider).toBe(replay.lanes[lane].provider)
      expect(all[lane].model).toBe(replay.lanes[lane].model)
    }
    expect(all.completed).toBe(true)
  })

  it('marks only the unguarded payment to the letter account as off file', () => {
    const off = all.unguarded.payments.filter((p) => p.offFile)
    expect(off).toHaveLength(replay.summary.letter_payment_count)
    expect(off[0]!.toAccount).toBe(replay.letter!.requested_account_display)
    expect(off[0]!.onFileAccount).toBe(replay.letter!.account_on_file_display)
    expect(off[0]!.toLetterAccount).toBe(true)
    expect(all.countersign.payments.some((p) => p.offFile)).toBe(false)
  })

  it('carries the hold reason and both accounts', () => {
    const h = all.countersign.holds[0]!
    const recorded = replay.lanes.countersign.holds[0]!
    expect(h.reasonCodes).toEqual(recorded.reason_codes)
    expect(h.onFileAccount).toBe(recorded.account_on_file_display)
    expect(h.letterAccount).toBe(recorded.letter_account_display)
    expect(h.paidLater).toBe(false)
  })

  it('shows a partial state part-way through and nothing before the first payment', () => {
    expect(lanesFromEvents([], ctx).unguarded.payments).toEqual([])
    const firstPay = replay.events.findIndex((e) => e.type === 'payment.executed')
    const part = lanesFromEvents(replay.events.slice(0, firstPay + 1), ctx)
    expect(part.unguarded.payments).toHaveLength(1)
    expect(part.countersign.payments).toHaveLength(0)
    expect(part.completed).toBe(false)
    expect(part.unguarded.tally).toBeUndefined()
  })

  it('lists every transfer from both lanes in seq order', () => {
    const t = transfers(all)
    expect(t).toHaveLength(replay.lanes.unguarded.payments.length + replay.lanes.countersign.payments.length)
    expect(t.map((x) => x.seq)).toEqual([...t.map((x) => x.seq)].sort((a, b) => a - b))
  })
})

describe('liveContext', () => {
  it('takes accounts from the approval and never names a letter account without a hold', () => {
    const inv = replay.batch.invoices
    expect(liveContext(inv, {}, [], 'run-x').letterAccount).toBeUndefined()
    const a = { ...(replay.lanes.countersign.holds[0]!.approval as Approval), run_id: 'run-x' }
    const c = liveContext(inv, {}, [a], 'run-x')
    expect(c.letterAccount).toBe(a.requested_account_display)
    expect(c.holdAccounts[a.invoice_id]).toEqual({ onFile: a.account_on_file_display, letter: a.requested_account_display })
    expect(liveContext(inv, {}, [a], 'run-other').letterAccount).toBeUndefined()
  })

  it('falls back to payee_on_file when no account on file is known', () => {
    const e = replay.events.find(
      (x) => x.type === 'payment.executed' && x.lane === 'unguarded' && !x.payload.payment.payee_on_file,
    ) as RunEvent
    const l = lanesFromEvents([e], liveContext(replay.batch.invoices, {}, [], 'run-x'))
    expect(l.unguarded.payments[0]!.offFile).toBe(true)
    expect(l.unguarded.payments[0]!.onFileAccount).toBeUndefined()
  })
})

describe('replayDelays', () => {
  it('speeds up the original gaps within the bounds', () => {
    const d = replayDelays(replay.events)
    expect(d).toHaveLength(replay.events.length)
    expect(Math.min(...d)).toBeGreaterThanOrEqual(REPLAY_MIN_GAP_MS)
    expect(Math.max(...d)).toBeLessThanOrEqual(REPLAY_MAX_GAP_MS)
    // The recording is about six minutes; the replay should take seconds.
    expect(d.reduce((a, b) => a + b, 0)).toBeLessThan(30_000)
  })
})
