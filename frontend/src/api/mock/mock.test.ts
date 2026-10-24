import { describe, expect, it, vi } from 'vitest'
import { createMockTransport, route } from './index'
import { MockEventSource } from './eventSource'
import { fixtures, parseJsonl, runBothEventsText } from './fixtures'

describe('mock router', () => {
  it('serves fixtures for known endpoints', () => {
    expect(route('GET', '/api/health', undefined)).toEqual({ status: 200, body: fixtures.health })
    expect(route('POST', '/api/batches', { kind: 'standard' }).body).toBe(fixtures.batch)
    expect(route('POST', '/api/runs', { batch_id: 'b', lanes: ['countersign'] }).body).toBe(fixtures.run)
    expect(route('GET', 'http://api.test/api/balances', undefined).body).toBe(fixtures.balances)
  })

  it('answers in the contract error shape when there is no fixture', () => {
    expect(route('GET', '/api/runs/run-unknown', undefined)).toMatchObject({
      status: 404,
      body: { error: { code: 'not_found' } },
    })
    expect(route('POST', '/api/runs', { lanes: [] })).toMatchObject({
      status: 422,
      body: { error: { code: 'validation_error' } },
    })
  })

  it('returns real Response objects through the transport', async () => {
    vi.useFakeTimers()
    const t = createMockTransport()
    const p = t.fetch('/api/health')
    await vi.runAllTimersAsync()
    const res = await p
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual(fixtures.health)
    vi.useRealTimers()
  })
})

describe('MockEventSource', () => {
  it('replays every fixture event with id = seq and event = type, then stays open', async () => {
    vi.useFakeTimers()
    const events = parseJsonl(runBothEventsText)
    const es = new MockEventSource(events, { delayMs: 10 })
    const got: Array<[string, string]> = []
    for (const type of new Set(events.map((e) => e.type))) {
      es.addEventListener(type, (m) => got.push([m.type, m.lastEventId]))
    }
    await vi.advanceTimersByTimeAsync(10 * (events.length + 2))
    expect(got).toEqual(events.map((e) => [e.type, String(e.seq)]))
    expect(es.readyState).toBe(1)
    es.close()
    vi.useRealTimers()
  })

  it('resumes after lastEventId', async () => {
    vi.useFakeTimers()
    const events = parseJsonl(runBothEventsText)
    const es = new MockEventSource(events, { delayMs: 10, lastEventId: 50 })
    const seqs: number[] = []
    for (const type of new Set(events.map((e) => e.type))) {
      es.addEventListener(type, (m) => seqs.push(Number(m.lastEventId)))
    }
    await vi.advanceTimersByTimeAsync(200)
    expect(seqs).toEqual([51, 52, 53, 54])
    vi.useRealTimers()
  })
})

describe('mock POST /api/attacks', () => {
  const body = { vendor_id: 'VEN-003', attack_text: 'neutral test text {{ATTACKER_ACCOUNT}}', placement: 'footer' }

  it('returns the fixture batch as kind "attack" for a valid request', () => {
    const r = route('POST', '/api/attacks', body)
    expect(r.status).toBe(200)
    expect((r.body as { kind: string }).kind).toBe('attack')
  })

  it.each([
    [{ ...body, attack_text: '' }, 'attack_text'],
    [{ ...body, attack_text: 'x'.repeat(601) }, 'attack_text'],
    [{ ...body, placement: 'margin' }, 'placement'],
    [{ ...body, vendor_id: 'VEN-999' }, 'vendor_id'],
  ])('answers 422 validation_error for a bad request (%#)', (bad, field) => {
    const r = route('POST', '/api/attacks', bad)
    expect(r.status).toBe(422)
    expect((r.body as { error: { code: string; message: string } }).error.code).toBe('validation_error')
    expect((r.body as { error: { message: string } }).error.message).toContain(field)
  })
})

describe('mock GET /api/approvals', () => {
  it('honours ?status=', () => {
    expect(route('GET', '/api/approvals?status=pending', undefined).body).toEqual([])
    expect((route('GET', '/api/approvals?status=resolved', undefined).body as unknown[]).length).toBe(1)
    expect((route('GET', '/api/approvals', undefined).body as unknown[]).length).toBe(1)
  })
})

describe('mock /api/autopilot', () => {
  it('remembers what POST set, and rejects a non-boolean', async () => {
    const { resetMockState } = await import('./index')
    resetMockState()
    expect((route('GET', '/api/autopilot', undefined).body as { enabled: boolean }).enabled).toBe(false)
    expect((route('POST', '/api/autopilot', { enabled: true }).body as { enabled: boolean }).enabled).toBe(true)
    expect((route('GET', '/api/autopilot', undefined).body as { enabled: boolean }).enabled).toBe(true)
    expect(route('POST', '/api/autopilot', { enabled: 'yes' }).status).toBe(422)
    resetMockState()
  })
})
