import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface ErrorStateProps {
  title: string
  /** Contract error code, e.g. "service_unavailable". */
  code?: string
  message?: ReactNode
  action?: ReactNode
  className?: string
}

export function ErrorState({ title, code, message, action, className }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        'flex gap-4 border border-taint/60 border-l-4 border-l-taint bg-[color-mix(in_srgb,var(--taint)_5%,var(--paper))] px-4 py-3',
        className,
      )}
    >
      <svg aria-hidden viewBox="0 0 20 20" className="mt-0.5 size-5 shrink-0 text-taint">
        <path d="M10 2 19 18H1Z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
        <path d="M10 8v4.5M10 14.6v.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <div className="min-w-0 flex-1">
        <p className="m-0 flex flex-wrap items-baseline gap-x-2 font-medium text-ink">
          {title}
          {code && <code className="font-mono text-[0.75rem] text-taint">{code}</code>}
        </p>
        {message && <div className="mt-1 text-sm text-ink-soft">{message}</div>}
        {action && <div className="mt-3">{action}</div>}
      </div>
    </div>
  )
}
