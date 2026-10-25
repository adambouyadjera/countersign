import { useEffect, useState } from 'react'
import { InkButton, InkRow } from '@/components/ui'
import type { StreamStatus } from '@/hooks/useRunEvents'
import { cn } from '@/lib/cn'
import { elapsedText, laneProgress, STALE_AFTER_MS, type LaneStep } from '@/lib/laneProgress'
import type { LaneState } from '@/state/runReducer'
import { LANE_META, type EffectiveLaneStatus } from './lanes'

const TONE: Partial<Record<LaneStep, string>> = {
  paid: 'text-good',
  done: 'text-good',
  held: 'text-bad',
  holding: 'text-bad',
  blocked: 'text-bad',
  error: 'text-bad',
  failed: 'text-bad',
}

export interface LiveProgressProps {
  lanes: { lane: LaneState; status: EffectiveLaneStatus }[]
  stream: StreamStatus
  /** Browser time of the last event, null before the first. */
  lastEventAt: number | null
  reconnect: () => void
  invoiceLabel: (invoiceId: string) => string
  /**
   * GET /api/health is failing. A proxy can keep a dead stream open without an error, and
   * EventSource hides the server's heartbeat comments, so this is the signal that it stopped.
   */
  backendUnreachable?: boolean
}

/**
 * What each lane is doing right now, from the run's SSE events, with the time since
 * the lane's current slice began. Says so when nothing has arrived for a while, and
 * when the stream drops. Mount with key = run id so the "since" clock starts per run.
 */
export function LiveProgress({ lanes, stream, lastEventAt, reconnect, invoiceLabel, backendUnreachable = false }: LiveProgressProps) {
  const [openedAt] = useState(() => Date.now())
  const rows = lanes
    .filter((l) => l.status !== 'not_running' && l.status !== 'not_implemented')
    .map((l) => {
      const p = laneProgress(l.lane)
      // Started but no event yet: POST /api/runs (or the run record) says running.
      const waitingForFirst = p.step === 'waiting' && l.status === 'running'
      return { ...l, p, active: p.active || waitingForFirst }
    })
  const anyActive = rows.some((r) => r.active)

  // A real clock for the elapsed counters, ticking only while a lane works.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!anyActive) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [anyActive])

  const quietFor = now - (lastEventAt ?? openedAt)
  const dropped = stream === 'reconnecting' || (stream !== 'idle' && backendUnreachable)
  const stale = anyActive && stream === 'open' && !dropped && quietFor >= STALE_AFTER_MS

  return (
    <section aria-label="Live progress" className="flex flex-col gap-1.5">
      <ul className="m-0 flex list-none flex-wrap gap-x-6 gap-y-1 p-0 font-mono text-small">
        {rows.map(({ lane, p, active }) => {
          const since = p.sliceStartedAt ? Date.parse(p.sliceStartedAt) : undefined
          const end = active ? now : p.stepAt ? Date.parse(p.stepAt) : undefined
          return (
            <li key={lane.lane} data-lane-progress={lane.lane} data-step={p.step} data-active={active ? 'true' : 'false'}>
              <span className="text-muted">{LANE_META[lane.lane].short}</span>{' '}
              <span className={cn('font-bold', TONE[p.step])}>
                {active && p.step === 'waiting' ? 'waiting for the first event' : p.step}
              </span>
              {p.invoiceId && <span className="text-muted"> · {invoiceLabel(p.invoiceId)}</span>}
              {since !== undefined && end !== undefined && (
                <span data-testid={`elapsed-${lane.lane}`} className="text-muted tabular">
                  {' '}
                  · {elapsedText(end - since)}
                </span>
              )}
            </li>
          )
        })}
      </ul>
      {stale && (
        <p role="status" data-testid="still-working" className="m-0 text-small text-muted">
          Still working: live model calls can take a minute
          <span className="tabular"> · last event {elapsedText(quietFor)} ago</span>
        </p>
      )}
      {dropped && (
        <InkRow tone="failed" role="alert" data-testid="stream-dropped" className="flex flex-wrap items-center gap-3 px-3 py-2">
          <span className="text-small">
            <strong className="text-bad">
              {stream === 'reconnecting'
                ? "Lost the connection to the run's event stream."
                : "Can't reach the backend (GET /api/health is failing); the event stream may have stopped."}
            </strong>{' '}
            Events already received are kept.
          </span>
          <InkButton size="sm" variant="secondary" onClick={reconnect} className="ml-auto">
            Reconnect
          </InkButton>
        </InkRow>
      )}
    </section>
  )
}
