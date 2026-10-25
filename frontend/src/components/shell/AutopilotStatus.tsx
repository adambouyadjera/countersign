import { useMutation, useQueryClient } from '@tanstack/react-query'
import { setAutopilot } from '@/api/client'
import { isApiError } from '@/api/errors'
import type { Autopilot, Lane } from '@/api/types'
import { useAutopilot } from '@/hooks/queries'
import { cn } from '@/lib/cn'

/** Autopilot processes the inbox through both lanes, so the demo can compare them. */
const AUTOPILOT_LANES: Lane[] = ['countersign', 'unguarded']

/**
 * "autopilot on/off" from GET /api/autopilot. When `interactive` (the header) it is a
 * switch that POSTs /api/autopilot. Says "unavailable" rather than guessing when the call fails.
 */
export function AutopilotStatus({ interactive = false }: { interactive?: boolean }) {
  const q = useAutopilot()
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: (a: Autopilot) => setAutopilot({ enabled: !a.enabled, lanes: AUTOPILOT_LANES }),
    onSuccess: (next) => {
      qc.setQueryData(['autopilot'], next)
      void qc.invalidateQueries({ queryKey: ['inbox'] })
    },
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
        {/*
          Track 36×20 px, border-box, 2px ink border (whole px, so it never rounds unevenly): inner box 32×16.
          Knob = inner height 16 − 2 × gap 2 = 12 px, centred vertically.
          Off: 2 px from the inner left edge. On: translateX(32 − 12 − 2 × 2 = 16 px). The gap is 2 px on every side.
        */}
        <span
          aria-hidden
          data-testid="autopilot-track"
          className={cn(
            'relative box-border inline-block h-[20px] w-[36px] shrink-0 rounded-full border-2 border-solid border-ink transition-colors motion-reduce:transition-none',
            on ? 'bg-good' : 'bg-panel',
          )}
        >
          <span
            data-testid="autopilot-knob"
            className={cn(
              'absolute top-1/2 left-[2px] box-border size-[12px] -translate-y-1/2 rounded-full bg-card shadow-sm transition-transform motion-reduce:transition-none',
              on ? 'translate-x-[16px]' : 'translate-x-0',
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
