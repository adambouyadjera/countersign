import { describe, expect, it } from 'vitest'
import runFixture from '@fixtures/run.json'
import type { Run } from '@/api/types'
import { scoreboard } from './attackScore'

const tallies = (runFixture as Run).tallies // unguarded: 1 not on file; countersign: 0

describe('scoreboard', () => {
  it('is null when nothing has been sent, so the page shows its "no data" text', () => {
    expect(scoreboard([], 0)).toBeNull()
    expect(scoreboard([{ runId: 'r1', isAttack: false, tallies }], 0)).toBeNull()
  })

  it('counts attack runs from the fixture tallies: fooled the unguarded agent, not past Sig', () => {
    expect(scoreboard([{ runId: 'run-example-both', isAttack: true, tallies }], 0)).toEqual({
      sent: 1,
      pastSig: 0,
      fooledUnguarded: 1,
      running: 0,
    })
  })

  it('counts a run that got past Countersign, ignores non-attack runs and duplicates', () => {
    const past = { ...tallies!, countersign: { ...tallies!.countersign!, not_on_file_count: 1, not_on_file_cents: 64000 } }
    const s = scoreboard(
      [
        { runId: 'a', isAttack: true, tallies: past },
        { runId: 'a', isAttack: true, tallies: past },
        { runId: 'b', isAttack: false, tallies: past },
      ],
      0,
    )
    expect(s).toEqual({ sent: 1, pastSig: 1, fooledUnguarded: 1, running: 0 })
  })

  it('counts runs without tallies and launches without a run as sent and still running', () => {
    expect(scoreboard([{ runId: 'a', isAttack: true }], 2)).toEqual({ sent: 3, pastSig: 0, fooledUnguarded: 0, running: 3 })
  })
})
