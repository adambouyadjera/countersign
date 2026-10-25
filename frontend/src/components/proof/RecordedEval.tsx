import { useQueries } from '@tanstack/react-query'
import { getEvalJob } from '@/api/client'
import type { EvalJob } from '@/api/types'
import { QueryView } from '@/components/casefile/Unavailable'
import { Card, Chip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { errorTypeRows } from '@/lib/proof'
import { ANSWER_KEY, RECORDED_EVALS, sameItems } from '@/lib/recordedEval'

export function EvalTable({ job, idPrefix }: { job: EvalJob; idPrefix: string }) {
  const rows = errorTypeRows(job)
  return (
    <table className="w-full border-collapse text-small">
      <thead>
        <tr className="border-b-2 border-ink text-left text-muted">
          <th className="px-1.5 py-1.5 font-medium">Planted error</th>
          <th className="px-1.5 py-1.5 font-medium">Expected</th>
          <th className="px-1.5 py-1.5 font-medium">Caught</th>
          <th className="px-1.5 py-1.5 font-medium">Misses</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.errorType} data-error-type={r.errorType} className="rule-dashed">
            <td className="px-1.5 py-1.5">{r.label}</td>
            <td className="px-1.5 py-1.5 font-mono">{r.expected}</td>
            <td className={cn('px-1.5 py-1.5 font-mono', r.caught < r.total && 'text-bad')}>
              {r.caught} / {r.total}
            </td>
            <td className="px-1.5 py-1.5">
              {r.misses.length > 0 ? (
                <a href={`#${idPrefix}-miss-${r.errorType}`} className="text-bad">
                  {r.misses.length} · view
                </a>
              ) : (
                <span className="text-muted">none</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function MissList({ job, idPrefix }: { job: EvalJob; idPrefix: string }) {
  const misses = job.items.filter((i) => !i.passed)
  if (misses.length === 0) return <p className="m-0 text-small text-muted">No misses.</p>
  return (
    <ul aria-label="Misses" className="m-0 list-none p-0 font-mono text-small">
      {misses.map((m) => (
        <li key={m.item_id} id={`${idPrefix}-miss-${m.error_type}`} className="text-bad">
          {m.item_id} · {m.invoice_number} · {m.error_type} · expected {m.expected_outcome}
          {m.expected_reason_code && ` (${m.expected_reason_code})`} · got {m.actual_outcome}
          {m.actual_reason_codes.length > 0 && ` (${m.actual_reason_codes.join(', ')})`}
        </li>
      ))}
    </ul>
  )
}

function RecordedCard({ title, job, index }: { title: string; job: EvalJob; index: number }) {
  const passed = job.items.filter((i) => i.passed).length
  const clean = job.items.filter((i) => i.error_type === 'clean').length
  const m = job.metrics
  return (
    <Card as="section" aria-label={title} label={`recorded · ${title}`} className="flex min-w-0 flex-col gap-3 p-[18px]">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span data-testid={`recorded-score-${index}`} className={cn('font-mono text-[2.143rem] font-bold', passed < job.items.length && 'text-bad')}>
          {passed} / {job.items.length}
        </span>
        <span className="text-small text-muted">match the answer key</span>
        <Chip className="ml-auto py-0 font-mono text-meta">{job.execution === 'dry_run' ? 'dry-run bank' : job.execution}</Chip>
      </div>
      <div className="grid grid-cols-2 gap-2 text-small">
        <div>
          <div className="text-meta text-muted">False holds on clean invoices</div>
          <div data-testid={`recorded-false-holds-${index}`} className={cn('font-mono text-lead font-bold', (m?.false_holds_on_clean ?? 0) > 0 && 'text-bad')}>
            {m ? `${m.false_holds_on_clean} / ${clean}` : '—'}
          </div>
        </div>
        <div>
          <div className="text-meta text-muted">Unauthorized would-pay</div>
          <div data-testid={`recorded-unauthorized-${index}`} className={cn('font-mono text-lead font-bold', (m?.unauthorized_payments ?? 0) > 0 && 'text-bad')}>
            {m ? m.unauthorized_payments : '—'}
          </div>
        </div>
      </div>
      <EvalTable job={job} idPrefix={`rec${index}`} />
      <MissList job={job} idPrefix={`rec${index}`} />
      <p className="m-0 font-mono text-meta text-muted">
        job {job.job_id} · {job.status} · GET /api/eval/{job.job_id}
      </p>
    </Card>
  )
}

export function RecordedEvals() {
  const jobs = useQueries({
    queries: RECORDED_EVALS.map((r) => ({
      queryKey: ['eval', r.jobId],
      queryFn: () => getEvalJob(r.jobId),
      retry: false,
      staleTime: Infinity,
    })),
  })
  const [a, b] = jobs.map((q) => q.data)
  return (
    <section aria-label="Recorded results" className="flex flex-col gap-3 px-7 pt-3">
      <div className="grid grid-cols-2 items-start gap-[18px]">
        {RECORDED_EVALS.map((r, i) => (
          <QueryView key={r.jobId} query={jobs[i]!} what={`the recorded run "${r.title}"`} endpoint={`GET /api/eval/${r.jobId}`}>
            {(job) => <RecordedCard index={i} title={r.title} job={job} />}
          </QueryView>
        ))}
      </div>
      <p data-testid="answer-key-note" className="m-0 text-small text-muted">
        The answer key was committed before the first run
        {ANSWER_KEY.sha256 ? ` (SHA-256 ${ANSWER_KEY.sha256})` : ''}
        {ANSWER_KEY.unchanged === true ? ' and is unchanged for the second.' : '.'}
        {sameItems(a, b) && ` Both runs scored the same ${a!.items.length} invoices.`}
      </p>
    </section>
  )
}
