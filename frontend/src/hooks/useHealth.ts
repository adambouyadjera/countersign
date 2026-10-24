import { useQuery } from '@tanstack/react-query'
import { getHealth } from '@/api/client'

export const HEALTH_POLL_MS = 15_000

/** GET /api/health, polled every 15 s. Shared so every view that shows a service's data can label it. */
export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: HEALTH_POLL_MS })
}
