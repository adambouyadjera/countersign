import { StatusDot, type StatusTone } from '@/components/ui'
import type { ServiceHealth, ServiceName } from '@/api/types'
import { isApiError } from '@/api/errors'
import { HEALTH_POLL_MS, useHealth } from '@/hooks/useHealth'
import { cn } from '@/lib/cn'

// Order and names follow the contract's ServicesHealth schema.
const SERVICES: readonly ServiceName[] = ['nessie', 'elastic', 'anthropic', 'gemini', 'bitdeer']

const TONE: Record<ServiceHealth['status'], StatusTone> = {
  ok: 'ok',
  offline: 'offline',
  missing_key: 'hold',
  error: 'fault',
}

const STATUS_TEXT: Record<ServiceHealth['status'], string> = {
  ok: 'ok',
  offline: 'offline',
  missing_key: 'missing key',
  error: 'error',
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString('en-GB', { hour12: false })
}

export function HealthStrip() {
  const health = useHealth()
  const services = health.data?.services

  return (
    <div
      aria-label="Service health"
      className="flex h-8 items-center gap-5 overflow-hidden border-t border-ink bg-paper-2 pr-6 pl-8 text-[0.75rem]"
    >
      <span className="smallcaps shrink-0 text-ink-soft">Services</span>
      {health.isError && !services ? (
        <span role="alert" className="flex min-w-0 items-center gap-2 text-taint">
          <span className="truncate">
            Health check failed
            {isApiError(health.error) && <code className="ml-1 font-mono">{health.error.code}</code>}:{' '}
            {health.error.message}
          </span>
          <button
            type="button"
            onClick={() => void health.refetch()}
            className="shrink-0 rounded-[2px] border border-taint px-1.5 leading-5 hover:bg-taint hover:text-paper"
          >
            Retry
          </button>
        </span>
      ) : (
        <ul className="m-0 flex min-w-0 list-none items-center gap-4 p-0">
          {SERVICES.map((name) => {
            const s = services?.[name]
            const offlineMode = s?.mode === 'offline'
            return (
              <li
                key={name}
                data-service={name}
                data-status={s?.status ?? 'unknown'}
                title={s?.detail}
                className="flex shrink-0 items-center gap-1.5"
              >
                <StatusDot
                  tone={s ? TONE[s.status] : 'idle'}
                  label={s ? `${name}: ${STATUS_TEXT[s.status]}, ${s.mode} mode` : `${name}: checking`}
                />
                <span className="font-mono text-ink">{name}</span>
                <span className={cn(s?.status === 'error' ? 'text-taint' : 'text-ink-soft')}>
                  {s ? STATUS_TEXT[s.status] : 'checking…'}
                </span>
                {offlineMode && s.status !== 'offline' && (
                  <span className="border border-dashed border-ink-soft px-1 font-mono text-[0.625rem] text-ink-soft">
                    offline mode
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <span className="ml-auto shrink-0 truncate font-mono text-[0.6875rem] text-ink-soft">
        {health.dataUpdatedAt
          ? `checked ${clock(health.dataUpdatedAt)}${health.isError ? ' · last check failed' : ''} · every ${HEALTH_POLL_MS / 1000}s`
          : 'GET /api/health'}
      </span>
    </div>
  )
}
