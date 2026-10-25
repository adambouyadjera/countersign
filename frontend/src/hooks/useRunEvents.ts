import { useCallback, useEffect, useReducer } from 'react'
import { reconnectRunStream, subscribeRunStream, type StreamStatus } from '@/api/runStream'
import type { RunEvent } from '@/api/types'
import { maskCanary } from '@/lib/canary'
import { initialRunState, runReducer, type RunState } from '@/state/runReducer'

export type { StreamStatus } from '@/api/runStream'

export interface RunEventsResult {
  state: RunState
  stream: StreamStatus
  /** Messages that arrived but were not valid JSON. */
  malformed: number
  /** Browser clock (ms) when the last event arrived on this subscription; null before the first. */
  lastEventAt: number | null
  /** Close the run's stream and open a fresh one now (state is kept; replayed events are dropped by seq). */
  reconnect: () => void
}

interface HookState extends Omit<RunEventsResult, 'reconnect'> {
  key: string
}

type HookAction =
  | { type: 'reset'; key: string; runId: string | null }
  | { type: 'event'; key: string; event: RunEvent; at: number }
  | { type: 'stream'; key: string; stream: StreamStatus }
  | { type: 'malformed'; key: string }

function init([key, runId]: [string, string | null]): HookState {
  return { key, state: initialRunState(runId), stream: runId ? 'connecting' : 'idle', malformed: 0, lastEventAt: null }
}

function hookReducer(s: HookState, a: HookAction): HookState {
  if (a.type === 'reset') return init([a.key, a.runId])
  // Drop anything from a subscription that has since been replaced.
  if (a.key !== s.key) return s
  switch (a.type) {
    case 'event': {
      const state = runReducer(s.state, { type: 'event', event: a.event })
      return state === s.state ? s : { ...s, state, lastEventAt: a.at }
    }
    case 'stream':
      return a.stream === s.stream ? s : { ...s, stream: a.stream }
    case 'malformed':
      return { ...s, malformed: s.malformed + 1 }
  }
}

/**
 * Subscribe to GET /api/runs/{runId}/events and reduce the stream with runReducer.
 *
 * The connection itself is shared per run (api/runStream): every component watching
 * the same run uses one EventSource, closed when the last of them unmounts, and
 * reopened with backoff after an error. Each subscriber keeps its own reduced state;
 * the reducer drops any seq it has already seen, so replays are harmless.
 *
 * Changing `session` resets the state and resubscribes even if runId is unchanged.
 */
export function useRunEvents(runId: string | null, session = 0): RunEventsResult {
  const key = `${session}|${runId ?? ''}`
  const [hs, dispatch] = useReducer(hookReducer, [key, runId] as [string, string | null], init)
  // Reset during render when the subscription changes, so stale state is never painted.
  if (hs.key !== key) dispatch({ type: 'reset', key, runId })

  useEffect(() => {
    if (!runId) return
    return subscribeRunStream(runId, {
      onMessage: (data, at) => {
        let event: RunEvent
        try {
          event = JSON.parse(maskCanary(data)) as RunEvent
        } catch {
          dispatch({ type: 'malformed', key })
          return
        }
        dispatch({ type: 'event', key, event, at })
      },
      onStatus: (stream) => dispatch({ type: 'stream', key, stream }),
    })
  }, [runId, key])

  const reconnect = useCallback(() => {
    if (runId) reconnectRunStream(runId)
  }, [runId])

  return { state: hs.state, stream: hs.stream, malformed: hs.malformed, lastEventAt: hs.lastEventAt, reconnect }
}
