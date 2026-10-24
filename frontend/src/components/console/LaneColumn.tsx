import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'framer-motion'
import { EmptyState, Kbd, LedgerRule, Money, Panel, StatusDot, type StatusTone } from '@/components/ui'
import type { Lane, Run } from '@/api/types'
import { cn } from '@/lib/cn'
import type { LaneState, LaneStatus } from '@/state/runReducer'
import { LANE_META } from './lanes'
import { ModeTag } from './ModeTag'
import { StepRow } from './StepRow'

export interface LaneColumnProps {
  lane: Lane
  /** The run's own record of this lane; undefined when the run did not start it. */
  runLane: Run['lanes'][number] | undefined
  hasRun: boolean
  state: LaneState
  invoiceLabel: (invoiceId: string) => string
}

const STATUS_TEXT: Record<LaneStatus | 'not_running', string> = {
  idle: 'Waiting for events',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  not_implemented: 'Not implemented',
  not_running: 'Not running',
}

const STATUS_TONE: Record<LaneStatus | 'not_running', StatusTone> = {
  idle: 'idle',
  running: 'ok',
  completed: 'ok',
  failed: 'fault',
  not_implemented: 'offline',
  not_running: 'offline',
}

export function LaneColumn({ lane, runLane, hasRun, state, invoiceLabel }: LaneColumnProps) {
  const meta = LANE_META[lane]
  const notRunning = hasRun && !runLane
  // Prefer the stream's lane.status; fall back to what POST /api/runs reported.
  const status: LaneStatus | 'not_running' = notRunning
    ? 'not_running'
    : state.status !== 'idle'
      ? state.status
      : (runLane?.status ?? 'idle')

  const listRef = useRef<HTMLOListElement>(null)
  const reduce = useReducedMotion()
  const stepCount = state.steps.length
  useEffect(() => {
    const el = listRef.current
    if (!el || stepCount === 0) return
    el.scrollTo({ top: el.scrollHeight, behavior: reduce ? 'auto' : 'smooth' })
  }, [stepCount, reduce])

  const t = state.tally
  return (
    <Panel
      dense
      aria-label={`Lane ${meta.letter}: ${meta.title}`}
      data-lane={lane}
      title={
        <>
          <span className="mr-2 text-ink-soft italic">{meta.letter}.</span>
          {meta.title}
        </>
      }
      aside={
        <StatusDot
          tone={STATUS_TONE[status]}
          label={STATUS_TEXT[status]}
          showLabel
          pulse={status === 'running'}
        />
      }
      className="flex min-h-0 flex-col"
      bodyClassName="flex min-h-0 flex-1 flex-col !pt-2"
    >
      <dl className="m-0 mb-1.5 grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 text-[0.75rem]">
        <dt className="smallcaps !text-[0.6875rem] text-ink-soft">{lane === 'countersign' ? 'Planner' : 'Agent'}</dt>
        <dd className="m-0 truncate font-mono text-ink" data-testid={`${lane}-model`} title={state.model}>
          {state.model ?? '—'}
        </dd>
        <dd className="m-0 row-span-2 self-start">
          <ModeTag mode={state.mode} />
        </dd>
        {lane === 'countersign' ? (
          <>
            <dt className="smallcaps !text-[0.6875rem] text-ink-soft">Extractor</dt>
            <dd className="m-0 truncate font-mono text-ink" title={state.extractorModel}>
              {state.extractorModel ?? '—'}
            </dd>
          </>
        ) : (
          <dd aria-hidden className="col-span-2 m-0">
            &nbsp;
          </dd>
        )}
      </dl>
      {state.statusDetail && <p className="m-0 mb-1 text-[0.75rem] text-ink-soft">{state.statusDetail}</p>}

      <div className="min-h-0 flex-1 border-t border-ink">
        {notRunning || status === 'not_implemented' ? (
          <EmptyState
            title={notRunning ? 'Not running' : 'Not implemented'}
            body={
              notRunning
                ? `The backend did not start the ${meta.short.toLowerCase()} lane for this run.`
                : 'The backend reports this lane as not implemented yet.'
            }
          />
        ) : !hasRun ? (
          <EmptyState
            title="No run yet"
            body={
              <>
                Draw a batch, then press <Kbd>R</Kbd> or Run batch.
              </>
            }
          />
        ) : stepCount === 0 ? (
          <p role="status" className="m-0 py-6 text-center text-sm text-ink-soft">
            Waiting for the first event…
          </p>
        ) : (
          <ol
            ref={listRef}
            aria-label={`${meta.title} steps`}
            className="relative m-0 h-full list-none overflow-y-auto p-0 pr-1"
          >
            {state.steps.map((e) => (
              <StepRow key={e.seq} event={e} invoiceLabel={invoiceLabel} laneMode={state.mode} />
            ))}
          </ol>
        )}
      </div>

      <LedgerRule variant="double" className="mt-1" />
      <dl aria-label={`${meta.title} tally`} className="m-0 mt-1.5 grid grid-cols-3">
        <TallyCell label="Paid" testId={`${lane}-tally-paid`} count={t.paid_count} cents={t.paid_cents} />
        <TallyCell label="Held" testId={`${lane}-tally-held`} count={t.held_count} tone="hold" />
        <TallyCell
          label="Sent to unknown accounts"
          testId={`${lane}-tally-unknown`}
          count={t.not_on_file_count}
          cents={t.not_on_file_cents}
          tone="taint"
        />
      </dl>
    </Panel>
  )
}

function TallyCell({
  label,
  count,
  cents,
  tone,
  testId,
}: {
  label: string
  count: number
  cents?: number
  tone?: 'hold' | 'taint'
  testId: string
}) {
  const lit = count > 0
  return (
    <div className="flex min-w-0 flex-col border-l border-rule-strong pl-2 first:border-l-0 first:pl-0">
      <dt className="smallcaps !text-[0.6875rem] leading-tight text-ink-soft">{label}</dt>
      <dd className="m-0 mt-auto flex items-baseline gap-2">
        <span
          data-testid={testId}
          className={cn(
            'font-mono tabular text-[1.5rem] leading-tight',
            !lit && 'text-ink-soft',
            lit && tone === 'hold' && 'text-[#7a4f0f]',
            lit && tone === 'taint' && 'text-taint',
            lit && !tone && 'text-ink',
          )}
        >
          {count}
        </span>
        {cents !== undefined && <Money cents={cents} flow className={cn('truncate text-[0.75rem]', lit && tone === 'taint' ? 'text-taint' : 'text-ink-soft')} />}
      </dd>
    </div>
  )
}
