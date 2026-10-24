import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  eyebrow?: string
  title?: ReactNode
  aside?: ReactNode
  /** Tighter padding for dense rails. */
  dense?: boolean
  bodyClassName?: string
}

export function Panel({ eyebrow, title, aside, dense, className, bodyClassName, children, ...rest }: PanelProps) {
  const hasHeader = Boolean(eyebrow || title || aside)
  return (
    <section
      className={cn(
        // A ledger section, not a card: heavy ink head rule, hairline sides, no shadow.
        'relative min-w-0 border border-t-2 border-rule-strong border-t-ink bg-paper',
        className,
      )}
      {...rest}
    >
      {hasHeader && (
        <header
          className={cn(
            'flex items-end justify-between gap-3 border-b border-ink',
            dense ? 'px-3.5 pt-2.5 pb-2' : 'px-5 pt-4 pb-3',
          )}
        >
          <div className="min-w-0">
            {eyebrow && <p className="smallcaps m-0 text-ink-soft">{eyebrow}</p>}
            {title && (
              <h2 className={cn('display m-0 truncate pb-0.5 text-ink', dense ? 'text-[1.875rem]' : 'text-[2.25rem]')}>
                {title}
              </h2>
            )}
          </div>
          {aside && <div className="shrink-0">{aside}</div>}
        </header>
      )}
      <div className={cn(dense ? 'p-3' : 'p-5', bodyClassName)}>{children}</div>
    </section>
  )
}
