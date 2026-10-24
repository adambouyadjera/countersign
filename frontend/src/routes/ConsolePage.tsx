import { Button, LedgerRule, Panel, StatusDot } from '@/components/ui'
import { PageHeader } from '@/components/shell/PageHeader'
import { Placeholder } from '@/components/shell/Placeholder'

// The contract's default run instruction. It is static, trusted text, not run output.
const DEFAULT_INSTRUCTION =
  'Pay every invoice in this batch that is due within 30 days and matches an open purchase order. Pay only to accounts on file; anything else needs human approval.'

const TALLY_LABELS = ['Paid', 'Held', 'Blocked', 'Not on file'] as const

function LaneColumn({
  letter,
  title,
  description,
  events,
}: {
  letter: string
  title: string
  description: string
  events: string
}) {
  return (
    <Panel
      dense
      aria-label={`Lane ${letter}: ${title}`}
      title={
        <>
          <span className="mr-2 text-ink-soft italic">{letter}.</span>
          {title}
        </>
      }
      className="flex min-h-0 flex-col"
      bodyClassName="flex min-h-0 flex-1 flex-col"
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="smallcaps text-ink-soft">Lane {letter}</span>
        <StatusDot tone="idle" label="No run started" showLabel />
      </div>
      <p className="m-0 mb-3 min-h-[2lh] text-[0.875rem] leading-snug text-ink-soft">{description}</p>
      <Placeholder title="Per-invoice activity for this lane" source={events} className="min-h-28 flex-1" />
      <LedgerRule label="Lane tally" variant="double" className="mt-3 mb-1.5" />
      <dl className="m-0 grid grid-cols-4">
        {TALLY_LABELS.map((l) => (
          <div key={l} className="min-w-0 border-l border-rule-strong pl-2 flex flex-col first:border-l-0 first:pl-0">
            <dt className="smallcaps leading-tight text-ink-soft">{l}</dt>
            <dd className="m-0 mt-auto font-mono tabular text-[1.5rem] leading-tight text-ink-soft">—</dd>
          </div>
        ))}
      </dl>
    </Panel>
  )
}

export function ConsolePage() {
  return (
    <div className="flex min-h-0 flex-1 flex-col pr-6 pb-5 pl-8">
      <PageHeader
        compact
        folio="I"
        title="Two agents, one batch"
        dek="The same invoices, paid once by an unguarded agent and once under countersignature."
        className="mb-4"
      />
      <div className="grid min-h-0 flex-1 grid-cols-[minmax(15rem,19rem)_minmax(0,1fr)_minmax(15rem,19rem)] gap-5">
        <aside aria-label="Invoice batch" className="flex min-w-0 flex-col gap-4">
          <Panel dense eyebrow="Batch" title="Invoice batch">
            <div className="grid grid-cols-[1fr_auto] gap-x-3 border-b border-ink pb-1">
              <span className="smallcaps text-ink-soft">Invoice · Vendor</span>
              <span className="smallcaps text-right text-ink-soft">Amount · Due</span>
            </div>
            <Placeholder
              title="Five invoices, one poisoned"
              source="POST /api/batches → Batch.invoices"
              className="mt-3"
            >
              <ol className="m-0 list-none p-0">
                {[1, 2, 3, 4, 5].map((n) => (
                  <li
                    key={n}
                    className="flex h-7 items-center gap-2 border-b border-rule/70 font-mono text-[0.75rem] text-ink-soft"
                  >
                    <span className="w-5 text-right">{n}.</span>
                    <span aria-hidden className="h-px flex-1" />
                  </li>
                ))}
              </ol>
            </Placeholder>
            <div className="mt-4 flex gap-2">
              <Button disabled className="flex-1" title="Not wired yet">
                New batch
              </Button>
              <Button variant="quiet" disabled title="Not wired yet">
                Reset
              </Button>
            </div>
          </Panel>
        </aside>

        <section aria-label="Twin lanes" className="flex min-w-0 flex-col gap-4">
          <div className="border-l-4 border-ink bg-paper py-2.5 pr-4 pl-4">
            <div className="flex items-baseline justify-between gap-4">
              <p className="smallcaps m-0 text-ink-soft">Standing instruction · trusted · contract default</p>
              <Button size="sm" disabled title="Not wired yet">
                Run both lanes
              </Button>
            </div>
            <p className="m-0 mt-1 font-display text-[1.3125rem] leading-snug text-ink">“{DEFAULT_INSTRUCTION}”</p>
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-2 gap-4">
            <LaneColumn
              letter="A"
              title="Unguarded agent"
              description="Reads each invoice and calls the bank tool directly. Whatever the document says, it may act on."
              events="SSE lane=unguarded · document.loaded, agent.tool_call, payment.executed"
            />
            <LaneColumn
              letter="B"
              title="Countersign agent"
              description="Plans over handles, not text. Every money-moving value must trace to a trusted record."
              events="SSE lane=countersign · fields.extracted, plan.proposed, policy.decision, payment.held"
            />
          </div>
        </section>

        <aside aria-label="Ledger balances" className="flex min-w-0 flex-col gap-4">
          <Panel dense eyebrow="Bank" title="Ledger balances">
            {[
              { role: 'company', label: 'Fogline Festival · operating' },
              { role: 'vendor', label: 'Vendors on file' },
              { role: 'attacker', label: 'Account not on file' },
            ].map((a) => (
              <div
                key={a.role}
                className="grid grid-cols-[minmax(0,1fr)_5.5rem] border-b border-rule-strong last:border-b-0"
              >
                <div className="min-w-0 py-2 pr-2">
                  <p className="smallcaps m-0 text-ink-soft">{a.role}</p>
                  <span className="block truncate text-sm text-ink">{a.label}</span>
                </div>
                {/* The ledger's amount column, ruled off on the left. */}
                <span className="flex items-end justify-end border-l border-rule-strong py-2 font-mono tabular text-lg text-ink-soft">
                  —
                </span>
              </div>
            ))}
            <Placeholder title="Balances and transfers this session" source="GET /api/balances" className="mt-3" />
          </Panel>
        </aside>
      </div>
    </div>
  )
}
