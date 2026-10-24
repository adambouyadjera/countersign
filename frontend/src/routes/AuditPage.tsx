import { EmptyState, Panel } from '@/components/ui'
import { PageHeader } from '@/components/shell/PageHeader'
import { Placeholder } from '@/components/shell/Placeholder'

export function AuditPage() {
  return (
    <div className="mx-auto w-full max-w-[100rem] pr-6 pb-8 pl-8">
      <PageHeader
        folio="III"
        title="Ask the record"
        dek="Every answer is drawn from the event log, with the queries that produced it."
        className="mb-6"
      />
      <div className="grid grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-5">
        <Panel eyebrow="Audit agent" title="Question and answer">
          <EmptyState
            title="No question asked yet"
            body="Questions are answered from the event log by the audit agent, with the queries it ran."
          />
          <Placeholder title="Question, answer and ES|QL steps" source="POST /api/audit/ask · AuditAnswer" />
        </Panel>
        <Panel eyebrow="Approvals" title="Pending holds">
          <Placeholder title="Held payments awaiting a human" source="GET /api/approvals?status=pending" />
        </Panel>
      </div>
    </div>
  )
}
