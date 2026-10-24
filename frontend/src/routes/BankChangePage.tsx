import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { createBatch, createRun, getBankChangeEvidence, getVendor, listBankChangeEvidence } from '@/api/client'
import type { BankChangeReplay, BankChangeReplaySummary, Batch, Run } from '@/api/types'
import { LanePanel, LetterCard, TransferStrip, type LetterView } from '@/components/bankchange/BankChangeViews'
import { RequestError } from '@/components/console/RequestError'
import { QueryView } from '@/components/casefile/Unavailable'
import { Chip, InkButton } from '@/components/ui'
import { useApprovals, useDocument, usePlannerTranscript } from '@/hooks/queries'
import { useReplayPlayer } from '@/hooks/useReplayPlayer'
import { useRunEvents } from '@/hooks/useRunEvents'
import { lanesFromEvents, liveContext, replayContext, replayDelays, REPLAY_SPEEDUP } from '@/lib/bankChange'
import { isMockMode } from '@/lib/env'

/** What the live button starts: the contract's bank-change batch, both lanes, the unguarded agent allowed to apply letters. */
const LIVE_RUN = { lanes: ['unguarded', 'countersign'] as Run['lanes'][number]['lane'][], unguarded_mode: 'ap_with_vendor_updates' as const }

function SourceLabel({ children, live }: { children: string; live?: boolean }) {
  return (
    <Chip
      data-testid="run-source-label"
      tone={live ? 'bad' : 'default'}
      className="max-w-full py-1 font-mono text-small font-bold break-all"
    >
      {children}
    </Chip>
  )
}

