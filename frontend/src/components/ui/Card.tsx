import type { ComponentPropsWithoutRef, ElementType, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { CardLabel } from './CardLabel'

export type CardTone = 'default' | 'untrusted' | 'good'

type CardProps<T extends ElementType> = {
  as?: T
  /**
   * default: white paper, ink outline, folded corner, soft offset shadow.
   * untrusted: 1.5px dashed red on the pale red ground (dashed = untrusted, everywhere).
   * good: paper with a verdigris outline.
   */
  tone?: CardTone
  /** The dark tab at the top, e.g. "INBOX · 5". */
  label?: ReactNode
  /** The folded top-right corner. Paper tones only; untrusted cards are never folded. */
  foldedCorner?: boolean
  className?: string
  children?: ReactNode
} & Omit<ComponentPropsWithoutRef<T>, 'as' | 'className' | 'children'>

const TONE: Record<CardTone, string> = {
  default: 'border-[1.5px] border-solid border-ink bg-card shadow-[var(--card-shadow)]',
  untrusted: 'border-[1.5px] border-dashed border-bad bg-bad-bg shadow-[var(--untrusted-shadow)]',
  good: 'border-[1.5px] border-solid border-good bg-card shadow-[3px_3px_0_#cde5d6]',
}

/** The refined design's paper card. Use it for every box; never hand-roll one. */
export function Card<T extends ElementType = 'div'>({
  as,
  tone = 'default',
  label,
  foldedCorner = true,
  className,
  children,
  ...rest
}: CardProps<T>) {
  const Tag: ElementType = as ?? 'div'
  const folded = foldedCorner && tone !== 'untrusted'
  return (
    <Tag
      data-card={tone}
      data-folded={folded ? 'true' : undefined}
      className={cn('rounded-[var(--card-radius)]', TONE[tone], folded && 'bg-[image:var(--card-fold)]', className)}
      {...rest}
    >
      {label !== undefined && label !== null && label !== false && <CardLabel className="mb-2.5 self-start">{label}</CardLabel>}
      {children}
    </Tag>
  )
}
