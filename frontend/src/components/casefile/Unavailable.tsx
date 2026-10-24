import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import { isApiError } from '@/api/errors'
import { RequestError } from '@/components/console/RequestError'

/**
 * Renders a query's data, or says honestly why it can't: loading, "not available
 * yet" for a 404 (an endpoint the backend has not built), or the error with a retry.
 */
export function QueryView<T>({
  query,
  endpoint,
  what,
  children,
}: {
  query: UseQueryResult<T, Error>
  /** e.g. "GET /api/runs/{id}/invoices/{id}/graph", shown in the empty state. */
  endpoint: string
  what: string
  children: (data: T) => ReactNode
}) {
  if (query.isPending) return <p className="m-0 text-small text-muted">reading {what}…</p>
  if (query.isError) {
    if (isApiError(query.error) && query.error.status === 404) {
      return (
        <div data-testid="not-available" className="rounded-[8px] border border-dashed border-line px-3.5 py-3 text-small">
          <p className="m-0 font-bold text-fg">{what} not available yet</p>
          <p className="m-0 font-mono text-meta text-muted">
            {endpoint} → 404 {query.error.code}
          </p>
        </div>
      )
    }
    return (
      <RequestError
        title={`Could not read ${what}`}
        error={query.error}
        onRetry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    )
  }
  return <>{children(query.data)}</>
}
