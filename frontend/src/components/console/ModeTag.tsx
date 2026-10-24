import type { Mode } from '@/api/types'
import { cn } from '@/lib/cn'

/** Labels data produced by an offline adapter or a dry-run bank. Renders nothing for live. */
export function ModeTag({ mode, service, className }: { mode?: Mode; service?: string; className?: string }) {
  if (!mode || mode === 'live') return null
  const text = mode === 'offline' ? 'offline mode' : 'dry run'
  return (
    <span
      data-mode={mode}
      title={service ? `${service}: ${text}` : text}
      className={cn(
        'inline-flex items-center rounded-[4px] border border-dashed border-muted px-1.5',
        'font-mono text-meta leading-5 whitespace-nowrap text-muted',
        className,
      )}
    >
      {service ? `${service} · ${text}` : text}
    </span>
  )
}
