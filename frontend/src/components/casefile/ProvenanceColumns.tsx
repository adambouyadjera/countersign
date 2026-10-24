import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { splitHandle } from '@/lib/handle'
import type { CaseColumns, CaseNode } from '@/lib/caseGraph'

export interface ProvenanceColumnsProps {
  columns: CaseColumns
  /** Node picked by the user, and the nodes linked to it (from connectedIds). */
  selectedId?: string
  linked?: ReadonlySet<string>
  onSelect: (node: CaseNode | null) => void
  /** Caption under the handles: what the planner saw (from the leak guard). */
  handleCaption?: ReactNode
  invoiceNumber?: string
}

const HEAD = 'text-[0.786rem] tracking-[0.08em] text-muted uppercase'

const ACTION_WORD: Record<string, string> = {
  held: 'HELD',
  paid: 'PAID',
  blocked: 'BLOCKED',
  approved: 'APPROVED',
  rejected: 'REJECTED',
}

function NodeButton({
  node,
  selected,
  dim,
  onSelect,
  className,
  children,
}: {
  node: CaseNode
  selected: boolean
  dim: boolean
  onSelect: ProvenanceColumnsProps['onSelect']
  className: string
  children: ReactNode
}) {
  return (
    <button
      type="button"
      data-node={node.id}
      data-trust={node.untrusted ? 'untrusted' : 'trusted'}
      aria-pressed={selected}
      onClick={() => onSelect(selected ? null : node)}
      className={cn(
        'w-full cursor-pointer text-left font-[inherit] text-fg transition-opacity',
        className,
        dim && 'opacity-35',
        selected && 'ring-2 ring-accent ring-offset-2 ring-offset-card',
      )}
    >
      {children}
    </button>
  )
}

export function ProvenanceColumns({
  columns,
  selectedId,
  linked,
  onSelect,
  handleCaption,
  invoiceNumber,
}: ProvenanceColumnsProps) {
  const dim = (id: string) => Boolean(selectedId && linked && !linked.has(id))
  const common = (n: CaseNode) => ({ node: n, selected: n.id === selectedId, dim: dim(n.id), onSelect })

  return (
    <div className="grid flex-1 grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)_minmax(0,1.15fr)_minmax(0,1fr)] items-start gap-3.5">
      <div className="flex flex-col gap-3" role="group" aria-label="Source">
        <div className={HEAD}>source</div>
        {columns.source.map((n) => (
          <NodeButton
            key={n.id}
            {...common(n)}
            className={cn(
              'rounded-[8px] p-2.5',
              n.untrusted ? 'border-[1.5px] border-dashed border-bad bg-bad-bg' : 'border-[1.5px] border-solid border-fg bg-card',
            )}
          >
            <div className="font-bold" title={n.span ? `“${n.span.quote}”` : undefined}>
              {n.title}
            </div>
            {n.subtitle && <div className="text-meta text-muted">{n.subtitle}</div>}
          </NodeButton>
        ))}
      </div>

      <div className="flex flex-col gap-3" role="group" aria-label="Handle">
        <div className={HEAD}>handle</div>
        {columns.handle.map((n) => (
          <NodeButton
            key={n.id}
            {...common(n)}
            className={cn(
              'rounded-[8px] bg-card p-2.5 font-mono text-meta',
              n.untrusted ? 'border-[1.5px] border-dashed border-bad' : 'border-[1.5px] border-solid border-fg',
            )}
          >
            {/* Invoice number + field; the full handle is in the tooltip. */}
            <div className={cn('break-all', n.untrusted ? 'text-claim' : 'text-fg')} title={n.title}>
              <span className="opacity-80">{invoiceNumber ? `${invoiceNumber} · ` : splitHandle(n.title)[0]}</span>
              <span className="font-medium">{splitHandle(n.title)[1]}</span>
            </div>
            {n.subtitle && <div className="font-prose text-muted">{n.subtitle}</div>}
          </NodeButton>
        ))}
        {handleCaption && <div className="text-meta text-muted">{handleCaption}</div>}
      </div>

      <div className="flex flex-col gap-2.5" role="group" aria-label="Checks">
        <div className={HEAD}>checks</div>
        {columns.check.map((n) => {
          const failed = n.status === 'failed'
          return (
            <NodeButton
              key={n.id}
              {...common(n)}
              className={cn(
                'rounded-[6px] px-2 py-1.5 font-mono text-meta break-all',
                failed ? 'border-2 border-bad bg-bad-bg' : 'border border-line bg-card',
              )}
            >
              <span title={n.message}>
                {n.title}{' '}
                {failed ? <span className="text-bad">✗</span> : n.status === 'passed' ? <span className="text-good">✓</span> : null}
                <span className="sr-only"> {n.status}</span>
              </span>
            </NodeButton>
          )
        })}
      </div>

      <div className="flex flex-col gap-3" role="group" aria-label="Action">
        <div className={HEAD}>action</div>
        {columns.action.map((n) => {
          const good = n.status === 'paid' || n.status === 'approved'
          const bad = n.status === 'held' || n.status === 'blocked'
          return (
            <NodeButton
              key={n.id}
              {...common(n)}
              className={cn(
                'rounded-[10px] bg-card p-3.5 text-center',
                good ? 'border-2 border-good' : bad ? 'border-2 border-bad' : 'border-2 border-fg',
              )}
            >
              <div className={cn('text-[1.429rem] font-extrabold', good ? 'text-good' : bad ? 'text-bad' : 'text-fg')}>
                {ACTION_WORD[n.status ?? ''] ?? n.status ?? '—'}
              </div>
              <div className="text-meta [overflow-wrap:anywhere] text-muted">{n.title}</div>
            </NodeButton>
          )
        })}
      </div>
    </div>
  )
}
