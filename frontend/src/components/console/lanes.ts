import type { Lane, Run } from '@/api/types'
import type { LaneState, LaneStatus } from '@/state/runReducer'

export const LANE_META: Record<Lane, { letter: string; title: string; short: string }> = {
  unguarded: { letter: 'A', title: 'Unguarded agent', short: 'Unguarded' },
  countersign: { letter: 'B', title: 'Countersign', short: 'Countersign' },
}

export type EffectiveLaneStatus = LaneStatus | 'not_running'

/**
 * One lane's status as the UI shows it: 'not_running' when the run did not start
 * the lane, else the stream's latest lane.status, else what POST /api/runs said.
 */
export function effectiveLaneStatus(
  hasRun: boolean,
  runLane: Run['lanes'][number] | undefined,
  state: LaneState,
): EffectiveLaneStatus {
  if (hasRun && !runLane) return 'not_running'
  if (state.status !== 'idle') return state.status
  return runLane?.status ?? 'idle'
}
