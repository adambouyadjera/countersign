import { cn } from '@/lib/cn'

export type StatusTone = 'ok' | 'hold' | 'fault' | 'idle' | 'offline'

const fill: Record<StatusTone, string> = {
  ok: 'bg-verified',
  hold: 'bg-hold',
  fault: 'bg-taint',
  idle: 'bg-ink-soft/50',
  offline: 'bg-transparent border border-ink-soft border-dashed',
}

export interface StatusDotProps {
  tone: StatusTone
  label: string
  /** Show the label next to the dot; otherwise it is screen-reader only. */
  showLabel?: boolean
  pulse?: boolean
  className?: string
}

export function StatusDot({ tone, label, showLabel = false, pulse = false, className }: StatusDotProps) {
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)} data-tone={tone}>
      <span className="relative inline-flex size-2">
        {pulse && (
          <span
            aria-hidden
            className={cn('absolute inset-0 rounded-full opacity-60 motion-safe:animate-ping', fill[tone])}
          />
        )}
        <span aria-hidden className={cn('relative inline-block size-2 rounded-full', fill[tone])} />
      </span>
      <span className={showLabel ? 'text-[0.8125rem] text-ink-soft' : 'sr-only'}>{label}</span>
    </span>
  )
}
