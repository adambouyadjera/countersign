import { useCallback, useEffect, useMemo } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createBatch, createRun, getBalances, getBatch, getPlannerTranscript } from '@/api/client'
import type { Lane } from '@/api/types'
import { ApprovalCard } from '@/components/console/ApprovalCard'
import { ChallengeInput } from '@/components/console/ChallengeInput'
import { DuelTranscript } from '@/components/console/DuelTranscript'
import { InboxColumn, type InboxTotals } from '@/components/console/InboxColumn'
import { LiveProgress } from '@/components/console/LiveProgress'
import { MailPanel } from '@/components/console/MailPanel'
import { effectiveLaneStatus } from '@/components/console/lanes'
import { ModeTag } from '@/components/console/ModeTag'
import { OneSentencePanel } from '@/components/console/OneSentencePanel'
import { RequestError } from '@/components/console/RequestError'
import { VendorHistoryLine } from '@/components/console/VendorHistoryLine'
import { SigFace } from '@/components/sig'
import { InkButton, Kbd } from '@/components/ui'
import { useAutopilot } from '@/hooks/queries'
import { useHealth } from '@/hooks/useHealth'
import { useRunEvents, type StreamStatus } from '@/hooks/useRunEvents'
import { handleNamespaces, splitHandle } from '@/lib/handle'
import { formatCents } from '@/lib/money'
import { countersignOutcome, unguardedOutcome } from '@/lib/outcomes'
import { buildRounds, currentInvoice, decisionFor, extractionFor } from '@/lib/rounds'
import { useConsoleStore } from '@/state/consoleStore'
import type { InvoiceStatus } from '@/state/runReducer'

const RUN_LANES: Lane[] = ['unguarded', 'countersign']
const DONE = new Set(['completed', 'failed', 'not_implemented', 'not_running'])

const STREAM_TEXT: Record<StreamStatus, string> = {
  idle: 'no stream',
  connecting: 'connecting',
  open: 'streaming',
  reconnecting: 'reconnecting',
}

// What Sig is doing with the shown invoice, from its Countersign status.
const VERB: Record<InvoiceStatus, string> = {
  pending: 'queued',
  extracting: 'dueling',
  cleared: 'paying',
  paid: 'paid',
  unknown_account: 'paid to an unknown account',
  held: 'held',
  blocked: 'blocked',
  failed: 'failed on',
}

/** False only for a lane this run did not start or that the backend reports as not implemented. */
function laneRuns(status: string): boolean {
  return status !== 'not_implemented' && status !== 'not_running'
}

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  return t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)
}

