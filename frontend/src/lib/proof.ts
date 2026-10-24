import type { EvalJob, RedTeamJob } from '@/api/types'

type Item = EvalJob['items'][number]

export interface ErrorTypeRow {
  errorType: string
  label: string
  expected: Item['expected_outcome'] | '—'
  caught: number
  total: number
  misses: Item[]
}

const LABELS: Record<string, string> = { clean: 'Clean (should pay)' }

/** "bank_details_mismatch" → "Bank details mismatch". */
export function errorTypeLabel(t: string): string {
  if (LABELS[t]) return LABELS[t]
  const s = t.replaceAll('_', ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** One row per planted error type: counts from metrics.by_error_type, expectations and misses from items. */
export function errorTypeRows(job: EvalJob): ErrorTypeRow[] {
  const byType = job.metrics?.by_error_type ?? {}
  const types = [...new Set([...Object.keys(byType), ...job.items.map((i) => i.error_type)])]
  return types.map((t) => {
    const items = job.items.filter((i) => i.error_type === t)
    const m = byType[t]
    const caught = m ? m.caught : items.filter((i) => i.passed).length
    const missed = m ? m.missed : items.filter((i) => !i.passed).length
    return {
      errorType: t,
      label: errorTypeLabel(t),
      expected: items[0]?.expected_outcome ?? '—',
      caught,
      total: caught + missed,
      misses: items.filter((i) => !i.passed),
    }
  })
}

export interface ProofKpis {
  matches?: { value: number; of: number }
  unauthorized?: number
  falseHolds?: { value: number; of: number }
  fooled?: { value: number; of: number }
}

/** The four tiles. A tile is undefined until its job reports the number. */
export function proofKpis(evalJob?: EvalJob, redteam?: RedTeamJob): ProofKpis {
  const k: ProofKpis = {}
  if (evalJob?.metrics) {
    const passed = evalJob.items.filter((i) => i.passed).length
    k.matches = { value: passed, of: evalJob.items.length }
    k.unauthorized = evalJob.metrics.unauthorized_payments
    const clean = evalJob.items.filter((i) => i.error_type === 'clean').length
    k.falseHolds = { value: evalJob.metrics.false_holds_on_clean, of: clean }
  }
  if (redteam?.metrics) k.fooled = { value: redteam.metrics.unguarded_compromised, of: redteam.metrics.variants }
  return k
}

export type MarkTone = 'bad' | 'held' | 'good' | 'error' | 'pending'

/** Unguarded: compromised = red. Countersign: held or blocked = blue, paid on file = green. Errors dashed. */
export function redteamMarks(job: RedTeamJob): { unguarded: MarkTone[]; countersign: MarkTone[] } {
  const total = Math.max(job.progress.total, job.variants.length)
  const pad = (tones: MarkTone[]) => [...tones, ...Array<MarkTone>(Math.max(0, total - tones.length)).fill('pending')]
  return {
    unguarded: pad(
      job.variants.map((v) => (v.unguarded_outcome === 'compromised' ? 'bad' : v.unguarded_outcome === 'safe' ? 'good' : 'error')),
    ),
    countersign: pad(
      job.variants.map((v) =>
        v.countersign_outcome === 'held' || v.countersign_outcome === 'blocked'
          ? 'held'
          : v.countersign_outcome === 'paid_on_file'
            ? 'good'
            : 'error',
      ),
    ),
  }
}

export function jobDone(job: { status: string } | undefined): boolean {
  return job?.status === 'completed' || job?.status === 'failed'
}
