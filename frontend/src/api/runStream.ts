import { runEventsUrl } from './client'
import { getTransport, type RunEventSource } from './transport'
import { RUN_EVENT_TYPES } from './types'

/**
 * One SSE connection per run, shared by every component that subscribes to it.
 *
 * - Reference counted: the connection opens with the first subscriber and closes
 *   when the last one unsubscribes (components unmount, routes change).
 * - Late subscribers are replayed the messages already received, so sharing never
 *   loses history.
 * - On any error the connection is closed (never left half-open for the browser to
 *   retry by itself) and reopened with backoff: 1, 2, 5, then every 10 s. A reopen
 *   passes the last seq as the resume point; subscribers drop repeats by seq.
 */

export type StreamStatus = 'idle' | 'connecting' | 'open' | 'reconnecting'

export interface StreamListener {
  /** One SSE message: its data string, and the browser time it arrived. */
  onMessage: (data: string, at: number) => void
  onStatus: (status: StreamStatus) => void
}

export const RECONNECT_BACKOFF_MS = [1000, 2000, 5000, 10000] as const

interface Entry {
  runId: string
  listeners: Set<StreamListener>
  /** Messages received so far, replayed to late subscribers. */
  history: { data: string; at: number }[]
  source: RunEventSource | null
  status: StreamStatus
  attempt: number
  lastSeq: number
  seen: Set<number>
  retry: ReturnType<typeof setTimeout> | undefined
  /** Bumped on every (re)open; a stale async open or callback checks it and stands down. */
  generation: number
}

const entries = new Map<string, Entry>()

function setStatus(e: Entry, status: StreamStatus) {
  e.status = status
  e.listeners.forEach((l) => l.onStatus(status))
}

function closeSource(e: Entry) {
  e.source?.close()
  e.source = null
  clearTimeout(e.retry)
  e.retry = undefined
}

function scheduleReconnect(e: Entry) {
  closeSource(e)
  setStatus(e, 'reconnecting')
  const delay = RECONNECT_BACKOFF_MS[Math.min(e.attempt, RECONNECT_BACKOFF_MS.length - 1)]!
  e.attempt += 1
  e.retry = setTimeout(() => void open(e), delay)
}

async function open(e: Entry) {
  closeSource(e)
  const gen = ++e.generation
  if (e.status !== 'reconnecting') setStatus(e, 'connecting')
  const transport = await getTransport()
  // Unsubscribed or reopened while the transport loaded.
  if (gen !== e.generation || entries.get(e.runId) !== e) return
  let source: RunEventSource
  try {
    source = transport.openEventSource(runEventsUrl(e.runId), { lastEventId: e.lastSeq || undefined })
  } catch {
    // Couldn't even open (no EventSource, bad URL): treat it as a stream error and back off.
    scheduleReconnect(e)
    return
  }
  e.source = source
  const onMessage = (msg: MessageEvent<string>) => {
    if (gen !== e.generation) return
    const at = Date.now()
    const seq = Number(msg.lastEventId)
    if (Number.isFinite(seq) && seq > 0) {
      // A reopen may replay from the start; keep one copy of each seq in the history.
      if (e.seen.has(seq)) return
      e.seen.add(seq)
      e.lastSeq = Math.max(e.lastSeq, seq)
    }
    e.history.push({ data: msg.data, at })
    e.listeners.forEach((l) => l.onMessage(msg.data, at))
  }
  for (const type of RUN_EVENT_TYPES) source.addEventListener(type, onMessage)
  source.onopen = () => {
    if (gen !== e.generation) return
    e.attempt = 0
    setStatus(e, 'open')
  }
  source.onerror = () => {
    if (gen !== e.generation) return
    // Close it ourselves: a dead or half-open connection must not linger.
    scheduleReconnect(e)
  }
}

/** Subscribe to a run's events. Returns the unsubscribe function. */
export function subscribeRunStream(runId: string, listener: StreamListener): () => void {
  let e = entries.get(runId)
  if (!e) {
    e = { runId, listeners: new Set(), history: [], source: null, status: 'idle', attempt: 0, lastSeq: 0, seen: new Set(), retry: undefined, generation: 0 }
    entries.set(runId, e)
    e.listeners.add(listener)
    void open(e)
  } else {
    e.listeners.add(listener)
    listener.onStatus(e.status)
    for (const h of e.history) listener.onMessage(h.data, h.at)
  }
  const entry = e
  return () => {
    entry.listeners.delete(listener)
    if (entry.listeners.size === 0) {
      entry.generation += 1
      closeSource(entry)
      entries.delete(runId)
    }
  }
}

/** Close the run's connection and open a fresh one now (resets the backoff). */
export function reconnectRunStream(runId: string): void {
  const e = entries.get(runId)
  if (!e) return
  e.attempt = 0
  void open(e)
}

/** How many event-stream connections are open or opening right now (tests and diagnostics). */
export function openStreamCount(): number {
  let n = 0
  for (const e of entries.values()) if (e.source) n += 1
  return n
}

// Diagnostics hook for the e2e connection check; holds no data, only the counter.
if (typeof window !== 'undefined') {
  ;(window as unknown as { __countersignOpenStreams?: () => number }).__countersignOpenStreams = openStreamCount
}
