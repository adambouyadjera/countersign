import { useCallback, useEffect, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createBatch, createRun, getBalances } from '@/api/client'
import type { Lane } from '@/api/types'
import { BatchRail } from '@/components/console/BatchRail'
import { LaneColumn } from '@/components/console/LaneColumn'
import { LedgerPanel } from '@/components/console/LedgerPanel'
import { RunBar } from '@/components/console/RunBar'
import { PageHeader } from '@/components/shell/PageHeader'
import { useHealth } from '@/hooks/useHealth'
import { useRunEvents } from '@/hooks/useRunEvents'
import { useConsoleStore } from '@/state/consoleStore'
import { LANES } from '@/state/runReducer'

const RUN_LANES: Lane[] = ['unguarded', 'countersign']

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  return t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)
}

export function ConsolePage() {
  const { batch, run, runNonce, setBatch, setRun } = useConsoleStore()
  const queryClient = useQueryClient()

  const newBatch = useMutation({ mutationFn: () => createBatch({ kind: 'standard' }), onSuccess: setBatch })
  const startRun = useMutation({
    mutationFn: () => {
      if (!batch) throw new Error('Draw a batch first')
      return createRun({ batch_id: batch.batch_id, lanes: RUN_LANES })
    },
    onSuccess: setRun,
  })

  const { state, stream } = useRunEvents(run?.run_id ?? null, runNonce)
  const balances = useQuery({ queryKey: ['balances'], queryFn: getBalances })
  const health = useHealth()

  // The ledger refetches only when a real transfer event arrives.
  useEffect(() => {
    if (state.paymentsExecuted > 0) void queryClient.invalidateQueries({ queryKey: ['balances'] })
  }, [state.paymentsExecuted, queryClient])

  const canRun = Boolean(batch)
  const { mutate: run_, isPending: runPending } = startRun
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'r' && e.key !== 'R') return
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || isTypingTarget(e.target)) return
      if (!canRun || runPending) return
      e.preventDefault()
      run_()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [canRun, runPending, run_])

  const labels = useMemo(
    () => new Map((batch?.invoices ?? []).map((i) => [i.invoice_id, i.invoice_number] as const)),
    [batch],
  )
  const invoiceLabel = useCallback((id: string) => labels.get(id) ?? id, [labels])
  const runningLanes = run ? run.lanes.map((l) => l.lane) : null

  return (
    <div className="flex h-[calc(100dvh-6rem-6px)] min-h-[34rem] flex-col pr-6 pb-4 pl-8">
      <PageHeader
        compact
        folio="I"
        title="Two agents, one batch"
        dek="The same invoices, paid once by an unguarded agent and once under countersignature."
        className="mb-3"
      />
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(14rem,17rem)_minmax(0,1fr)_minmax(14rem,17rem)] gap-4">
        <BatchRail batch={batch} newBatch={newBatch} runningLanes={runningLanes} runState={state} />

        <section aria-label="Twin lanes" className="flex min-h-0 min-w-0 flex-col gap-3">
          <RunBar canRun={canRun} run={run} startRun={startRun} stream={stream} state={state} />
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
            {LANES.map((lane) => (
              <LaneColumn
                key={lane}
                lane={lane}
                hasRun={Boolean(run)}
                runLane={run?.lanes.find((l) => l.lane === lane)}
                state={state.lanes[lane]}
                invoiceLabel={invoiceLabel}
              />
            ))}
          </div>
        </section>

        <LedgerPanel balances={balances} nessie={health.data?.services.nessie} />
      </div>
    </div>
  )
}
