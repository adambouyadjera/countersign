import { useMutation, useQueryClient } from '@tanstack/react-query'
import { setAutopilot } from '@/api/client'
import { isApiError } from '@/api/errors'
import type { Autopilot } from '@/api/types'
import { useAutopilot } from '@/hooks/queries'
import { cn } from '@/lib/cn'

/**
 * "autopilot on/off" from GET /api/autopilot. On the console (`interactive`) it is a
 * switch that POSTs /api/autopilot. Says "unavailable" rather than guessing when the call fails.
 */
export function AutopilotStatus({ interactive = false }: { interactive?: boolean }) {
  const q = useAutopilot()
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: (a: Autopilot) => setAutopilot({ enabled: !a.enabled, lanes: a.lanes }),
    onSuccess: (next) => qc.setQueryData(['autopilot'], next),
  })

  if (q.isPending) return <span className="text-muted">autopilot …</span>
  if (q.isError) {
    return (
      <span className="text-muted" title={`GET /api/autopilot failed${isApiError(q.error) ? `: ${q.error.code}` : ''}`}>
        autopilot unavailable
      </span>
    )
  }
  const on = q.data.enabled
  if (!interactive) {
    return (
      <span className="text-muted" data-testid="autopilot">
        autopilot {on ? <span className="text-good">on</span> : <span className="text-muted">off</span>}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-2" data-testid="autopilot">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="autopilot"
        disabled={toggle.isPending}
        onClick={() => toggle.mutate(q.data)}
        title={`Autopilot lanes: ${q.data.lanes.join(', ') || 'none'}`}
        className="inline-flex cursor-pointer items-center gap-2 border-0 bg-transparent p-0 font-[inherit] text-muted disabled:cursor-wait"
      >
        autopilot
        <span
          aria-hidden
          className={cn(
            'relative inline-block h-5 w-9 rounded-full border-[1.5px] transition-colors',
            on ? 'border-ink bg-good' : 'border-ink bg-panel',
          )}
        >
          <span
            className={cn(
              'absolute top-[2px] size-3.5 rounded-full bg-card shadow-sm transition-[left] motion-reduce:transition-none',
              on ? 'left-[18px]' : 'left-[2px]',
            )}
          />
        </span>
        <span className={on ? 'text-good' : 'text-muted'}>{on ? 'on' : 'off'}</span>
      </button>
      {toggle.isError && (
        <span role="alert" className="text-meta text-bad" title={toggle.error.message}>
          couldn&apos;t switch{isApiError(toggle.error) ? ` · ${toggle.error.code}` : ''}
        </span>
      )}
    </span>
  )
}
