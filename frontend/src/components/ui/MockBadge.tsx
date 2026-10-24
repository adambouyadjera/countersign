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
        'inline-flex h-6 items-center rounded-[6px] border border-bad-line bg-bad-bg px-2',
        'font-mono text-meta font-bold tracking-[0.12em] text-bad',
        className,
      )}
    >
      MOCK DATA
    </span>
  )
}
