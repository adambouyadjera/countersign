import { InkButton, InkRow } from '@/components/ui'
import { isApiError } from '@/api/errors'
import { cn } from '@/lib/cn'

export interface RequestErrorProps {
  title: string
  error: unknown
  onRetry?: () => void
  retrying?: boolean
  className?: string
}

/** A failed request: the contract error code and message, and a retry button. */
export function RequestError({ title, error, onRetry, retrying, className }: RequestErrorProps) {
  const code = isApiError(error) ? error.code : undefined
  const message = error instanceof Error ? error.message : String(error)
  return (
    <InkRow tone="failed" role="alert" className={cn('flex flex-col gap-1.5 px-3.5 py-2.5', className)}>
      <p className="m-0 font-bold text-bad">
        {title}
        {code && <code className="ml-2 font-mono text-meta font-normal">{code}</code>}
      </p>
      <p className="m-0 text-small text-fg">{message}</p>
      {onRetry && (
        <InkButton size="sm" variant="secondary" className="self-start" onClick={onRetry} disabled={retrying}>
          {retrying ? 'retrying…' : 'retry'}
        </InkButton>
      )}
    </InkRow>
  )
}
