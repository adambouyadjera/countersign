import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface PageHeaderProps {
  /** Roman folio shown in the margin, e.g. "I". */
  folio: string
  title: string
  /** One-line italic subtitle under or beside the title. */
  dek?: ReactNode
  /** Right-hand slot for page actions. */
  aside?: ReactNode
  /** Tighter version for screens that must fit 1280x720 without scrolling. */
  compact?: boolean
  className?: string
}

/**
 * A blind emboss: the notary's seal pressed into the paper with no ink. Purely
 * decorative; it never stands for a status (the amber Seal does that).
 */
function BlindSeal({ size }: { size: number }) {
  const teeth = 48
  const pts: string[] = []
  for (let i = 0; i < teeth * 2; i++) {
    const r = i % 2 === 0 ? 48 : 45.5
    const a = (Math.PI * i) / teeth
    pts.push(`${(50 + r * Math.cos(a)).toFixed(2)},${(50 + r * Math.sin(a)).toFixed(2)}`)
  }
  const shape = (
    <>
      <polygon points={pts.join(' ')} strokeWidth="0.9" />
      <circle cx="50" cy="50" r="40" strokeWidth="1.6" />
      <circle cx="50" cy="50" r="30" strokeWidth="0.7" />
      <circle cx="44" cy="50" r="11" strokeWidth="1.8" />
      <circle cx="56" cy="50" r="11" strokeWidth="1.8" />
      <path d="M34 66h32" strokeWidth="0.9" />
    </>
  )
  return (
    <svg
      aria-hidden
      data-blind-seal
      viewBox="0 0 100 100"
      width={size}
      height={size}
      className="pointer-events-none shrink-0 -rotate-6"
      fill="none"
    >
      {/* Highlight below-right, shadow above-left: reads as pressed in. */}
      <g stroke="rgba(255,255,255,0.75)" transform="translate(0.6 0.6)">
        {shape}
      </g>
      <g stroke="color-mix(in srgb, var(--ink) 24%, var(--paper))">{shape}</g>
    </svg>
  )
}

export function PageHeader({ folio, title, dek, aside, compact = false, className }: PageHeaderProps) {
  return (
    <header
      className={cn(
        'relative flex items-end gap-6 border-b-[3px] border-double border-ink',
        compact ? 'pt-3 pb-2.5' : 'pt-6 pb-4',
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          'display shrink-0 self-stretch border-r border-rule-strong pr-5 text-ink-soft italic',
          compact ? 'text-[2.25rem]' : 'text-[3.25rem]',
          'flex items-end',
        )}
      >
        {folio}
      </span>
      <div className="min-w-0 flex-1">
        <h1 className={cn('display m-0 text-ink', compact ? 'text-[2.75rem]' : 'text-[clamp(3rem,4.6vw,4.75rem)]')}>
          {title}
        </h1>
        {dek && (
          <p
            className={cn(
              'm-0 font-display text-ink-soft italic',
              compact ? 'mt-1 text-[1.125rem] leading-snug' : 'mt-2 text-[1.375rem] leading-snug',
            )}
          >
            {dek}
          </p>
        )}
      </div>
      {aside && <div className="shrink-0 self-end">{aside}</div>}
      <BlindSeal size={compact ? 72 : 112} />
    </header>
  )
}
