/**
 * The seam between the typed client and the network. In production it is the
 * browser's fetch and EventSource. With VITE_USE_MOCK=1 it is the fixture
 * replay in ./mock, loaded through a dynamic import that sits behind a
 * compile-time constant, so a build with the flag at 0 contains no mock code
 * and no fixture data.
 */

/** The subset of EventSource the app uses; the mock implements the same. */
export interface RunEventSource {
  readonly readyState: number
  onopen: ((ev: Event) => void) | null
  onerror: ((ev: Event) => void) | null
  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void
  close(): void
}

export interface Transport {
  fetch: (url: string, init?: RequestInit) => Promise<Response>
  /**
   * Open an SSE stream. `lastEventId` is the resume point for a fresh
   * connection: the browser's EventSource cannot send a custom header, so it
   * ignores it (its own automatic retries already send Last-Event-ID), while
   * the mock honours it.
   */
  openEventSource: (url: string, opts?: { lastEventId?: number }) => RunEventSource
}

const browserTransport: Transport = {
  fetch: (url, init) => globalThis.fetch(url, init),
  openEventSource: (url) => new EventSource(url) as unknown as RunEventSource,
}

let override: Transport | null = null
let cached: Promise<Transport> | null = null

export function getTransport(): Promise<Transport> {
  if (override) return Promise.resolve(override)
  if (!cached) {
    cached =
      import.meta.env.VITE_USE_MOCK === '1'
        ? import('./mock').then((m) => m.createMockTransport())
        : Promise.resolve(browserTransport)
  }
  return cached
}

/** Tests only: route all requests through a fake transport (null restores the default). */
export function setTransportForTests(t: Transport | null): void {
  override = t
}
