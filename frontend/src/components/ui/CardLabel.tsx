import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/** The dark label tab on a card: Space Mono 10.5px, light on ink. */
export function CardLabel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      data-card-label
      className={cn(
        'inline-block w-fit rounded-[3px] bg-[var(--tab-bg)] px-2 py-[2px] font-mono text-[0.75rem] leading-normal tracking-[0.06em] text-[var(--tab-fg)] uppercase',
        className,
      )}
    >
      {children}
    </span>
  )
}
