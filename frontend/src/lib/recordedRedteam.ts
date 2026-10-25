import type { RedTeamJob } from '@/api/types'

/**
 * The recorded red-team study, served by GET /api/redteam/{job_id} (jobs persist across
 * demo resets). No endpoint lists jobs, so the id comes from the backend's write-up:
 * backend/docs/redteam_results.md (completed 40/40, dry-run bank).
 */
export const RECORDED_REDTEAM_JOB_ID = 'redteam-dcb67a52ba40438d8dfcd1af4a36ad8b'

/** "40 different attacks. The planner's input was identical." only when the data says so. */
export function redteamHeadline(job: RedTeamJob): string {
  const m = job.metrics
  const attacks = m?.variants ?? job.variants.length
  const first = `${attacks} different attacks.`
  if (!m) return first
  return m.distinct_planner_prompts === 1
    ? `${first} The planner's input was identical.`
    : `${first} The planner saw ${m.distinct_planner_prompts} distinct inputs.`
}
