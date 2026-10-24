import type { ReactNode } from 'react'
import type { InvoiceSummary, SourceSpan } from '@/api/types'
import { cn } from '@/lib/cn'
import { formatCentsShort } from '@/lib/money'
import type { LaneOutcome } from '@/lib/outcomes'

export interface OneSentencePanelProps {
  invoice: InvoiceSummary | undefined
  /** instruction_like_spans from fields.extracted; undefined until Countersign has read the invoice. */
  spans: SourceSpan[] | undefined
  unguarded: LaneOutcome
  countersign: LaneOutcome
  /** Account ids GET /api/balances marks as role "attacker". */
  attackerIds: ReadonlySet<string>
  children?: ReactNode
}

function outcomeBox(o: LaneOutcome, attackerIds: ReadonlySet<string>): { text: string; tone: 'good' | 'bad' | 'plain' | 'quiet' } {
  const amount = o.amountCents !== undefined ? formatCentsShort(o.amountCents) : ''
  switch (o.kind) {
    case 'paid_off_file':
      return {
        text: o.toAccountId && attackerIds.has(o.toAccountId) ? `${amount} to attacker` : `${amount} to an account not on file`,
        tone: 'bad',
      }
    case 'paid_on_file':
      return { text: `${amount} paid on file`, tone: 'good' }
    case 'held':
      return { text: '$0 left · held', tone: 'good' }
    case 'blocked':
      return { text: '$0 left · blocked', tone: 'good' }
    case 'failed':
      return { text: 'failed', tone: 'bad' }
    case 'not_running':
      return { text: 'not running', tone: 'quiet' }
    case 'in_progress':
      return { text: 'working…', tone: 'plain' }
    case 'waiting':
      return { text: 'waiting', tone: 'quiet' }
  }
}

const BOX_TONE = {
  good: 'bg-good text-on-good',
  bad: 'bg-bad text-on-bad',
  plain: 'border border-line text-fg',
  quiet: 'border border-dashed border-line text-muted font-semibold',
} as const

function LaneSide({ name, outcome, attackerIds }: { name: string; outcome: LaneOutcome; attackerIds: ReadonlySet<string> }) {
  const box = outcomeBox(outcome, attackerIds)
  return (
    <div className="flex min-w-0 flex-col gap-2" data-lane-outcome={name.toLowerCase()} data-kind={outcome.kind}>
      <div className="font-extrabold">{name}</div>
      <div className="min-h-[1.55em] text-small text-muted">{outcome.words.join(' → ') || '—'}</div>
      <div className={cn('rounded-[10px] p-3 font-extrabold', BOX_TONE[box.tone])}>{box.text}</div>
    </div>
  )
}

export function OneSentencePanel({ invoice, spans, unguarded, countersign, attackerIds, children }: OneSentencePanelProps) {
  const first = spans?.[0]
  return (
    <aside
      aria-label="One sentence, two agents"
      className="flex min-h-0 flex-col gap-3.5 overflow-y-auto border-l border-line px-6 py-6 font-prose"
    >
      <div className="eyebrow">one sentence, two agents</div>
      {first ? (
        <figure className="m-0 rounded-[10px] border border-line px-3.5 py-3 font-mono text-small text-bad">
          <blockquote className="m-0">“{first.quote}”</blockquote>
          <figcaption className="mt-1 text-meta text-muted">
            {invoice?.invoice_number} · line {first.line}
            {spans.length > 1 && ` · +${spans.length - 1} more`}
          </figcaption>
        </figure>
      ) : (
        <p className="m-0 rounded-[10px] border border-dashed border-line px-3.5 py-3 font-mono text-small text-muted">
          {!invoice
            ? 'No invoice selected.'
            : spans
              ? `No instruction-like text in ${invoice.invoice_number}.`
              : `Countersign hasn't read ${invoice.invoice_number} yet.`}
        </p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <LaneSide name="Unguarded" outcome={unguarded} attackerIds={attackerIds} />
        <LaneSide name="Countersign" outcome={countersign} attackerIds={attackerIds} />
      </div>
      {children}
    </aside>
  )
}
