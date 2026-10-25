import { useQuery } from '@tanstack/react-query'
import { getRedTeamJob } from '@/api/client'
import type { RedTeamJob } from '@/api/types'
import { QueryView } from '@/components/casefile/Unavailable'
import { Card, Chip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { RECORDED_REDTEAM_JOB_ID, redteamHeadline } from '@/lib/recordedRedteam'

type Variant = RedTeamJob['variants'][number]


function Metric({ label, value, testId, bad }: { label: string; value: string; testId: string; bad?: boolean }) {
  return (
    <div>
      <div className="text-meta text-muted">{label}</div>
      <div data-testid={testId} className={cn('font-mono text-lead font-bold', bad && 'text-bad')}>
        {value}
      </div>
    </div>
  )
}

/** The variant list without the generated attack text: ids, template, placement and both lanes' outcomes. */
function VariantTable({ variants }: { variants: Variant[] }) {
  return (
    <div className="max-h-[22rem] overflow-auto">
      <table aria-label="Variants" className="w-full border-collapse font-mono text-small">
        <thead className="sticky top-0 bg-card">
          <tr className="border-b-2 border-ink text-left text-muted">
            <th className="px-1.5 py-1.5 font-medium">Variant</th>
            <th className="px-1.5 py-1.5 font-medium">Template</th>
            <th className="px-1.5 py-1.5 font-medium">Placement</th>
            <th className="px-1.5 py-1.5 font-medium">Unguarded</th>
            <th className="px-1.5 py-1.5 font-medium">Countersign</th>
          </tr>
        </thead>
        <tbody>
          {variants.map((v) => (
            <tr key={v.variant_id} data-variant={v.variant_id} className="rule-dashed">
              <td className="px-1.5 py-1.5">{v.variant_id.split('-').at(-1)}</td>
              <td className="px-1.5 py-1.5">{v.template}</td>
              <td className="px-1.5 py-1.5">{v.placement}</td>
              <td className={cn('px-1.5 py-1.5', v.unguarded_outcome === 'compromised' && 'text-bad')}>{v.unguarded_outcome}</td>
              <td className={cn('px-1.5 py-1.5', v.countersign_outcome === 'error' && 'text-muted')}>{v.countersign_outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** The recorded red-team job, read from GET /api/redteam/{job_id}. Separate from running a new job. */
export function RecordedRedTeam() {
  const q = useQuery({
    queryKey: ['redteam', RECORDED_REDTEAM_JOB_ID],
    queryFn: () => getRedTeamJob(RECORDED_REDTEAM_JOB_ID),
    retry: false,
    staleTime: Infinity,
  })
  return (
    <section aria-label="Recorded red team" className="px-7 pt-5">
      <QueryView query={q} what="the recorded red-team job" endpoint={`GET /api/redteam/${RECORDED_REDTEAM_JOB_ID}`}>
        {(job) => {
          const m = job.metrics
          return (
            <Card label={`recorded · red team · ${job.mutator_model}`} className="flex flex-col gap-3.5 p-[18px]">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h2 data-testid="redteam-headline" className="display m-0 text-big">
                  {redteamHeadline(job)}
                </h2>
                <Chip data-testid="redteam-execution" className="ml-auto py-0 font-mono text-meta">
                  {job.execution === 'dry_run' ? 'dry-run bank: no money moved' : job.execution}
                </Chip>
              </div>
              {m ? (
                <div className="grid grid-cols-3 gap-3 md:grid-cols-6">
                  <Metric label="Variants" value={`${m.variants}`} testId="rt-variants" />
                  <Metric label="Unguarded fooled" value={`${m.unguarded_compromised} / ${m.variants}`} testId="rt-fooled" bad={m.unguarded_compromised > 0} />
                  <Metric label="Countersign unauthorized" value={`${m.countersign_unauthorized}`} testId="rt-unauthorized" bad={m.countersign_unauthorized > 0} />
                  <Metric label="Countersign held" value={`${m.countersign_held}`} testId="rt-held" />
                  <Metric label="False holds on controls" value={`${m.false_holds_on_controls}`} testId="rt-false-holds" bad={m.false_holds_on_controls > 0} />
                  <Metric label="Distinct planner inputs" value={`${m.distinct_planner_prompts}`} testId="rt-distinct" />
                </div>
              ) : (
                <p className="m-0 text-small text-muted">This job reports no metrics.</p>
              )}
              <VariantTable variants={job.variants} />
              <p className="m-0 font-mono text-meta text-muted">
                job {job.job_id} · {job.status} · {job.progress.completed} / {job.progress.total} · GET /api/redteam/{job.job_id}
              </p>
            </Card>
          )
        }}
      </QueryView>
    </section>
  )
}
