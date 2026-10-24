import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { isApiError } from '@/api/errors'
import { RequestError } from '@/components/console/RequestError'
import { InkRow } from '@/components/ui'
import { cn } from '@/lib/cn'

/**
 * Renders a query's data, or says honestly why it can't: loading, "not available
 * yet" for a 404 (an endpoint the backend has not built), or the error with a retry.
 */
export function QueryView<T>({
  query,
  endpoint,
  what,
  children,
  className,
}: {
  query: UseQueryResult<T, Error>
  /** e.g. "GET /api/runs/{id}/invoices/{id}/graph", shown in the empty state. */
  endpoint: string
  what: string
  children: (data: T) => ReactNode
  /** Applied to the loading, "not available yet" and error states (e.g. to inset them in a flush card). */
  className?: string
}) {
  if (query.isPending) return <p className={cn('m-0 text-small text-muted', className)}>reading {what}…</p>
  if (query.isError) {
    if (isApiError(query.error) && query.error.status === 404) {
      return (
        <InkRow data-testid="not-available" className={cn('px-3.5 py-3 text-small', className)}>
          <p className="m-0 font-bold text-fg">{what} not available yet</p>
          <p className="m-0 font-mono text-meta text-muted">
            {endpoint} → 404 {query.error.code}
          </p>
        </InkRow>
      )
    }
    return (
      <RequestError
        className={className}
        title={`Could not read ${what}`}
        error={query.error}
        onRetry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    )
  }
  return <>{children(query.data)}</>
}
