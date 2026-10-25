import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import { setTransportForTests, type RunEventSource } from '@/api/transport'
import { openStreamCount } from '@/api/runStream'
import { useRunEvents } from './useRunEvents'

const events = parseJsonl(runBothEventsText)
const RUN_ID = events[0]!.run_id

class FakeES implements RunEventSource {
  readyState = 0
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>()
  constructor(
    public url: string,
    public lastEventId?: number,
  ) {}
  addEventListener(type: string, l: (ev: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l])
  }
  close() {
    this.readyState = 2
  }
  open() {
    this.readyState = 1
    this.onopen?.(new Event('open'))
  }
  send(e: (typeof events)[number]) {
    const m = new MessageEvent<string>(e.type, { data: JSON.stringify(e), lastEventId: String(e.seq) })
    this.listeners.get(e.type)?.forEach((l) => l(m))
  }
  fail() {
    this.readyState = 2
    this.onerror?.(new Event('error'))
  }
}

let opened: FakeES[] = []

beforeEach(() => {
  opened = []
  setTransportForTests({
    fetch: () => Promise.reject(new Error('unused')),
    openEventSource: (url, opts) => {
      const es = new FakeES(url, opts?.lastEventId)
      opened.push(es)
      return es
    },
  })
})

afterEach(() => {
  setTransportForTests(null)
  vi.useRealTimers()
})

async function flush() {
  await act(async () => {
    await Promise.resolve()
  })
}

describe('useRunEvents', () => {
  it('is idle without a run id', () => {
    const { result } = renderHook(() => useRunEvents(null))
    expect(result.current.stream).toBe('idle')
    expect(opened).toHaveLength(0)
  })

  it('subscribes to the run events URL and reduces messages', async () => {
    const { result } = renderHook(() => useRunEvents(RUN_ID))
    await flush()
    expect(opened).toHaveLength(1)
    expect(opened[0]!.url).toBe(`/api/runs/${RUN_ID}/events`)
    act(() => opened[0]!.open())
    expect(result.current.stream).toBe('open')
    act(() => events.forEach((e) => opened[0]!.send(e)))
    expect(result.current.state.lanes.countersign.tally.held_count).toBe(1)
    expect(result.current.state.lastSeq).toBe(54)
  })

  it('reconnects after the stream closes, resumes from the last seq, and drops replayed events', async () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useRunEvents(RUN_ID))
    await flush()
    const first = opened[0]!
    act(() => {
      first.open()
      events.slice(0, 30).forEach((e) => first.send(e))
      first.fail()
    })
    expect(result.current.stream).toBe('reconnecting')
    expect(first.readyState).toBe(2)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999)
    })
    expect(opened).toHaveLength(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
    expect(opened).toHaveLength(2)
    const second = opened[1]!
    expect(second.lastEventId).toBe(30)
    // A server that ignores the resume point replays from the start; nothing is double counted.
    act(() => {
      second.open()
      events.forEach((e) => second.send(e))
    })
    const s = result.current.state
    expect(s.events).toHaveLength(events.length)
    expect(s.lanes.unguarded.tally.paid_count).toBe(5)
    expect(s.lanes.countersign.tally.paid_count).toBe(4)
    expect(result.current.stream).toBe('open')
  })

  it('counts malformed messages without breaking the stream', async () => {
    const { result } = renderHook(() => useRunEvents(RUN_ID))
    await flush()
    act(() => {
      opened[0]!.listeners.get('error')?.forEach((l) => l(new MessageEvent('error', { data: '{not json' })))
      opened[0]!.send(events[0]!)
    })
    expect(result.current.malformed).toBe(1)
    expect(result.current.state.started).toBeDefined()
  })

  it('closes the stream on unmount and resets on a new session', async () => {
    const { result, rerender, unmount } = renderHook(({ s }) => useRunEvents(RUN_ID, s), { initialProps: { s: 0 } })
    await flush()
    act(() => opened[0]!.send(events[0]!))
    expect(result.current.state.events).toHaveLength(1)
    rerender({ s: 1 })
    expect(result.current.state.events).toHaveLength(0)
    expect(opened[0]!.readyState).toBe(2)
    await flush()
    expect(opened).toHaveLength(2)
    unmount()
    expect(opened[1]!.readyState).toBe(2)
  })

  it('reconnect() opens a fresh stream, keeps the events and records when the last one arrived', async () => {
    const { result } = renderHook(() => useRunEvents(RUN_ID))
    await flush()
    act(() => {
      opened[0]!.open()
      opened[0]!.send(events[0]!)
      opened[0]!.send(events[1]!)
    })
    expect(result.current.state.events).toHaveLength(2)
    expect(result.current.lastEventAt).not.toBeNull()
    act(() => result.current.reconnect())
    await flush()
    expect(opened).toHaveLength(2)
    expect(opened[0]!.readyState).toBe(2)
    expect(result.current.state.events).toHaveLength(2)
    act(() => {
      opened[1]!.open()
      // A replay from the start is harmless: duplicates are dropped by seq.
      opened[1]!.send(events[0]!)
      opened[1]!.send(events[2]!)
    })
    expect(result.current.state.events.map((e) => e.seq)).toEqual([1, 2, 3])
    expect(result.current.stream).toBe('open')
  })

  it('shares one connection per run between subscribers and closes it with the last one', async () => {
    const a = renderHook(() => useRunEvents(RUN_ID))
    await flush()
    act(() => {
      opened[0]!.open()
      opened[0]!.send(events[0]!)
      opened[0]!.send(events[1]!)
    })
    // A second component joins late: same connection, and it is replayed what arrived so far.
    const b = renderHook(() => useRunEvents(RUN_ID))
    await flush()
    expect(opened).toHaveLength(1)
    expect(openStreamCount()).toBe(1)
    expect(b.result.current.state.events.map((e) => e.seq)).toEqual([1, 2])
    expect(b.result.current.stream).toBe('open')
    act(() => opened[0]!.send(events[2]!))
    expect(a.result.current.state.lastSeq).toBe(3)
    expect(b.result.current.state.lastSeq).toBe(3)
    a.unmount()
    expect(opened[0]!.readyState).toBe(1)
    b.unmount()
    expect(opened[0]!.readyState).toBe(2)
    expect(openStreamCount()).toBe(0)
  })

  it('closes on every error (even one the browser would retry itself) and backs off 1, 2, 5, 10, 10 s', async () => {
    vi.useFakeTimers()
    renderHook(() => useRunEvents(RUN_ID))
    await flush()
    const gaps: number[] = []
    for (let i = 0; i < 5; i++) {
      const es = opened[opened.length - 1]!
      // readyState 0: the browser would keep this connection and retry on its own.
      act(() => {
        es.readyState = 0
        es.onerror?.(new Event('error'))
      })
      expect(es.readyState).toBe(2)
      const before = opened.length
      let waited = 0
      while (opened.length === before) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(500)
        })
        waited += 500
      }
      gaps.push(waited)
    }
    expect(gaps).toEqual([1000, 2000, 5000, 10000, 10000])
    // Only the newest connection is open; every failed one was closed.
    expect(opened.filter((e) => e.readyState !== 2)).toHaveLength(1)
  })
})
