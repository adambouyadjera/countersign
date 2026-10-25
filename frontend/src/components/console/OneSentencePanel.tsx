import type { ReactNode } from 'react'
import type { InvoiceSummary, SourceSpan } from '@/api/types'
import { Card, InkRow, type InkRowTone } from '@/components/ui'
import { cn } from '@/lib/cn'
import { formatCentsShort } from '@/lib/money'
import { meaningfulSpans, type LaneOutcome } from '@/lib/outcomes'

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

type BoxTone = 'good' | 'bad' | 'warn' | 'plain' | 'quiet'

function outcomeBox(
  o: LaneOutcome,
  attackerIds: ReadonlySet<string>,
  /** For the unguarded side: what Countersign did with the same invoice. */
  other?: LaneOutcome,
): { text: string; tone: BoxTone } {
  const amount = o.amountCents !== undefined ? formatCentsShort(o.amountCents) : ''
  // Paid to the account on file, but Countersign stopped this invoice: not a clean result.
  if (o.kind === 'paid_on_file' && other && (other.kind === 'blocked' || other.kind === 'held')) {
    const why = other.reasonCodes?.length ? ` (${other.reasonCodes.join(', ')})` : ''
    return { text: `${amount} paid, but Countersign ${other.kind} it${why}`, tone: 'warn' }
  }
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

// Verdicts are solid tiles on a hard ink shadow; unfinished states are plain inner rows.
const BOX_TONE: Record<BoxTone, { tone: InkRowTone; text: string }> = {
  good: { tone: 'good-fill', text: '' },
  bad: { tone: 'bad-fill', text: '' },
  // Amber, like the held seal: dark ink on a light amber fill (about 12:1).
  warn: { tone: 'default', text: '!border-ink !bg-[#f6dfa4] text-fg shadow-[var(--button-shadow)]' },
  plain: { tone: 'default', text: 'text-fg' },
  quiet: { tone: 'default', text: 'text-muted font-semibold' },
}

function LaneSide({
  name,
  outcome,
  attackerIds,
  other,
}: {
  name: string
  outcome: LaneOutcome
  attackerIds: ReadonlySet<string>
  other?: LaneOutcome
}) {
  const box = outcomeBox(outcome, attackerIds, other)
  return (
    <div className="flex min-w-0 flex-col gap-2" data-lane-outcome={name.toLowerCase()} data-kind={outcome.kind}>
      <div className="font-extrabold">{name}</div>
      <div className="min-h-[1.55em] text-small text-muted">{outcome.words.join(' → ') || '—'}</div>
      <InkRow
        tone={BOX_TONE[box.tone].tone}
        data-tone={box.tone}
        className={cn('rounded-[8px] p-3 font-extrabold', BOX_TONE[box.tone].text)}
      >
        {box.text}
      </InkRow>
    </div>
  )
}

export function OneSentencePanel({ invoice, spans, unguarded, countersign, attackerIds, children }: OneSentencePanelProps) {
  // Never show a span that is only the blanked reference marker.
  const real = spans ? meaningfulSpans(spans) : undefined
  const first = real?.[0]
  return (
    <Card
      as="aside"
      aria-label="One sentence, two agents"
      label="one sentence, two agents"
      className="my-[18px] mr-[18px] flex min-h-0 flex-col gap-3.5 overflow-y-auto px-5 py-[18px] font-prose"
    >
      {first ? (
        <Card as="figure" tone="untrusted" className="m-0 px-3.5 py-3 font-mono text-small text-bad">
          <blockquote className="m-0">“{first.quote}”</blockquote>
          <figcaption className="mt-1 text-meta text-muted">
            {invoice?.invoice_number} · line {first.line}
            {real!.length > 1 && ` · +${real!.length - 1} more`}
          </figcaption>
        </Card>
      ) : (
        <InkRow as="p" className="m-0 px-3.5 py-3 font-mono text-small text-muted">
          {!invoice
            ? 'No invoice selected.'
            : real
              ? `No instruction-like text found in ${invoice.invoice_number}.`
              : `Countersign hasn't read ${invoice.invoice_number} yet.`}
        </InkRow>
      )}
      <div className="grid grid-cols-2 gap-3">
        <LaneSide name="Unguarded" outcome={unguarded} attackerIds={attackerIds} other={countersign} />
        <LaneSide name="Countersign" outcome={countersign} attackerIds={attackerIds} />
      </div>
      {children}
    </Card>
  )
}
