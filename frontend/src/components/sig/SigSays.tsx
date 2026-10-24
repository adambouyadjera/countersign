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

/** Sig's face, the "sig" label and one short line, in a white card (as in the mockups). */
export function SigSays({ expression = 'default', children, className }: SigSaysProps) {
  return (
    <div
      className={cn('flex items-start gap-3 rounded-[12px] border border-line bg-card px-4 py-3.5', className)}
    >
      <SigFace expression={expression} />
      <div className="min-w-0">
        <div className="font-mono text-meta font-bold text-accent">sig</div>
        <div className="font-prose leading-normal text-fg">{children}</div>
      </div>
    </div>
  )
}