export function ConsolePage() {
  const { batch, run, runNonce, selectedInvoiceId, setBatch, setRun, selectInvoice, setFocused, refreshBatch } = useConsoleStore()
  const queryClient = useQueryClient()

  const newBatch = useMutation({ mutationFn: () => createBatch({ kind: 'standard' }), onSuccess: setBatch })
  const startRun = useMutation({
    mutationFn: () => {
      if (!batch) throw new Error('Draw a batch first')
      return createRun({ batch_id: batch.batch_id, lanes: RUN_LANES })
    },
    onSuccess: setRun,
  })

  const { state, stream, lastEventAt, reconnect } = useRunEvents(run?.run_id ?? null, runNonce)
  const health = useHealth()
  const balances = useQuery({ queryKey: ['balances'], queryFn: getBalances })
  const cs = state.lanes.countersign
  const ug = state.lanes.unguarded

  // Balances refetch only when a real transfer event arrives; approvals when a hold or resolution does.
  useEffect(() => {
    if (state.paymentsExecuted > 0) void queryClient.invalidateQueries({ queryKey: ['balances'] })
  }, [state.paymentsExecuted, queryClient])
  const approvalEvents = cs.steps.filter((e) => e.type === 'payment.held' || e.type === 'approval.resolved').length
  useEffect(() => {
    if (approvalEvents > 0) void queryClient.invalidateQueries({ queryKey: ['approvals'] })
  }, [approvalEvents, queryClient])

  // An autopilot run's aggregate batch grows with each admitted inbox item: re-read it (and the inbox) on inbox.received.
  const received = state.system.filter((e) => e.type === 'inbox.received').length
  const batchId = run?.batch_id
  // Debounced: subscribing replays the run's history, which can hold many inbox.received events at once.
  useEffect(() => {
    if (received === 0 || !batchId) return
    let stale = false
    const t = setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: ['inbox'] })
      getBatch(batchId)
        .then((b) => !stale && refreshBatch(b))
        .catch(() => {})
    }, 400)
    return () => {
      stale = true
      clearTimeout(t)
    }
  }, [received, batchId, queryClient, refreshBatch])
  // Each processing slice reports lane status; the inbox item's status changes with it.
  const laneStatusEvents = cs.steps.filter((e) => e.type === 'lane.status').length + ug.steps.filter((e) => e.type === 'lane.status').length
  useEffect(() => {
    if (laneStatusEvents === 0) return
    const t = setTimeout(() => void queryClient.invalidateQueries({ queryKey: ['inbox'] }), 400)
    return () => clearTimeout(t)
  }, [laneStatusEvents, queryClient])

  // The autopilot run processes its inbox by itself; a manual run of its aggregate batch isn't offered.
  const autopilot = useAutopilot()
  const onAutopilotRun = Boolean(run) && run?.run_id === autopilot.data?.run_id
  const canRun = Boolean(batch) && !onAutopilotRun
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

  const hasRun = Boolean(run)
  const csStatus = effectiveLaneStatus(hasRun, run?.lanes.find((l) => l.lane === 'countersign'), cs)
  const ugStatus = effectiveLaneStatus(hasRun, run?.lanes.find((l) => l.lane === 'unguarded'), ug)
  const active = hasRun && !state.completed && !DONE.has(csStatus)
  const dueling = active ? currentInvoice(cs.steps) : undefined

  // Show what the user picked; otherwise follow the duel, then the first hold, then the first invoice.
  const invoices = useMemo(() => batch?.invoices ?? [], [batch])
  const picked = invoices.find((i) => i.invoice_id === selectedInvoiceId)
  const firstHeld = invoices.find((i) => cs.invoices[i.invoice_id]?.status === 'held')
  const shown = picked ?? invoices.find((i) => i.invoice_id === dueling) ?? firstHeld ?? invoices[0]
  const shownId = shown?.invoice_id
  useEffect(() => setFocused(shownId ?? null), [shownId, setFocused])

  const labels = useMemo(() => new Map(invoices.map((i) => [i.invoice_id, i.invoice_number] as const)), [invoices])
  const namespaces = useMemo(
    () => handleNamespaces(cs.steps.flatMap((e) => (e.type === 'fields.extracted' ? [e.payload] : []))),
    [cs.steps],
  )
  const handleInvoice = useCallback(
    (h: string) => {
      const id = namespaces.get(splitHandle(h)[0])
      return id === undefined ? undefined : (labels.get(id) ?? id)
    },
    [namespaces, labels],
  )

  const rounds = useMemo(() => (shownId ? buildRounds(cs.steps, shownId) : []), [cs.steps, shownId])
  const hasPlan = rounds.some((r) => r.kind === 'planner')
  const planner = useQuery({
    queryKey: ['planner', run?.run_id, runNonce],
    queryFn: () => getPlannerTranscript(run!.run_id),
    enabled: hasRun && hasPlan,
    retry: false,
  })

  const attackerIds = useMemo(
    () => new Set((balances.data?.accounts ?? []).filter((a) => a.role === 'attacker').map((a) => a.account_id)),
    [balances.data],
  )

  const statuses: Record<string, InvoiceStatus> | null = hasRun
    ? Object.fromEntries(invoices.map((i) => [i.invoice_id, cs.invoices[i.invoice_id]?.status ?? 'pending']))
    : null

  const totals: InboxTotals | null = useMemo(() => {
    if (!hasRun) return null
    const held = invoices.filter((i) => cs.invoices[i.invoice_id]?.status === 'held')
    const offFile = cs.steps.flatMap((e) => (e.type === 'payment.executed' && !e.payload.payment.payee_on_file ? [e.payload.payment] : []))
    const leaked = balances.data
      ? { label: 'to attacker', cents: offFile.filter((p) => attackerIds.has(p.to_account_id)).reduce((n, p) => n + p.amount_cents, 0) }
      : { label: 'not on file', cents: cs.tally.not_on_file_cents }
    return {
      letIn: { count: cs.tally.paid_count, cents: cs.tally.paid_cents },
      held: { count: held.length, cents: held.reduce((n, i) => n + i.amount_cents, 0) },
      leaked,
    }
  }, [hasRun, invoices, cs, balances.data, attackerIds])

  const shownStatus: InvoiceStatus | undefined = shown && hasRun ? (cs.invoices[shown.invoice_id]?.status ?? 'pending') : undefined
  const decision = shownId ? decisionFor(cs.steps, shownId) : undefined
  const extraction = shownId ? extractionFor(cs.steps, shownId) : undefined
  const duelingNumber = dueling ? (labels.get(dueling) ?? dueling) : undefined
  const systemErrors = state.system.filter((e) => e.type === 'error')

  return (
    <div className="grid h-full min-h-[36rem] grid-cols-[clamp(13rem,17vw,17.15rem)_minmax(0,1fr)_clamp(24rem,32vw,32.86rem)]">
      <InboxColumn
        batch={batch}
        newBatch={newBatch}
        statuses={statuses}
        selectedId={shownId}
        onSelect={selectInvoice}
        totals={totals}
        mail={<MailPanel batch={batch} runId={run?.run_id} />}
        controls={
          <>
            <InkButton size="sm" variant="secondary" onClick={() => newBatch.mutate()} disabled={newBatch.isPending}>
              {newBatch.isPending ? 'drawing…' : 'new batch'}
            </InkButton>
            <InkButton
              size="sm"
              onClick={() => run_()}
              disabled={!canRun || runPending}
              aria-keyshortcuts="r"
              title={onAutopilotRun ? 'Autopilot processes this run from the inbox' : undefined}
            >
              {runPending ? 'starting…' : run ? 'run again' : 'run batch'}
              <Kbd>R</Kbd>
            </InkButton>
          </>
        }
      />

      <section aria-label="Sig's duel" className="flex min-h-0 min-w-0 flex-col gap-4 px-8 py-6.5">
        <div className="flex items-start gap-[18px]">
          <SigFace size="large" expression={shownStatus === 'held' ? 'serious' : 'default'} />
          <div className="min-w-0 flex-1">
            <h1 className="m-0 text-[1.357rem] leading-tight">
              <span className="display">Sig</span>{' '}
              <span className="font-display font-semibold text-muted">· AP clerk</span>
            </h1>
            <p className="m-0 truncate text-muted" data-testid="sig-status">
              {!batch
                ? 'no batch yet · draw one to start'
                : !shown
                  ? 'empty batch'
                  : `${shownStatus ? VERB[shownStatus] : 'ready for'} ${shown.invoice_number} · ${shown.vendor_name} · ${formatCents(shown.amount_cents, shown.currency)}`}
            </p>
            {run && (
              <p className="m-0 flex items-center gap-x-2 text-meta whitespace-nowrap text-muted">
                <span data-testid="stream-status" className="min-w-0 truncate">
                  run {run.run_id} · {STREAM_TEXT[stream]}
                  {state.completed && ' · run completed'} · seq {state.lastSeq || '—'}
                </span>
                <ModeTag mode={state.systemMode} />
                {systemErrors.length > 0 && (
                  <span
                    className="text-bad"
                    title={systemErrors.map((e) => (e.type === 'error' ? e.payload.message : '')).join('\n')}
                  >
                    {systemErrors.length} system {systemErrors.length === 1 ? 'warning' : 'warnings'}
                  </span>
                )}
              </p>
            )}
          </div>
        </div>
        {run && (
          <LiveProgress
            key={`${run.run_id}|${runNonce}`}
            lanes={[
              { lane: ug, status: ugStatus },
              { lane: cs, status: csStatus },
            ]}
            stream={stream}
            lastEventAt={lastEventAt}
            reconnect={() => {
              reconnect()
              void health.refetch()
            }}
            backendUnreachable={health.isError}
            invoiceLabel={(id) => labels.get(id) ?? id}
          />
        )}
        {startRun.isError && (
          <RequestError title="Could not start the run" error={startRun.error} onRetry={() => run_()} retrying={runPending} />
        )}

        <DuelTranscript
          rounds={rounds}
          handleInvoice={handleInvoice}
          leakGuard={planner.data?.leak_guard}
          extraLines={shown ? { payee: <VendorHistoryLine vendorId={shown.vendor_id} /> } : undefined}
          empty={
            !batch
              ? 'Draw a batch, then press R. Sig will duel each invoice here.'
              : !run
                ? 'Press R to run the batch.'
                : csStatus === 'not_implemented' || csStatus === 'not_running'
                  ? 'The Countersign lane is not running for this run.'
                  : extraction
                    ? `Read ${shown?.invoice_number}: ${extraction.fields.length} fields quarantined as untrusted. Waiting for the plan and the checks.`
                    : `No rounds yet for ${shown?.invoice_number ?? 'this invoice'}.`
          }
        />

        {active && (
          <p role="status" className="m-0 text-muted">
            <span aria-hidden className="inline-block motion-safe:animate-pulse">
              ✻
            </span>{' '}
            {duelingNumber ? `Dueling ${duelingNumber}…` : 'Reading the inbox…'}
          </p>
        )}

        <div className="mt-auto">
          <ChallengeInput />
        </div>
      </section>

      <OneSentencePanel
        invoice={shown}
        spans={extraction?.instruction_like_spans}
        unguarded={unguardedOutcome(ug.steps, shownId ?? '', laneRuns(ugStatus))}
        countersign={countersignOutcome(cs.steps, shownId ?? '', laneRuns(csStatus))}
        attackerIds={attackerIds}
      >
        {run && shown && shownStatus === 'held' && <ApprovalCard runId={run.run_id} invoice={shown} decision={decision} />}
      </OneSentencePanel>
    </div>
  )
}
