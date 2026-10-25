// The two recorded planted-errors runs. Their results come from GET /api/eval/{job_id};
// no endpoint lists eval jobs, so the job ids and the answer-key hash are read from the
// backend's committed comparison record (backend/docs/eval_semantic_comparison.json).
import comparison from '@evidence/eval_semantic_comparison.json'
import type { EvalJob } from '@/api/types'

export const RECORDED_EVALS: { title: string; jobId: string }[] = [
  { title: 'v1 policy (before fix)', jobId: comparison.before_job_id },
  { title: 'after semantic duplicate check', jobId: comparison.after_job_id },
]

export const ANSWER_KEY = {
  sha256: comparison.answer_key_sha256 as string | undefined,
  unchanged: comparison.answer_key_unchanged as boolean | undefined,
}

/** True when both jobs scored exactly the same item ids. */
export function sameItems(a: EvalJob | undefined, b: EvalJob | undefined): boolean {
  if (!a || !b) return false
  const ids = (j: EvalJob) => j.items.map((i) => i.item_id).join(',')
  return ids(a) === ids(b)
}
