import { useQuery } from '@tanstack/react-query'
import { getAutopilot } from '@/api/client'
import { isApiError } from '@/api/errors'

/** "autopilot on/off" from GET /api/autopilot. Says "unavailable" rather than guessing when the call fails. */
export function AutopilotStatus() {
  const q = useQuery({ queryKey: ['autopilot'], queryFn: getAutopilot, refetchInterval: 15_000 })
  if (q.isPending) return <span className="text-muted">autopilot …</span>
  if (q.isError) {
    return (
      <span className="text-muted" title={`GET /api/autopilot failed${isApiError(q.error) ? `: ${q.error.code}` : ''}`}>
        autopilot unavailable
      </span>
    )
  }
  return (
    <span className="text-muted" data-testid="autopilot">
      autopilot{' '}
      {q.data.enabled ? <span className="text-good">on</span> : <span className="text-muted">off</span>}
    </span>
  )
}
