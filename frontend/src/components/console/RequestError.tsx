import { Button, ErrorState } from '@/components/ui'
import { isApiError } from '@/api/errors'

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
    <ErrorState
      title={title}
      code={code}
      message={message}
      className={className}
      action={
        onRetry && (
          <Button size="sm" variant="quiet" onClick={onRetry} disabled={retrying}>
            {retrying ? 'Retrying…' : 'Retry'}
          </Button>
        )
      }
    />
  )
}
