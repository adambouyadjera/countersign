import { useQuery } from '@tanstack/react-query'
import {
  getAgentTranscript,
  getAutopilot,
  getBatch,
  getDocument,
  getInvoiceGraph,
  getPlannerTranscript,
  getRun,
  getReconciliation,
  getRunDecisions,
  getTrialBalance,
  getVendor,
  listApprovals,
  listInboxItems,
  listJournalEntries,
  listPeople,
} from '@/api/client'

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

// Case file reads. retry: false so a 404 from an endpoint the backend has not built
// shows its "not available yet" state at once instead of after retries.

export function useRun(runId: string | undefined) {
  return useQuery({ queryKey: ['run', runId], queryFn: () => getRun(runId!), enabled: Boolean(runId), retry: false })
}

export function useBatch(batchId: string | undefined) {
  return useQuery({
    queryKey: ['batch', batchId],
    queryFn: () => getBatch(batchId!),
    enabled: Boolean(batchId),
    retry: false,
  })
}

export function useDocument(documentId: string | undefined) {
  return useQuery({
    queryKey: ['document', documentId],
    queryFn: () => getDocument(documentId!),
    enabled: Boolean(documentId),
    retry: false,
  })
}

export function useGraph(runId: string, invoiceId: string) {
  return useQuery({ queryKey: ['graph', runId, invoiceId], queryFn: () => getInvoiceGraph(runId, invoiceId), retry: false })
}

export function useDecisions(runId: string) {
  return useQuery({ queryKey: ['decisions', runId], queryFn: () => getRunDecisions(runId), retry: false })
}

export function usePlannerTranscript(runId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['planner', runId],
    queryFn: () => getPlannerTranscript(runId!),
    enabled: Boolean(runId) && enabled,
    retry: false,
  })
}

export function useAgentTranscript(runId: string, invoiceId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['agent-transcript', runId, invoiceId],
    queryFn: () => getAgentTranscript(runId, invoiceId),
    enabled,
    retry: false,
  })
}

/** GET /api/autopilot, shared by the header and the Attack Lab. */
export function useAutopilot() {
  return useQuery({ queryKey: ['autopilot'], queryFn: getAutopilot, refetchInterval: 15_000, retry: false })
}

/** GET /api/approvals?status=pending, for the header's countersign queue. */
export function usePendingApprovals() {
  return useQuery({
    queryKey: ['approvals', 'pending'],
    queryFn: () => listApprovals('pending'),
    refetchInterval: 10_000,
    retry: false,
  })
}

/** GET /api/inbox, polled every 2 s while waiting for autopilot to pick up an attack. */
export function useInbox(enabled: boolean) {
  return useQuery({ queryKey: ['inbox'], queryFn: listInboxItems, enabled, refetchInterval: enabled ? 2_000 : false, retry: false })
}

/** GET /api/inbox for the console's mail panel: every 2 s while autopilot is on, else every 10 s. */
export function useInboxList(fast: boolean) {
  return useQuery({ queryKey: ['inbox'], queryFn: listInboxItems, refetchInterval: fast ? 2_000 : 10_000, retry: false })
}

export function useTrialBalance() {
  return useQuery({ queryKey: ['ledger', 'trial_balance'], queryFn: getTrialBalance, retry: false })
}

export function useReconciliation() {
  return useQuery({ queryKey: ['ledger', 'reconciliation'], queryFn: getReconciliation, retry: false })
}

export function useJournal() {
  return useQuery({ queryKey: ['ledger', 'entries'], queryFn: () => listJournalEntries(), retry: false })
}
