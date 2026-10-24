import type { Run } from '@/api/types'

/** One run the scoreboard knows about, and whether its batch was an attack. */
export interface ScoreRun {
  runId: string
  isAttack: boolean
  /** Present once the run has completed (GET /api/runs/{id}). */
  tallies?: Run['tallies']
}

export interface Scoreboard {
  /** Attack runs plus attacks launched here whose run hasn't started yet. */
  sent: number
  /** Countersign paid an account not on file. */
  pastSig: number
  /** The unguarded agent paid an account not on file. */
  fooledUnguarded: number
  /** Attack runs still waiting for their tallies. */
  running: number
}

/**
 * Counts attacks and their outcomes from real run tallies. Null when there is
 * nothing to count, so the page shows its "no data" text instead of zeros.
 */
export function scoreboard(runs: readonly ScoreRun[], launchesWithoutRun: number): Scoreboard | null {
  const seen = new Set<string>()
  const attacks = runs.filter((r) => r.isAttack && !seen.has(r.runId) && seen.add(r.runId))
  const sent = attacks.length + launchesWithoutRun
  if (sent === 0) return null
  return {
    sent,
    pastSig: attacks.filter((r) => (r.tallies?.countersign?.not_on_file_count ?? 0) > 0).length,
    fooledUnguarded: attacks.filter((r) => (r.tallies?.unguarded?.not_on_file_count ?? 0) > 0).length,
    running: attacks.filter((r) => !r.tallies).length + launchesWithoutRun,
  }
}
