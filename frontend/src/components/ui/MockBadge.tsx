import { isMockMode } from '@/lib/env'
import { cn } from '@/lib/cn'

export interface MockBadgeProps {
  /** Defaults to the VITE_USE_MOCK flag. */
  enabled?: boolean
  className?: string
}

export function MockBadge({ enabled, className }: MockBadgeProps) {
  const on = enabled ?? isMockMode()
  if (!on) return null
  return (
    <span
      role="status"
      title="Replaying contracts/fixtures. Nothing on screen comes from the backend."
      className={cn(
        'inline-flex h-6 -rotate-2 items-center gap-1.5 border-2 border-double border-taint px-2',
        'font-mono text-[0.6875rem] font-medium tracking-[0.18em] text-taint',
        className,
      )}
    >
      MOCK DATA
    </span>
  )
}
