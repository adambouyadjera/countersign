import { useQueries, useQuery } from '@tanstack/react-query'
import { getBatch, getRun, listInboxItems } from '@/api/client'
import { scoreboard, type ScoreRun } from '@/lib/attackScore'
import { useAttackStore } from '@/state/attackStore'

const RUN_POLL_MS = 3_000

/**
 * The Attack Lab scoreboard, from real data only.
 *
 * There is no endpoint that lists attack batches, so this walks
 * GET /api/inbox → GET /api/runs/{run_id} → GET /api/batches/{batch_id} and keeps
 * batches of kind "attack", plus attacks launched from this browser (their batch
 * came back from POST /api/attacks as kind "attack").
 * TODO: switch to a list endpoint (e.g. GET /api/attacks) once the contract has one.
 */
export function useAttackScoreboard() {
  const launches = useAttackStore((s) => s.launches)
  const inbox = useQuery({ queryKey: ['inbox', 'scoreboard'], queryFn: listInboxItems, refetchInterval: 10_000, retry: false })

  const launchedRuns = new Set(launches.flatMap((l) => (l.runId ? [l.runId] : [])))
  const runIds = [
    ...new Set([...(inbox.data ?? []).flatMap((i) => (i.run_id ? [i.run_id] : [])), ...launchedRuns]),
  ]

  const runs = useQueries({
    queries: runIds.map((id) => ({
      queryKey: ['run', id],
      queryFn: () => getRun(id),
      retry: false,
      // Poll until the run reports its tallies (it does once complete).
      refetchInterval: (q: { state: { data?: { tallies?: unknown } } }) => (q.state.data?.tallies ? false : RUN_POLL_MS),
    })),
  })

  // Batch kinds for inbox runs we didn't launch here (ours are known to be attacks).
  const batchIds = [
    ...new Set(runs.flatMap((r, i) => (r.data && !launchedRuns.has(runIds[i]!) ? [r.data.batch_id] : []))),
  ]
  const batches = useQueries({
    queries: batchIds.map((id) => ({ queryKey: ['batch', id], queryFn: () => getBatch(id), retry: false, staleTime: Infinity })),
  })
  const kind = new Map(batches.flatMap((b) => (b.data ? [[b.data.batch_id, b.data.kind] as const] : [])))

  const known: ScoreRun[] = runs.flatMap((r, i) =>
    r.data
      ? [
          {
            runId: runIds[i]!,
            isAttack: launchedRuns.has(runIds[i]!) || kind.get(r.data.batch_id) === 'attack',
            tallies: r.data.tallies,
          },
        ]
      : [],
  )
  return {
    board: scoreboard(known, launches.filter((l) => !l.runId).length),
    inboxError: inbox.error,
  }
}
