import type { UseMutationResult } from '@tanstack/react-query'
import { Button, Kbd } from '@/components/ui'
import type { Run } from '@/api/types'
import type { StreamStatus } from '@/hooks/useRunEvents'
import type { RunState } from '@/state/runReducer'
import { ModeTag } from './ModeTag'
import { RequestError } from './RequestError'

// The contract's default run instruction (CONTRACT.md). Static trusted text, shown until a run reports its own.
export const DEFAULT_INSTRUCTION =
  'Pay every invoice in this batch that is due within 30 days and matches an open purchase order. Pay only to accounts on file; anything else needs human approval.'

const STREAM_TEXT: Record<StreamStatus, string> = {
  idle: 'no stream',
  connecting: 'connecting',
  open: 'streaming',
  reconnecting: 'reconnecting',
}

export interface RunBarProps {
  canRun: boolean
  run: Run | null
  startRun: UseMutationResult<Run, Error, void>
  stream: StreamStatus
  state: RunState
}

export function RunBar({ canRun, run, startRun, stream, state }: RunBarProps) {
  const instruction = state.started?.instruction ?? run?.instruction
  const systemErrors = state.system.filter((e) => e.type === 'error')
  const inbox = state.system.filter((e) => e.type === 'inbox.received')
  return (
    <div className="border-l-4 border-ink bg-paper py-1.5 pr-3 pl-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="smallcaps m-0 !text-[0.6875rem] text-ink-soft">
            Standing instruction · trusted · {instruction ? 'from this run' : 'contract default'}
          </p>
          <p className="m-0 mt-0.5 line-clamp-2 font-display text-[1.0625rem] leading-tight text-ink">
            “{instruction ?? DEFAULT_INSTRUCTION}”
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button onClick={() => startRun.mutate()} disabled={!canRun || startRun.isPending} aria-keyshortcuts="r">
            {startRun.isPending ? 'Starting…' : run ? 'Run again' : 'Run batch'}
            <Kbd className="!border-paper/40 !bg-transparent !text-paper">R</Kbd>
          </Button>
          {!canRun && !startRun.isPending && <span className="text-[0.6875rem] text-ink-soft">Draw a batch first</span>}
        </div>
      </div>
      {run && (
        <p className="m-0 mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.75rem] text-ink-soft">
          <span>
            run <span className="font-mono text-ink">{run.run_id}</span>
          </span>
          <span data-testid="stream-status">
            {STREAM_TEXT[stream]}
            {state.completed && ' · run completed'}
          </span>
          <span>
            last seq <span className="font-mono tabular text-ink">{state.lastSeq || '—'}</span>
          </span>
          <ModeTag mode={state.systemMode} />
          {inbox.length > 0 && <span>{inbox.length} inbox {inbox.length === 1 ? 'item' : 'items'} received</span>}
        </p>
      )}
      {systemErrors.map((e) =>
        e.type === 'error' ? (
          <p key={e.seq} className="m-0 mt-1 text-[0.75rem] text-taint">
            <span className="font-mono">#{e.seq}</span> system {e.payload.recoverable ? 'warning' : 'error'}:{' '}
            {e.payload.message}
          </p>
        ) : null,
      )}
      {startRun.isError && (
        <RequestError
          className="mt-2"
          title="Could not start the run"
          error={startRun.error}
          onRetry={() => startRun.mutate()}
          retrying={startRun.isPending}
        />
      )}
    </div>
  )
}
