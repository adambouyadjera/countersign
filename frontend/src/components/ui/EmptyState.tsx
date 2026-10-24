import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface EmptyStateProps {
  title: string
  body?: ReactNode
  action?: ReactNode
  className?: string
}

export function EmptyState({ title, body, action, className }: EmptyStateProps) {
  return (
    <div className={cn('flex flex-col items-center gap-3 px-6 py-10 text-center', className)}>
      <svg aria-hidden viewBox="0 0 48 40" className="h-10 w-12 text-rule">
        {/* An empty ledger page: three blank rules and a margin line. */}
        <rect x="1" y="1" width="46" height="38" fill="none" stroke="currentColor" strokeWidth="1.2" />
        <path d="M10 1v38" stroke="var(--taint)" strokeOpacity=".45" strokeWidth="1" />
        <path d="M14 14h28M14 22h28M14 30h20" stroke="currentColor" strokeWidth="1.2" />
      </svg>
      <p className="m-0 font-display text-[1.375rem] leading-tight text-ink">{title}</p>
      {body && <div className="max-w-[36ch] text-sm text-ink-soft">{body}</div>}
      {action}
    </div>
  )
}
