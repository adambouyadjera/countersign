import { useCallback, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import type { Approval, PolicyDecision, SourceSpan } from '@/api/types'
import { AgentTranscriptView } from '@/components/casefile/AgentTranscriptView'
import { CountersignActions } from '@/components/casefile/CountersignActions'
import { DocumentViewer } from '@/components/casefile/DocumentViewer'
import { PlannerTranscriptDialog } from '@/components/casefile/PlannerTranscriptDialog'
import { ProvenanceColumns } from '@/components/casefile/ProvenanceColumns'
import { QueryView } from '@/components/casefile/Unavailable'
import { VendorStrip } from '@/components/casefile/VendorStrip'
import { SigSays } from '@/components/sig'
import {
  useAgentTranscript,
  useApprovals,
  useBatch,
  useDecisions,
  useDocument,
  useGraph,
  usePlannerTranscript,
  useRun,
  useVendor,
} from '@/hooks/queries'
import { useActingAs } from '@/hooks/useActingAs'
import { decisionLine, type LineMark } from '@/lib/caseFile'
import { maskAccount } from '@/lib/account'
import { caseColumns, connectedIds, decisionFromGraph, instructionSpanIds, type CaseNode } from '@/lib/caseGraph'
import { cn } from '@/lib/cn'
import { formatCents } from '@/lib/money'

const CARD = 'rounded-[10px] border border-line bg-card'
const EYEBROW = 'font-prose text-meta tracking-[0.08em] text-muted uppercase'

const OUTCOME_WORD: Record<PolicyDecision['outcome'], string> = { ALLOW: 'PAID', HOLD: 'HELD', BLOCK: 'BLOCKED' }

export function CaseFilePage() {
  const { runId = '', invoiceId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const lane = params.get('lane') === 'unguarded' ? 'unguarded' : 'countersign'
  const plannerOpen = params.get('view') === 'planner'
  const setParam = useCallback(
    (key: string, value: string | null) =>
      setParams(
        (p) => {
          const next = new URLSearchParams(p)
          if (value === null) next.delete(key)
          else next.set(key, value)
          return next
        },
        { replace: key === 'view' ? false : true },
      ),
    [setParams],
  )

  const run = useRun(runId)
  const batch = useBatch(run.data?.batch_id)
  const invoice = batch.data?.invoices.find((i) => i.invoice_id === invoiceId)
  const graph = useGraph(runId, invoiceId)
  const decisions = useDecisions(runId)
  const approvals = useApprovals()
  const vendor = useVendor(invoice?.vendor_id)
  const { people, person } = useActingAs()
  const planner = usePlannerTranscript(runId, Boolean(graph.data))
  const agent = useAgentTranscript(runId, invoiceId, lane === 'unguarded')

  const g = graph.data
  const spanNodes = useMemo(() => (g ? g.nodes.filter((n) => n.kind === 'span' && n.span) : []), [g])
  const documentId = invoice?.document_id ?? spanNodes[0]?.span?.document_id
  const doc = useDocument(documentId)

  const [selected, setSelected] = useState<CaseNode | null>(null)
  const columns = useMemo(
    () =>
      g
        ? caseColumns(g, {
            mask: maskAccount,
            personName: (id) => people.data?.find((p) => p.person_id === id)?.name ?? id,
          })
        : null,
    [g, people.data],
  )
  const linked = useMemo(() => (g && selected ? connectedIds(g, selected.id) : undefined), [g, selected])
  const docSpans = useMemo(() => {
    if (!g) return []
    const instr = instructionSpanIds(g)
    return spanNodes.map((n) => ({ span: n.span as SourceSpan, kind: (instr.has(n.id) ? 'instruction' : 'field') as LineMark['kind'] }))
  }, [g, spanNodes])

  const decision =
    decisions.data?.find((d) => d.invoice_id === invoiceId) ?? (g ? decisionFromGraph(g) : undefined)
  const reasonCodes = decisions.data?.find((d) => d.invoice_id === invoiceId)?.reason_codes
  const approval: Approval | undefined = approvals.data?.find((a) => a.run_id === runId && a.invoice_id === invoiceId)
  const remitField = g?.nodes.find((n) => n.kind === 'field' && n.label === 'remit_account_number')?.detail?.value
  const requestedAccount = approval?.requested_account_display ?? (typeof remitField === 'string' ? remitField : undefined)
  const endIso = approval?.created_at ?? run.data?.created_at
  const leak = planner.data?.leak_guard
  const selectedLine = selected?.span ? `${selected.span.page}:${selected.span.line}` : undefined
  const caseBase = `/runs/${encodeURIComponent(runId)}/invoices/${encodeURIComponent(invoiceId)}`

  return (
    <div className="flex flex-col font-prose">
      <div className="flex flex-wrap items-baseline gap-4 px-7 pt-4.5 pb-1.5">
        <h1 className="m-0 font-prose text-[1.857rem] font-extrabold">
          {invoice
            ? `${invoice.invoice_number} · ${invoice.vendor_name} · ${formatCents(invoice.amount_cents, invoice.currency)}`
            : `Case file · ${invoiceId}`}
        </h1>
        {decision && (
          <span
            data-testid="outcome-badge"
            className={cn(
              'rounded-[6px] border px-2.5 py-0.5 font-mono',
              decision.outcome === 'ALLOW' ? 'border-good text-good' : 'border-bad-line bg-bad-bg text-bad',
            )}
          >
            {OUTCOME_WORD[decision.outcome]}
            {(reasonCodes ?? approval?.reason_codes ?? []).map((c) => ` · ${c}`)}
          </span>
        )}
        <nav aria-label="Lane" className="ml-auto flex gap-1.5 font-mono">
          {(['countersign', 'unguarded'] as const).map((l) => (
            <Link
              key={l}
              to={l === 'countersign' ? caseBase : `${caseBase}?lane=unguarded`}
              replace
              aria-current={lane === l ? 'page' : undefined}
              className={cn(
                'rounded-[6px] px-3 py-1.5 no-underline',
                lane === l ? 'bg-accent text-on-accent' : 'border border-line text-fg',
              )}
            >
              {l} lane
            </Link>
          ))}
        </nav>
      </div>

      <div className="grid grid-cols-[clamp(17rem,24vw,25.72rem)_minmax(0,1fr)_clamp(17rem,24vw,25.72rem)] items-start gap-[18px] px-7 pt-3 pb-6">
        <section aria-label="Document" className={cn(CARD, 'py-4')}>
          <div className={cn(EYEBROW, 'px-4 pb-2')}>the invoice as received</div>
          {documentId ? (
            <div className="px-0">
              <QueryView query={doc} what="the document" endpoint={`GET /api/documents/${documentId}`}>
                {(d) => <DocumentViewer doc={d} spans={docSpans} selected={selectedLine} />}
              </QueryView>
            </div>
          ) : (
            <p className="m-0 px-4 text-small text-muted">
              {run.isPending || graph.isPending ? 'finding the document…' : 'No document id: the run, batch and graph are all unavailable.'}
            </p>
          )}
        </section>

        {lane === 'countersign' ? (
          <section aria-label="Provenance graph" className={cn(CARD, 'flex flex-col gap-3 px-[18px] py-4')}>
            <div className={EYEBROW}>where every value came from</div>
            <QueryView query={graph} what="the provenance graph" endpoint={`GET /api/runs/${runId}/invoices/${invoiceId}/graph`}>
              {() =>
                columns && (
                  <ProvenanceColumns
                    columns={columns}
                    selectedId={selected?.id}
                    linked={linked}
                    onSelect={setSelected}
                    invoiceNumber={invoice?.invoice_number}
                    handleCaption={
                      leak &&
                      (leak.matches === 0 ? (
                        <>
                          the planner saw these names, never the words: {leak.strings_checked} document strings checked,
                          none reached its prompt
                        </>
                      ) : (
                        <span className="text-bad">{leak.matches} document strings reached the planner's prompt</span>
                      ))
                    }
                  />
                )
              }
            </QueryView>
            <div className="text-meta text-muted">
              Dashed red = came from untrusted text. Solid = trusted record. Click a node to highlight its line.
            </div>
          </section>
        ) : (
          <section aria-label="Agent transcript" className={cn(CARD, 'flex flex-col gap-3 px-[18px] py-4')}>
            <div className={EYEBROW}>what the unguarded agent did</div>
            <QueryView
              query={agent}
              what="the agent transcript"
              endpoint={`GET /api/runs/${runId}/invoices/${invoiceId}/agent_transcript`}
            >
              {(t) => <AgentTranscriptView transcript={t} />}
            </QueryView>
          </section>
        )}

        <section aria-label="Decision" className="flex flex-col gap-3.5">
          <SigSays expression="serious">
            <span data-testid="decision-line">
              {decision
                ? decisionLine(decision)
                : decisions.isPending || graph.isPending
                  ? 'Reading the decision…'
                  : "I can't find a decision for this invoice yet."}
            </span>
          </SigSays>

          <div className={cn(CARD, 'p-4')}>
            <div className={EYEBROW}>vendor history{invoice && ` · ${invoice.vendor_name}`}</div>
            {invoice ? (
              <QueryView query={vendor} what="the vendor history" endpoint={`GET /api/vendors/${invoice.vendor_id}`}>
                {(v) =>
                  endIso ? (
                    <VendorStrip vendor={v} people={people.data ?? []} endIso={endIso} requestedAccount={requestedAccount} />
                  ) : null
                }
              </QueryView>
            ) : (
              <p className="m-0 mt-2 text-small text-muted">Needs the invoice's vendor (GET /api/runs, /api/batches).</p>
            )}
          </div>

          <div className={cn(CARD, 'flex flex-col gap-2.5 p-4')}>
            <div className={EYEBROW}>countersign</div>
            <QueryView query={approvals} what="approvals" endpoint="GET /api/approvals">
              {() =>
                approval ? (
                  <CountersignActions approval={approval} actor={person} people={people.data ?? []} />
                ) : (
                  <p className="m-0 text-small text-muted">
                    Nothing to countersign: no approval for this invoice in this run.
                  </p>
                )
              }
            </QueryView>
          </div>

          <button
            type="button"
            onClick={() => setParam('view', 'planner')}
            className="cursor-pointer self-start border-0 bg-transparent p-0 font-mono text-small text-accent underline"
          >
            what the planner saw →
          </button>
        </section>
      </div>

      {plannerOpen && <PlannerTranscriptDialog runId={runId} onClose={() => setParam('view', null)} />}
    </div>
  )
}
