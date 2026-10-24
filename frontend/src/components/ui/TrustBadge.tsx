import { cn } from '@/lib/cn'

export type TrustLevel = 'trusted' | 'untrusted' | 'corroborated' | 'human-approved'

const text: Record<TrustLevel, string> = {
  trusted: 'Trusted',
  untrusted: 'Untrusted',
  corroborated: 'Corroborated',
  'human-approved': 'Human-approved',
}

const tone: Record<TrustLevel, string> = {
  trusted: 'border-ink text-ink',
  untrusted: 'border-taint text-taint border-dashed',
  corroborated: 'border-verified text-verified',
  'human-approved': 'border-hold text-[#8a5a12] bg-[color-mix(in_srgb,var(--hold)_10%,var(--paper))]',
}

export function TrustBadge({ level, className }: { level: TrustLevel; className?: string }) {
  return (
    <span
      data-level={level}
      className={cn(
        'smallcaps inline-flex h-5 items-center gap-1 rounded-[2px] border px-1.5 leading-none whitespace-nowrap',
        tone[level],
        className,
      )}
    >
      {level === 'human-approved' && (
        <svg aria-hidden viewBox="0 0 10 10" className="size-2">
          <circle cx="5" cy="5" r="4" fill="currentColor" />
        </svg>
      )}
      {text[level]}
    </span>
  )
}
