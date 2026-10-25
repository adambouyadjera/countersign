import { useCallback, useEffect, useReducer, useState } from 'react'
import { runEventsUrl } from '@/api/client'
import { EVENT_SOURCE_CLOSED, getTransport, type RunEventSource } from '@/api/transport'
import { RUN_EVENT_TYPES, type RunEvent } from '@/api/types'
import { maskCanary } from '@/lib/canary'
import { initialRunState, runReducer, type RunState } from '@/state/runReducer'

export type StreamStatus = 'idle' | 'connecting' | 'open' | 'reconnecting'

export interface RunEventsResult {
  state: RunState
  stream: StreamStatus
  /** Messages that arrived but were not valid JSON. */
  malformed: number
  /** Browser clock (ms) when the last event arrived on this subscription; null before the first. */
  lastEventAt: number | null
  /** Close the stream and open a fresh one now (state is kept; replayed events are dropped by seq). */
  reconnect: () => void
}

const BACKOFF_MS = [500, 1000, 2000, 4000, 8000]

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
 * Reconnects: while the browser's EventSource is retrying on its own it sends
 * Last-Event-ID itself. If the stream is closed outright (HTTP error) a new
 * EventSource is opened with backoff, passing the last seq to the transport.
 * Either way the reducer drops any seq it has already seen, so a replay from
 * the start is harmless.
 *
 * Changing `session` resets the state and resubscribes even if runId is unchanged.
 */
export function useRunEvents(runId: string | null, session = 0): RunEventsResult {
  const key = `${session}|${runId ?? ''}`
  const [hs, dispatch] = useReducer(hookReducer, [key, runId] as [string, string | null], init)
  // Reset during render when the subscription changes, so stale state is never painted.
  if (hs.key !== key) dispatch({ type: 'reset', key, runId })
  // Bumped by reconnect(): re-runs the subscription effect without resetting the reduced state.
  const [attemptNonce, setAttemptNonce] = useState(0)
  const reconnect = useCallback(() => setAttemptNonce((n) => n + 1), [])

  useEffect(() => {
    if (!runId) return
    let disposed = false
    let es: RunEventSource | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let lastSeen = 0

    const onMessage = (msg: MessageEvent<string>) => {
      let event: RunEvent
      try {
        event = JSON.parse(maskCanary(msg.data)) as RunEvent
      } catch {
        dispatch({ type: 'malformed', key })
        return
      }
      if (typeof event.seq === 'number') lastSeen = Math.max(lastSeen, event.seq)
      dispatch({ type: 'event', key, event, at: Date.now() })
    }

    const connect = async () => {
      const transport = await getTransport()
      if (disposed) return
      const source = transport.openEventSource(runEventsUrl(runId), { lastEventId: lastSeen || undefined })
      es = source
      for (const type of RUN_EVENT_TYPES) source.addEventListener(type, onMessage)
      source.onopen = () => {
        attempt = 0
        dispatch({ type: 'stream', key, stream: 'open' })
      }
      source.onerror = () => {
        if (disposed) return
        dispatch({ type: 'stream', key, stream: 'reconnecting' })
        if (source.readyState !== EVENT_SOURCE_CLOSED) return // the browser retries by itself
        source.close()
        const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]
        attempt += 1
        retry = setTimeout(() => void connect(), delay)
      }
    }

    dispatch({ type: 'stream', key, stream: 'connecting' })
    void connect()
    return () => {
      disposed = true
      clearTimeout(retry)
      es?.close()
    }
  }, [runId, key, attemptNonce])

  return { state: hs.state, stream: hs.stream, malformed: hs.malformed, lastEventAt: hs.lastEventAt, reconnect }
}
