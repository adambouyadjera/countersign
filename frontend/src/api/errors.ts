import type { ErrorCode } from './types'

const KNOWN_CODES: readonly ErrorCode[] = [
  'validation_error',
  'not_found',
  'conflict',
  'rate_limited',
  'service_unavailable',
  'internal',
  'sod_violation',
]

/**
 * A failed request, carrying the contract's error envelope
 * `{"error": {"code", "message", "detail"?}}`. When the server did not send
 * that envelope (proxy error, network failure) the code is derived from the
 * HTTP status and `envelope` is false, so the UI can say so.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode | 'network_error' | 'timeout'
  readonly detail?: Record<string, unknown>
  /** True when the body matched the contract's error shape. */
  readonly envelope: boolean

  constructor(opts: {
    status: number
    code: ApiError['code']
    message: string
    detail?: Record<string, unknown>
    envelope: boolean
  }) {
    super(opts.message)
    this.name = 'ApiError'
    this.status = opts.status
    this.code = opts.code
    this.detail = opts.detail
    this.envelope = opts.envelope
  }
}

function codeForStatus(status: number): ErrorCode {
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 422) return 'validation_error'
  if (status === 429) return 'rate_limited'
  if (status === 503) return 'service_unavailable'
  return 'internal'
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** Parse a non-2xx response body into an ApiError. Never throws. */
export function parseApiError(status: number, bodyText: string, statusText = ''): ApiError {
  let body: unknown
  try {
    body = bodyText ? JSON.parse(bodyText) : undefined
  } catch {
    body = undefined
  }
  const err = isRecord(body) ? body.error : undefined
  if (isRecord(err) && typeof err.code === 'string' && typeof err.message === 'string') {
    const code = (KNOWN_CODES as readonly string[]).includes(err.code)
      ? (err.code as ErrorCode)
      : codeForStatus(status)
    return new ApiError({
      status,
      code,
      message: err.message,
      detail: isRecord(err.detail) ? err.detail : undefined,
      envelope: true,
    })
  }
  const fallback = statusText ? `HTTP ${status} ${statusText}` : `HTTP ${status}`
  return new ApiError({ status, code: codeForStatus(status), message: fallback, envelope: false })
}

/** A request that never reached the server. */
export function networkError(cause: unknown): ApiError {
  const msg = cause instanceof Error ? cause.message : String(cause)
  return new ApiError({ status: 0, code: 'network_error', message: `Network error: ${msg}`, envelope: false })
}

/** A request that got no complete response within its time limit. */
export function timeoutError(what: string, ms: number): ApiError {
  return new ApiError({ status: 0, code: 'timeout', message: `No response from ${what} after ${Math.round(ms / 1000)} s.`, envelope: false })
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}
