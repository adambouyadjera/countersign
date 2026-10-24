import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface PlaceholderProps {
  title: string
  /** The contract endpoint or event that will fill this region. */
  source?: string
  children?: ReactNode
  className?: string
}

/** Marks a region that is intentionally empty until it is wired to the backend. */
export function Placeholder({ title, source, children, className }: PlaceholderProps) {
  return (
    <div
      data-placeholder
      className={cn('hatch relative border border-dashed border-ink-soft/70 px-4 pt-6 pb-4', className)}
    >
      <span className="smallcaps absolute top-0 left-3 -translate-y-1/2 bg-paper px-1.5 text-ink-soft">
        Placeholder
      </span>
      <p className="m-0 text-sm font-medium text-ink">{title}</p>
      {source && <p className="m-0 mt-1 font-mono text-[0.75rem] text-ink-soft">{source}</p>}
      {children && <div className="mt-3">{children}</div>}
    </div>
  )
}