function ReplayView({ replay }: { replay: BankChangeReplay }) {
  const ctx = useMemo(() => replayContext(replay), [replay])
  const delays = useMemo(() => replayDelays(replay.events), [replay.events])
  const player = useReplayPlayer(delays, replay.run_id)
  const lanes = useMemo(() => lanesFromEvents(replay.events.slice(0, player.shown), ctx), [replay.events, player.shown, ctx])
  const current = player.shown > 0 ? replay.events[player.shown - 1] : undefined
  const letter: LetterView | undefined = replay.letter && {
    vendorName: replay.letter.vendor_name,
    requestedAccount: replay.letter.requested_account_display,
    onFileAccount: replay.letter.account_on_file_display,
    document: replay.letter.document,
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 px-7 pb-3">
        <SourceLabel>{`Recording of a live run · ${replay.recorded_at} · ${replay.run_id}`}</SourceLabel>
        {player.playing ? (
          <>
            <InkButton size="sm" variant="secondary" onClick={player.finish}>
              Skip to the end
            </InkButton>
            <span role="status" data-testid="replay-progress" className="font-mono text-small text-muted">
              event {player.shown} of {replay.events.length}
              {current && ` · seq ${current.seq} · ${current.type} · recorded ${current.ts}`} · about {REPLAY_SPEEDUP}× speed
            </span>
          </>
        ) : (
          <InkButton size="sm" onClick={player.play} disabled={replay.events.length === 0}>
            Replay recorded run
          </InkButton>
        )}
      </div>
      {(!replay.complete || replay.limitations.length > 0) && (
        <div className="px-7 pb-3 text-small text-bad" data-testid="limitations">
          {!replay.complete && <p className="m-0 font-bold">Incomplete recording (status {replay.status}).</p>}
          <ul className="m-0 pl-5">
            {replay.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </div>
      )}
      <Screen
        letter={letter}
        lanes={lanes}
        unguardedMode={replay.unguarded_mode ?? replay.run.unguarded_mode}
        leakGuard={replay.leak_guard ?? null}
      />
    </>
  )
}

function LiveView({ batch, run, onExit }: { batch: Batch; run: Run; onExit: () => void }) {
  const qc = useQueryClient()
  const { state, stream } = useRunEvents(run.run_id)
  const approvals = useApprovals()
  const vendorIds = useMemo(() => [...new Set(batch.invoices.map((i) => i.vendor_id))], [batch.invoices])
  const vendors = useQueries({
    queries: vendorIds.map((id) => ({ queryKey: ['vendor', id], queryFn: () => getVendor(id), retry: false })),
  })
  const onFileByVendor = useMemo(() => {
    const m: Record<string, string> = {}
    for (const v of vendors) if (v.data) m[v.data.vendor_id] = v.data.account_on_file_display
    return m
  }, [vendors])
  const letterDoc = batch.supporting_documents?.find((d) => d.document_type === 'vendor_bank_change_letter')
  const doc = useDocument(letterDoc?.document_id)
  const planner = usePlannerTranscript(run.run_id, state.completed)

  // A hold opens an approval; read it again so the hold can name both accounts.
  const holds = state.events.filter((e) => e.type === 'payment.held').length
  useEffect(() => {
    if (holds > 0) void qc.invalidateQueries({ queryKey: ['approvals'] })
  }, [holds, qc])

  const ctx = useMemo(
    () => liveContext(batch.invoices, onFileByVendor, approvals.data ?? [], run.run_id),
    [batch.invoices, onFileByVendor, approvals.data, run.run_id],
  )
  const lanes = useMemo(() => lanesFromEvents(state.events, ctx), [state.events, ctx])
  const letterVendor = letterDoc && batch.invoices.find((i) => i.invoice_id === letterDoc.invoice_id)
  const letter: LetterView | undefined = letterDoc && {
    vendorName: letterVendor?.vendor_name,
    requestedAccount: ctx.letterAccount,
    onFileAccount: onFileByVendor[letterDoc.vendor_id],
    document: doc.data,
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 px-7 pb-3">
        <SourceLabel live>{`Live · ${run.run_id}`}</SourceLabel>
        <span role="status" className="font-mono text-small text-muted">
          stream {stream} · {state.events.length} events{state.completed ? ' · run completed' : ''}
        </span>
        <InkButton size="sm" variant="secondary" onClick={onExit} className="ml-auto">
          Back to the recording
        </InkButton>
      </div>
      <Screen
        letter={letter}
        letterState={
          letterDoc && (
            <QueryView query={doc} what="the letter" endpoint={`GET /api/documents/${letterDoc.document_id}`}>
              {() => null}
            </QueryView>
          )
        }
        lanes={lanes}
        unguardedMode={run.unguarded_mode}
        leakGuard={planner.data?.leak_guard ?? null}
      />
    </>
  )
}

function Screen({
  letter,
  letterState,
  lanes,
  unguardedMode,
  leakGuard,
}: {
  letter: LetterView | undefined
  letterState?: ReactNode
  lanes: ReturnType<typeof lanesFromEvents>
  unguardedMode?: Run['unguarded_mode']
  leakGuard: BankChangeReplay['leak_guard'] | null
}) {
  return (
    <div className="flex flex-col gap-[18px] px-7 pb-7">
      <LetterCard letter={letter} documentState={letterState} />
      <div className="grid grid-cols-2 items-start gap-[18px]">
        <LanePanel lane={lanes.unguarded} unguardedMode={unguardedMode} />
        <LanePanel lane={lanes.countersign} extractionModels={lanes.extractionModels} leakGuard={leakGuard} />
      </div>
      <TransferStrip lanes={lanes} />
    </div>
  )
}

function runOption(s: BankChangeReplaySummary): string {
  return `${s.recorded_at} · ${s.run_id} · ${s.status}${s.complete ? '' : ' (incomplete)'} · ${s.unguarded_model}${s.is_default ? ' · default' : ''}`
}

export function BankChangePage() {
  const list = useQuery({ queryKey: ['bank-change-evidence'], queryFn: listBankChangeEvidence, retry: false, staleTime: Infinity })
  const [picked, setPicked] = useState<string | null>(null)
  const runId = picked ?? list.data?.find((s) => s.is_default)?.run_id ?? list.data?.[0]?.run_id
  const detail = useQuery({
    queryKey: ['bank-change-evidence', runId],
    queryFn: () => getBankChangeEvidence(runId!),
    enabled: Boolean(runId),
    retry: false,
    staleTime: Infinity,
  })
  const [live, setLive] = useState<{ batch: Batch; run: Run } | null>(null)
  const start = useMutation({
    mutationFn: async () => {
      const batch = await createBatch({ kind: 'bank_change' })
      const run = await createRun({ batch_id: batch.batch_id, ...LIVE_RUN })
      return { batch, run }
    },
    onSuccess: setLive,
  })
  const mock = isMockMode()

  return (
    <div className="flex flex-col font-prose">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-7 pt-5 pb-3">
        <h1 className="display m-0 text-[2rem]">Bank-change letter</h1>
        <label className="flex items-center gap-2 font-mono text-small">
          <span className="text-muted">recorded run</span>
          <select
            aria-label="Recorded run"
            className="ink-field max-w-[44rem] px-2 py-1.5 font-mono text-small text-fg"
            value={runId ?? ''}
            disabled={!list.data || list.data.length === 0}
            onChange={(e) => {
              setPicked(e.target.value)
              setLive(null)
            }}
          >
            {!list.data && <option value="">{list.isError ? 'unavailable' : 'loading…'}</option>}
            {list.data?.map((s) => (
              <option key={s.run_id} value={s.run_id}>
                {runOption(s)}
              </option>
            ))}
          </select>
        </label>
        <InkButton
          variant="secondary"
          size="sm"
          className="ml-auto"
          onClick={() => start.mutate()}
          disabled={mock || start.isPending || live !== null}
          title={mock ? 'Mock mode replays fixtures; a live run needs the backend.' : undefined}
        >
          {start.isPending ? 'Starting…' : 'Run live (~6 min)'}
        </InkButton>
      </div>
      {mock && (
        <p className="m-0 px-7 pb-3 text-small text-muted">Mock mode is on: live runs are off, the recording below is the contract fixture.</p>
      )}
      {start.error && (
        <div className="px-7 pb-3">
          <RequestError title="Could not start a live run" error={start.error} />
        </div>
      )}
      {list.isError && (
        <div className="px-7 pb-3">
          <QueryView query={list} what="the recorded runs" endpoint="GET /api/evidence/bank-change">
            {() => null}
          </QueryView>
        </div>
      )}

      {list.isPending && <p className="m-0 px-7 text-small text-muted">reading the recorded runs…</p>}
      {live ? (
        <LiveView key={live.run.run_id} batch={live.batch} run={live.run} onExit={() => setLive(null)} />
      ) : list.data?.length === 0 ? (
        <p className="m-0 px-7 text-small text-muted">No recorded bank-change runs on the server.</p>
      ) : (
        runId && (
          <div className="flex flex-col">
            {detail.data ? (
              <ReplayView replay={detail.data} />
            ) : (
              <div className="px-7">
                <QueryView query={detail} what="the recorded run" endpoint={`GET /api/evidence/bank-change/${runId}`}>
                  {() => null}
                </QueryView>
              </div>
            )}
          </div>
        )
      )}
    </div>
  )
}
