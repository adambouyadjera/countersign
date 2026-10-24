import { describe, expect, it } from 'vitest'
import evalFixture from '@fixtures/eval_job.json'
import redteamFixture from '@fixtures/redteam_job.json'
import type { EvalJob, RedTeamJob } from '@/api/types'
import { errorTypeLabel, errorTypeRows, proofKpis, redteamMarks } from './proof'

const evalJob = evalFixture as EvalJob
const redteam = redteamFixture as RedTeamJob

describe('proof helpers', () => {
  it('builds one row per planted error from the job', () => {
    const rows = errorTypeRows(evalJob)
    expect(rows.map((r) => [r.label, r.expected, r.caught, r.total])).toEqual([
      ['Clean (should pay)', 'PAY', 1, 1],
      ['Bank details mismatch', 'HOLD', 1, 1],
      ['Amount mismatch', 'HOLD', 1, 1],
      ['Unknown vendor', 'BLOCK', 1, 1],
      ['Over autonomy limit', 'HOLD', 1, 1],
    ])
    expect(rows.every((r) => r.misses.length === 0)).toBe(true)
  })

  it('keeps misses visible', () => {
    const job: EvalJob = {
      ...evalJob,
      items: evalJob.items.map((i, n) => (n === 1 ? { ...i, passed: false, actual_outcome: 'PAY', actual_reason_codes: [] } : i)),
      metrics: { ...evalJob.metrics!, by_error_type: { ...evalJob.metrics!.by_error_type, bank_details_mismatch: { caught: 0, missed: 1 } } },
    }
    const row = errorTypeRows(job).find((r) => r.errorType === 'bank_details_mismatch')!
    expect(row.caught).toBe(0)
    expect(row.misses.map((m) => m.invoice_number)).toEqual(['EVAL-002'])
    expect(proofKpis(job).matches).toEqual({ value: 4, of: 5 })
  })

  it('fills the KPI tiles only from reported numbers', () => {
    expect(proofKpis()).toEqual({})
    expect(proofKpis(evalJob, redteam)).toEqual({
      matches: { value: 5, of: 5 },
      unauthorized: 0,
      falseHolds: { value: 0, of: 1 },
      fooled: { value: 8, of: 10 },
    })
    expect(proofKpis({ ...evalJob, metrics: undefined })).toEqual({})
  })

  it('marks each red-team variant, padding unfinished ones as pending', () => {
    const m = redteamMarks(redteam)
    expect(m.unguarded.filter((t) => t === 'bad')).toHaveLength(8)
    expect(m.countersign.every((t) => t === 'held')).toBe(true)
    const partial = redteamMarks({ ...redteam, status: 'running', progress: { completed: 3, total: 12 }, variants: redteam.variants.slice(0, 3) })
    expect(partial.unguarded).toHaveLength(12)
    expect(partial.unguarded.slice(3).every((t) => t === 'pending')).toBe(true)
  })

  it('labels error types', () => {
    expect(errorTypeLabel('over_autonomy_limit')).toBe('Over autonomy limit')
  })
})
