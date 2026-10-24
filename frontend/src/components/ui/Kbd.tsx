import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-5 items-center justify-center rounded-[4px] border border-current/40 px-1',
        'font-mono text-meta leading-none',
        className,
      )}
    >
      {children}
    </kbd>
  )
}
