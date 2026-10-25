import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { askAudit } from '@/api/client'
import { isApiError } from '@/api/errors'
import { AnswerText } from '@/components/audit/AnswerText'
import { RequestError } from '@/components/console/RequestError'
import { SigSays } from '@/components/sig'
import { Card, Chip, InkButton, InkRow } from '@/components/ui'
import { useAutopilot, useBatch, useRun } from '@/hooks/queries'
import { useHealth } from '@/hooks/useHealth'
import { useRunEvents } from '@/hooks/useRunEvents'
import { cn } from '@/lib/cn'
import { eventSummary } from '@/lib/eventLog'
import { serviceWord } from '@/lib/serviceWord'
import { useConsoleStore } from '@/state/consoleStore'

const LOG_SIZE = 12

// Questions only: the auditor answers from the indexed event log.
const SUGGESTIONS = ['Money sent to accounts not on file', 'Who approved what today?', 'Summarize the latest run']

export function AuditPage() {
  const consoleRun = useConsoleStore((s) => s.run)
  const runNonce = useConsoleStore((s) => s.runNonce)
  const consoleBatch = useConsoleStore((s) => s.batch)
  // No run open on the console: follow the autopilot run (GET /api/autopilot run_id) instead.
  const autopilot = useAutopilot()
  const fallbackRunId = consoleRun ? undefined : (autopilot.data?.run_id ?? undefined)
  const fallbackRun = useRun(fallbackRunId)
  const fallbackBatch = useBatch(fallbackRun.data?.batch_id)
  const runId = consoleRun?.run_id ?? fallbackRunId ?? null
  const batch = consoleRun ? consoleBatch : fallbackBatch.data
  const health = useHealth()
  const elastic = health.data?.services.elastic
  const [question, setQuestion] = useState('')
  const [askedAt, setAskedAt] = useState<number | null>(null)
  const ask = useMutation({
    mutationFn: (q: string) => askAudit({ question: q, ...(runId ? { run_id: runId } : {}) }),
    onMutate: () => setAskedAt(Date.now()),
  })
  const { state } = useRunEvents(runId, consoleRun ? runNonce : 0)

  // A real clock while the auditor works; nothing is simulated.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!ask.isPending) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [ask.isPending])
  const elapsed = askedAt ? Math.max(0, Math.floor((now - askedAt) / 1000)) : 0

  const label = (id: string) => batch?.invoices.find((i) => i.invoice_id === id)?.invoice_number ?? id
  const latest = [...state.events].sort((a, b) => b.seq - a.seq).slice(0, LOG_SIZE)
  const offline503 = isApiError(ask.error) && ask.error.status === 503
  const submit = (q: string) => {
    const t = q.trim()
    if (t) ask.mutate(t)
  }

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_clamp(20rem,28vw,30rem)] items-start gap-[22px] px-7 py-5.5 font-prose">
      <main className="flex min-w-0 flex-col gap-4">
        <h1 className="display m-0 text-[2rem]">Ask the auditor</h1>
        <SigSays expression="calm">I don&apos;t grade my own homework. The auditor reads the event log on its own.</SigSays>

        <form
          aria-label="Ask the auditor"
          className="flex flex-wrap items-center gap-2.5"
          onSubmit={(e) => {
            e.preventDefault()
            submit(question)
          }}
        >
          <label htmlFor="q" className="sr-only">
            Question
          </label>
          <input
            id="q"
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about payments, holds, approvals…"
            className="ink-field min-h-12 min-w-0 flex-1 px-3.5 font-[inherit] text-[1.0715rem] text-fg"
          />
          <InkButton type="submit" className="min-h-12 px-[18px]" disabled={!question.trim() || ask.isPending}>
            {ask.isPending ? 'asking…' : 'Ask'}
          </InkButton>
        </form>
        {ask.isPending && (
          <p role="status" data-testid="audit-waiting" className="m-0 text-small text-muted">
            <span className="font-mono font-bold text-fg tabular">{elapsed}s</span> · The Elastic agent runs its own queries; answers
            can take 1-2 minutes.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <Chip
              as="button"
              pill
              key={s}
              type="button"
              onClick={() => {
                setQuestion(s)
                submit(s)
              }}
              disabled={ask.isPending}
              className="min-h-9 text-small"
            >
              {s}
            </Chip>
          ))}
        </div>
        {elastic && elastic.status !== 'ok' && (
          <p className="m-0 text-small text-muted" data-testid="elastic-health">
            GET /api/health: elastic {serviceWord(elastic)}
            {elastic.detail ? ` · ${elastic.detail}` : ''}
          </p>
        )}

        {offline503 ? (
          <InkRow tone="failed" role="alert" data-testid="elastic-offline" className="p-4">
            <p className="m-0 font-bold text-bad">Elastic is offline</p>
            <p className="m-0 text-small">
              The auditor runs on Elastic Agent Builder, so it can&apos;t answer right now. Countersign won&apos;t answer audit
              questions from its own model instead.
            </p>
            <p className="m-0 mt-1 font-mono text-meta text-muted">
              POST /api/audit/ask → 503 {isApiError(ask.error) && ask.error.code}: {ask.error?.message}
            </p>
          </InkRow>
        ) : ask.isError ? (
          isApiError(ask.error) && ask.error.status === 404 ? (
            <p data-testid="not-available" className="m-0 text-small text-muted">
              not available yet · POST /api/audit/ask → 404 {ask.error.code}
            </p>
          ) : (
            <RequestError title="The auditor could not answer" error={ask.error} onRetry={() => submit(question)} />
          )
        ) : ask.data ? (
          <Card as="article" aria-label="Answer" className="flex flex-col gap-3.5 p-5">
            <div data-testid="answer-text" className="text-body">
              <AnswerText text={ask.data.answer} />
            </div>
            {ask.data.steps.map((s, i) => (
              <InkRow as="details" key={i} open={i === 0} className="px-3.5 py-2.5">
                <summary className="cursor-pointer font-mono text-small">
                  step {i + 1} · {s.tool_id}
                  {s.row_count !== undefined && ` · ${s.row_count} ${s.row_count === 1 ? 'row' : 'rows'}`}
                </summary>
                {s.esql ? (
                  <pre data-testid="esql" className="m-0 mt-2.5 overflow-x-auto rounded-[6px] bg-bg p-3 font-mono text-meta leading-[1.7] whitespace-pre-wrap">
                    {s.esql.replaceAll(' | ', '\n| ')}
                  </pre>
                ) : (
                  <p className="m-0 mt-2.5 text-small text-muted">No ES|QL for this step.</p>
                )}
              </InkRow>
            ))}
            <div className="text-small text-muted">
              Answered by Elastic Agent Builder (agent <span className="font-mono">{ask.data.agent_id}</span>) from the indexed
              event log. Countersign never answers audit questions from its own model.
            </div>
          </Card>
        ) : null}
      </main>

      <Card
        as="aside"
        aria-label="Event log"
        label={`event log · ${consoleRun ? 'current run, latest' : fallbackRunId ? 'autopilot run, latest' : 'no run'}`}
        className="flex flex-col gap-2 p-[18px]"
      >
        {!runId ? (
          <p className="m-0 text-small text-muted">
            {autopilot.isPending ? 'Looking for a run…' : 'No run yet: run a batch on the console or turn autopilot on.'}
          </p>
        ) : latest.length === 0 ? (
          <p className="m-0 text-small text-muted">Waiting for the run&apos;s events…</p>
        ) : (
          <ol aria-label="Latest events" className="m-0 flex list-none flex-col p-0 font-mono text-small">
            {latest.map((e) => {
              const s = eventSummary(e, label)
              return (
                <li
                  key={e.seq}
                  data-seq={e.seq}
                  data-tone={s.tone}
                  className={cn(
                    'rule-dashed grid grid-cols-[3ch_minmax(0,1fr)_auto] gap-2 py-1.5 last:border-b-0',
                    e.type === 'payment.held' && 'bg-bad-bg',
                  )}
                >
                  <span className="text-right text-muted">{e.seq}</span>
                  <span className="min-w-0 truncate">{s.what}</span>
                  {s.tag && (
                    <span
                      className={cn(
                        'max-w-[12ch] truncate',
                        s.tone === 'good' ? 'text-good' : s.tone === 'bad' ? 'text-bad' : 'text-muted',
                      )}
                    >
                      {s.tag}
                    </span>
                  )}
                </li>
              )
            })}
          </ol>
        )}
        {runId && (
          <p className="m-0 text-meta text-muted">
            From the {consoleRun ? 'run' : 'autopilot run'}&apos;s event stream ({runId}); the auditor queries the same events in
            Elastic.
          </p>
        )}
      </Card>
    </div>
  )
}
