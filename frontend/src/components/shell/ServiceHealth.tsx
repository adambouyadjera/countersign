import type { ServiceName } from '@/api/types'
import { isApiError } from '@/api/errors'
import { useHealth } from '@/hooks/useHealth'
import { cn } from '@/lib/cn'
import { serviceWord } from '@/lib/serviceWord'

// Order and names follow the contract's ServicesHealth schema.
const SERVICES: readonly ServiceName[] = ['nessie', 'gemini', 'elastic', 'anthropic', 'bitdeer']

/** The header's right side: "nessie live · gemini live · elastic live …", from GET /api/health. */
export function ServiceHealth({ className }: { className?: string }) {
  const health = useHealth()
  const services = health.data?.services

  if (health.isError && !services) {
    return (
      <span role="alert" className={cn('text-bad', className)}>
        health check failed{isApiError(health.error) && ` · ${health.error.code}`}{' '}
        <button
          type="button"
          onClick={() => void health.refetch()}
          className="cursor-pointer border-0 bg-transparent p-0 font-[inherit] text-accent underline"
        >
          retry
        </button>
      </span>
    )
  }
  if (!services) return <span className={cn('text-muted', className)}>checking services…</span>

  return (
    <ul aria-label="Service health" className={cn('m-0 flex list-none flex-wrap p-0 text-muted', className)}>
      {SERVICES.map((name, i) => {
        const s = services[name]
        const word = serviceWord(s)
        const bad = s.status === 'error' || s.status === 'missing_key'
        // Services in a row that share a word read as one group: "nessie · gemini · elastic live".
        const next = SERVICES[i + 1]
        const lastOfGroup = !next || serviceWord(services[next]) !== word
        return (
          <li
            key={name}
            data-service={name}
            data-status={s.status}
            data-mode={s.mode}
            title={s.detail ?? `${name}: ${s.status}, ${s.mode} mode`}
            className="whitespace-nowrap"
          >
            {i > 0 && (
              <span aria-hidden className="px-[0.6ch]">
                ·
              </span>
            )}
            {name}
            {lastOfGroup ? (
              <>
                {' '}
                <span className={cn(bad && 'text-bad')}>{word}</span>
              </>
            ) : (
              <span className="sr-only"> {word}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}
