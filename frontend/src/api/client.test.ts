import { afterEach, describe, expect, it, vi } from 'vitest'
import errorFixture from '@fixtures/error.json'
import { ApiError, createAttack, createRun, getBalances, getHealth, listApprovals, runEventsUrl } from './client'
import { parseApiError } from './errors'
import { setTransportForTests, type Transport } from './transport'

function fakeTransport(respond: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => respond(url, init))
  const t: Transport = { fetch, openEventSource: () => ({}) as never }
  setTransportForTests(t)
  return fetch
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  setTransportForTests(null)
  vi.unstubAllEnvs()
})

describe('parseApiError', () => {
  it('parses the contract error envelope (fixture error.json)', () => {
    const e = parseApiError(403, JSON.stringify(errorFixture))
    expect(e).toBeInstanceOf(ApiError)
    expect(e).toMatchObject({
      status: 403,
      code: 'sod_violation',
      message: 'Only a vendor_admin may change a vendor account.',
      detail: { actor_id: 'person-ap-agent', required_role: 'vendor_admin' },
      envelope: true,
    })
  })

  it.each([
    [404, 'not_found'],
    [409, 'conflict'],
    [422, 'validation_error'],
    [429, 'rate_limited'],
    [503, 'service_unavailable'],
    [500, 'internal'],
    [502, 'internal'],
  ])('falls back to a code from HTTP %i when the body is not the envelope', (status, code) => {
    const e = parseApiError(status, '<html>Bad Gateway</html>', 'Bad Gateway')
    expect(e.code).toBe(code)
    expect(e.envelope).toBe(false)
    expect(e.message).toBe(`HTTP ${status} Bad Gateway`)
  })

  it('treats a partial envelope as not matching', () => {
    expect(parseApiError(422, JSON.stringify({ error: { code: 'validation_error' } })).envelope).toBe(false)
    expect(parseApiError(422, JSON.stringify({ detail: 'fastapi default' })).envelope).toBe(false)
    expect(parseApiError(500, '').message).toBe('HTTP 500')
  })

  it('keeps the message but maps an unknown code by status', () => {
    const e = parseApiError(503, JSON.stringify({ error: { code: 'elastic_down', message: 'Elastic is offline' } }))
    expect(e).toMatchObject({ code: 'service_unavailable', message: 'Elastic is offline', envelope: true })
  })
})

describe('client', () => {
  it('throws ApiError with the contract message on a non-2xx response', async () => {
    fakeTransport(() =>
      json(422, { error: { code: 'validation_error', message: 'lanes must not be empty', detail: { field: 'lanes' } } }),
    )
    const err = await createRun({ batch_id: 'b', lanes: [] }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 422, code: 'validation_error', message: 'lanes must not be empty' })
  })

  it('wraps network failures as ApiError network_error', async () => {
    fakeTransport(() => {
      throw new TypeError('Failed to fetch')
    })
    await expect(getHealth()).rejects.toMatchObject({ code: 'network_error', status: 0 })
  })

  it('rejects a 200 whose body is not JSON', async () => {
    fakeTransport(() => new Response('<html>', { status: 200 }))
    await expect(getBalances()).rejects.toMatchObject({ code: 'internal' })
  })

  it('sends JSON bodies with the right method, path and headers', async () => {
    const fetch = fakeTransport(() => json(200, { run_id: 'r' }))
    await createRun({ batch_id: 'batch 1', lanes: ['unguarded', 'countersign'] })
    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/runs')
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(init?.body))).toEqual({ batch_id: 'batch 1', lanes: ['unguarded', 'countersign'] })
  })

  it('builds query strings and encodes path segments', async () => {
    const fetch = fakeTransport(() => json(200, []))
    await listApprovals('pending')
    await listApprovals()
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(['/api/approvals?status=pending', '/api/approvals'])
    expect(runEventsUrl('run/1')).toBe('/api/runs/run%2F1/events')
  })

  it('prefixes VITE_API_BASE_URL', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/')
    const fetch = fakeTransport(() => json(200, {}))
    await getHealth()
    expect(fetch.mock.calls[0]![0]).toBe('https://api.example.test/api/health')
  })
})

describe('createAttack timeout', () => {
  it('rejects with a timeout error after 20 s when no response arrives, and aborts the request', async () => {
    vi.useFakeTimers()
    try {
      let signal: AbortSignal | undefined
      fakeTransport((_url, init) => {
        signal = init?.signal ?? undefined
        return new Promise<Response>(() => {})
      })
      const p = createAttack({ vendor_id: 'VEN-001', attack_text: 'neutral test text', placement: 'visible_note' })
      const done = expect(p).rejects.toMatchObject({ code: 'timeout', message: 'No response from POST /api/attacks after 20 s.' })
      await vi.advanceTimersByTimeAsync(20_000)
      await done
      expect(signal?.aborted).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('returns the batch when the response arrives in time', async () => {
    const batch = { batch_id: 'b1', kind: 'attack', created_at: 'x', invoices: [] }
    fakeTransport(() => json(200, batch))
    await expect(createAttack({ vendor_id: 'VEN-001', attack_text: 'neutral test text', placement: 'visible_note' })).resolves.toEqual(batch)
  })
})
