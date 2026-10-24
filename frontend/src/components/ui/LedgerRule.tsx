import { cn } from '@/lib/cn'

export interface LedgerRuleProps {
  /** single: hairline. double: accountant's double rule, used under totals. */
  variant?: 'single' | 'double' | 'heavy'
  label?: string
  className?: string
}

export function LedgerRule({ variant = 'single', label, className }: LedgerRuleProps) {
  const line =
    variant === 'double'
      ? 'h-[4px] border-y border-ink'
      : variant === 'heavy'
        ? 'h-0 border-t-2 border-ink'
        : 'h-0 border-t border-rule-strong'
  if (!label) return <hr className={cn('m-0 w-full border-0', line, className)} />
  return (
    <div role="separator" aria-label={label} className={cn('flex items-center gap-3', className)}>
      <span className="smallcaps shrink-0 text-ink-soft">{label}</span>
      <span aria-hidden className={cn('flex-1', line)} />
    </div>
  )
}
