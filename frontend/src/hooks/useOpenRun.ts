import { useMutation } from '@tanstack/react-query'
import { getBatch, getRun } from '@/api/client'
import { useConsoleStore } from '@/state/consoleStore'

/**
 * Open a run on the console with the batch it really processes: GET /api/runs/{id}, then
 * GET /api/batches/{run.batch_id}. For the autopilot run that is the aggregate batch of
 * admitted inbox items, not the batch an attack was drawn from.
 */
export function useOpenRun(onOpened?: () => void) {
  const openRun = useConsoleStore((s) => s.openRun)
  const selectInvoice = useConsoleStore((s) => s.selectInvoice)
  return useMutation({
    /** `documentId`: focus the console on the invoice made from this document (e.g. the attack just sent). */
    mutationFn: async ({ runId }: { runId: string; documentId?: string }) => {
      const run = await getRun(runId)
      const batch = await getBatch(run.batch_id)
      return { run, batch }
    },
    onSuccess: ({ run, batch }, { documentId }) => {
      openRun(batch, run)
      const focus = documentId && batch.invoices.find((i) => i.document_id === documentId)
      if (focus) selectInvoice(focus.invoice_id)
      onOpened?.()
    },
  })
}
