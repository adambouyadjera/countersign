import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'
import type { SigExpression } from './face'
import { SigFace } from './SigFace'

export interface SigSaysProps {
  expression?: SigExpression
  /** One short first-person line. Real text, read by screen readers. */
  children: ReactNode
  className?: string
}

/**
 * Sig's face, the "sig" label and one short line, in the cobalt speech bubble.
 * The bubble's bottom-left corner is squared off: that's the corner pointing at Sig.
 */
export function SigSays({ expression = 'default', children, className }: SigSaysProps) {
  return (
    <div
      data-sig-says
      className={cn(
        'flex items-start gap-3 bg-bubble px-4 py-3.5 [border-radius:var(--bubble-radius)] [border:var(--bubble-border)] shadow-[var(--bubble-shadow)]',
        className,
      )}
    >
      <SigFace expression={expression} />
      <div className="min-w-0">
        <div className="font-mono text-meta font-bold text-accent">sig</div>
        <div className="font-prose leading-normal text-fg">{children}</div>
      </div>
    </div>
  )
}
