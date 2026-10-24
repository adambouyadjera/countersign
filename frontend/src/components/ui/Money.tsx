import { useEffect, useRef, useState } from 'react'
import { animate, useReducedMotion } from 'framer-motion'
import { cn } from '@/lib/cn'
import { formatCents, type Currency } from '@/lib/money'

export interface MoneyProps {
  cents: number
  currency?: Currency
  /** Tween between old and new values when `cents` changes. Only the prop change drives it. */
  flow?: boolean
  /** Colour a signed amount: negatives in vermilion. */
  signed?: boolean
  className?: string
}

type Tween = { value: number; direction: 'up' | 'down' }

export function Money({ cents, currency = 'USD', flow = false, signed = false, className }: MoneyProps) {
  const reduce = useReducedMotion()
  const animated = flow && !reduce
  const [tween, setTween] = useState<Tween | null>(null)
  // Last value painted, so an interrupted tween restarts from where it visibly is.
  const painted = useRef(cents)
  const shown = animated && tween ? tween.value : cents

  useEffect(() => {
    const from = painted.current
    if (!animated || from === cents) {
      painted.current = cents
      return
    }
    const direction = cents > from ? 'up' : 'down'
    const controls = animate(from, cents, {
      duration: 0.9,
      ease: [0.2, 0.7, 0.2, 1],
      onUpdate: (v) => {
        painted.current = Math.round(v)
        setTween({ value: painted.current, direction })
      },
      onComplete: () => {
        painted.current = cents
        setTween(null)
      },
    })
    return () => controls.stop()
  }, [cents, animated])

  const direction = animated && tween ? tween.direction : undefined
  return (
    <span
      data-flowing={direction}
      aria-label={formatCents(cents, currency)}
      className={cn(
        'font-mono tabular whitespace-nowrap transition-colors duration-300',
        signed && cents < 0 && 'text-taint',
        direction === 'up' && 'text-verified',
        direction === 'down' && 'text-taint',
        className,
      )}
    >
      <span aria-hidden>{formatCents(shown, currency)}</span>
    </span>
  )
}
