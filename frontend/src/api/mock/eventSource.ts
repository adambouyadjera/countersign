import type { RunEvent } from '../types'
import type { RunEventSource } from '../transport'

/** Fixed gap between replayed events. Not a simulation of work: just pacing so the replay is watchable. */
export const MOCK_EVENT_DELAY_MS = 180

const CONNECTING = 0
const OPEN = 1
const CLOSED = 2

/**
 * Replays a fixture event list as an SSE stream: one message per event, with
 * `event` = type, `lastEventId` = seq and `data` = the JSON RunEvent, exactly
 * as the contract describes. Like the real stream it stays open after
 * run.completed. Events at or below `lastEventId` are skipped, matching the
 * server's Last-Event-ID behaviour.
 */
export class MockEventSource implements RunEventSource {
  readyState = CONNECTING
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  private listeners = new Map<string, Set<(ev: MessageEvent<string>) => void>>()
  private timer: ReturnType<typeof setTimeout> | undefined

  constructor(events: RunEvent[] | null, opts: { lastEventId?: number; delayMs?: number } = {}) {
    const delay = opts.delayMs ?? MOCK_EVENT_DELAY_MS
    const queue = (events ?? []).filter((e) => e.seq > (opts.lastEventId ?? 0))
    this.timer = setTimeout(() => {
      if (this.readyState === CLOSED) return
      if (events === null) {
        // Unknown run: the real server answers 404, which closes an EventSource.
        this.readyState = CLOSED
        this.onerror?.(new Event('error'))
        return
      }
      this.readyState = OPEN
      this.onopen?.(new Event('open'))
      const next = (i: number) => {
        if (this.readyState === CLOSED || i >= queue.length) return
        this.emit(queue[i]!)
        this.timer = setTimeout(() => next(i + 1), delay)
      }
      next(0)
    }, delay)
  }

  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void {
    let set = this.listeners.get(type)
    if (!set) this.listeners.set(type, (set = new Set()))
    set.add(listener)
  }

  close(): void {
    this.readyState = CLOSED
    clearTimeout(this.timer)
  }

  private emit(e: RunEvent): void {
    const msg = new MessageEvent<string>(e.type, { data: JSON.stringify(e), lastEventId: String(e.seq) })
    this.listeners.get(e.type)?.forEach((l) => l(msg))
  }
}
