import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import { setTransportForTests, type RunEventSource } from '@/api/transport'
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
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600)
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
})
