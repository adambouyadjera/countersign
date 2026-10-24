import { cn } from '@/lib/cn'

export interface SigLogoProps {
  /** Show the "countersign" wordmark next to the mark. */
  wordmark?: boolean
  className?: string
}

/** Sig's line-art mark (rounded head, two eyes, two feet) and the wordmark, as in every mockup header. */
export function SigLogo({ wordmark = true, className }: SigLogoProps) {
  return (
    <span className={cn('inline-flex items-center gap-2 font-mono font-bold text-[var(--logo)]', className)}>
      <svg aria-hidden="true" width="26" height="26" viewBox="0 0 26 26" className="shrink-0">
        <rect x="3" y="2" width="20" height="15" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="9.5" cy="9.5" r="2" fill="currentColor" />
        <circle cx="16.5" cy="9.5" r="2" fill="currentColor" />
        <path
          d="M9 17v4M17 17v4M6 23h6M14 23h6"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
      {wordmark && <span>countersign</span>}
    </span>
  )
}
