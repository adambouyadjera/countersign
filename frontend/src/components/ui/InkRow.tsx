import type { ComponentPropsWithoutRef, ElementType, ReactNode } from 'react'
import { cn } from '@/lib/cn'

export type InkRowTone = 'default' | 'untrusted' | 'failed' | 'selected' | 'good-fill' | 'bad-fill'

type InkRowProps<T extends ElementType> = {
  as?: T
  /**
   * default: inner paper (#FBFAF6), ink outline, soft shadow (journal entries, steps, check rows).
   * untrusted: dashed red on pale red. failed: 2px solid red on pale red (a failed check).
   * selected: cobalt outline and shadow (the picked inbox row).
   * good-fill / bad-fill: solid verdict tiles with a hard ink shadow (the console's outcome tiles).
   */
  tone?: InkRowTone
  className?: string
  children?: ReactNode
} & Omit<ComponentPropsWithoutRef<T>, 'as' | 'className' | 'children'>

const TONE: Record<InkRowTone, string> = {
  default: 'border-[1.5px] border-solid border-ink bg-inner shadow-[var(--inner-shadow)]',
  untrusted: 'border-[1.5px] border-dashed border-bad bg-bad-bg',
  failed: 'border-2 border-solid border-bad bg-bad-bg',
  selected: 'border-[1.5px] border-solid border-accent bg-bubble shadow-[2px_2px_0_var(--accent)]',
  'good-fill': 'border-[1.5px] border-solid border-ink bg-good text-on-good shadow-[var(--button-shadow)]',
  'bad-fill': 'border-[1.5px] border-solid border-ink bg-bad text-on-bad shadow-[var(--button-shadow)]',
}

/** An inner item inside a card. */
export function InkRow<T extends ElementType = 'div'>({ as, tone = 'default', className, children, ...rest }: InkRowProps<T>) {
  const Tag: ElementType = as ?? 'div'
  return (
    <Tag data-ink-row={tone} className={cn('rounded-[6px]', TONE[tone], className)} {...rest}>
      {children}
    </Tag>
  )
}
