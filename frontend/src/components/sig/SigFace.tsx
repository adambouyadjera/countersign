import { cn } from '@/lib/cn'
import { sigFaceLines, type SigExpression } from './face'

export interface SigFaceProps {
  expression?: SigExpression
  /**
   * small: the three-line glyph in Sig's speech cards.
   * large: the console's four-line glyph with feet.
   */
  size?: 'small' | 'large'
  className?: string
}

/**
 * Sig's terminal face. Decorative: aria-hidden, so put the meaning in real text next to it.
 * Each character sits in a 1ch cell, so the box stays aligned even when a glyph
 * falls back to another font (the web fonts have no box-drawing characters).
 */
export function SigFace({ expression = 'default', size = 'small', className }: SigFaceProps) {
  const lines = sigFaceLines(expression, size)
  return (
    <div
      aria-hidden="true"
      data-expression={expression}
      className={cn(
        'shrink-0 font-mono font-bold whitespace-pre text-accent select-none',
        size === 'large' ? 'text-face leading-[1.15]' : 'text-small leading-[1.1]',
        className,
      )}
    >
      {lines.map((line, i) => (
        <div key={i} className="flex">
          {[...line].map((ch, j) => (
            <span key={j} className="inline-block w-[1ch] text-center">
              {ch}
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}
