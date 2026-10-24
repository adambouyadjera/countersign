import { EmptyState, Panel } from '@/components/ui'
import { PageHeader } from '@/components/shell/PageHeader'
import { Placeholder } from '@/components/shell/Placeholder'

export function RedTeamPage() {
  return (
    <div className="mx-auto w-full max-w-[100rem] pr-6 pb-8 pl-8">
      <PageHeader
        folio="II"
        title="Attack Lab"
        dek="Write an invoice that lies, and see which agent believes it."
        className="mb-6"
      />
      <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-5">
        <Panel eyebrow="Compose" title="Write an attack">
          <Placeholder title="Attack text, vendor and placement" source="POST /api/attacks · AttackRequest" />
        </Panel>
        <Panel eyebrow="Red team" title="Variant sweep">
          <EmptyState
            title="No sweep has run"
            body="Mutated attacks run against both lanes on a dry-run bank. Results appear here when a job completes."
          />
          <Placeholder
            title="Job progress and per-variant outcomes"
            source="POST /api/redteam → GET /api/redteam/{job_id}"
          />
        </Panel>
      </div>
    </div>
  )
}
