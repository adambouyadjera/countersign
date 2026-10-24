import { useQuery } from '@tanstack/react-query'
import { getVendor, listApprovals, listPeople } from '@/api/client'

/** GET /api/vendors/{id}. No retry: a 404 while the endpoint is unbuilt should show its placeholder at once. */
export function useVendor(vendorId: string | undefined) {
  return useQuery({
    queryKey: ['vendor', vendorId],
    queryFn: () => getVendor(vendorId!),
    enabled: Boolean(vendorId),
    retry: false,
  })
}

/** GET /api/people. People rarely change during a demo. */
export function usePeople() {
  return useQuery({ queryKey: ['people'], queryFn: listPeople, staleTime: Infinity, retry: false })
}

/** GET /api/approvals (all statuses). The console invalidates it when a hold or resolution event arrives. */
export function useApprovals() {
  return useQuery({ queryKey: ['approvals'], queryFn: () => listApprovals() })
}
