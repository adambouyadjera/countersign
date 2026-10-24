import { useState } from 'react'
import { lastFour, maskAccount } from '@/lib/account'
import { cn } from '@/lib/cn'

export interface AccountNumberProps {
  value: string
  /** Disable reveal (e.g. when the full number was never sent). */
  revealable?: boolean
  className?: string
}

export function AccountNumber({ value, revealable = true, className }: AccountNumberProps) {
  const [revealed, setRevealed] = useState(false)
  const show = revealable && revealed
  const last = lastFour(value)
  return (
    <span
      tabIndex={revealable ? 0 : undefined}
      onMouseEnter={() => setRevealed(true)}
      onMouseLeave={() => setRevealed(false)}
      onFocus={() => setRevealed(true)}
      onBlur={() => setRevealed(false)}
      aria-label={`Account ending ${last || 'unknown'}`}
      data-revealed={show ? 'true' : 'false'}
      className={cn(
        'inline-block font-mono tabular whitespace-nowrap tracking-[0.02em]',
        revealable && 'cursor-default border-b border-dotted border-muted/60',
        className,
      )}
    >
      <span aria-hidden>{show ? value : maskAccount(value)}</span>
    </span>
  )
}
