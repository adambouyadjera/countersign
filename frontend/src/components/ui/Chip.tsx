import type { ComponentPropsWithoutRef, ElementType, ReactNode } from 'react'
import { cn } from '@/lib/cn'
import { chipClass, type ChipTone } from './chipStyle'

type ChipProps<T extends ElementType> = {
  as?: T
  /** Fully rounded, e.g. the audit page's suggested questions. */
  pill?: boolean
  tone?: ChipTone
  className?: string
  children?: ReactNode
} & Omit<ComponentPropsWithoutRef<T>, 'as' | 'className' | 'children'>

/** A small inline item: 1.5px ink outline with a soft offset shadow. Pass as="button" for a clickable chip. */
export function Chip<T extends ElementType = 'span'>({ as, pill, tone, className, children, ...rest }: ChipProps<T>) {
  const Tag: ElementType = as ?? 'span'
  return (
    <Tag
      data-chip
      className={cn(chipClass({ pill, tone, button: Tag === 'button' }), className)}
      {...rest}
    >
      {children}
    </Tag>
  )
}
