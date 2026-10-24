import type { ReactNode } from 'react'
import { SigSays } from './SigSays'
import type { SigExpression } from './face'

export interface SigPlaceholderProps {
  title: string
  expression: SigExpression
  /** What the page will show once it is built. Real text, not sample data. */
  what: string
  children?: ReactNode
}

/** A page that is not built yet: Sig says so. No sample content. */
export function SigPlaceholder({ title, expression, what, children }: SigPlaceholderProps) {
  return (
    <div className="flex w-full max-w-[52rem] flex-col gap-5 px-8 py-7">
      <h1 className="m-0 font-prose text-big font-extrabold">{title}</h1>
      <SigSays expression={expression}>Not built yet. {what}</SigSays>
      {children}
    </div>
  )
}
