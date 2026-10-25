import { create } from 'zustand'
import type { Batch, Run } from '@/api/types'

/**
 * Console UI state that should survive navigating away and back: the batch in
 * the inbox, the run being watched, and the invoice picked in the inbox.
 * Batch and run hold server responses verbatim.
 */
interface ConsoleStore {
  batch: Batch | null
  run: Run | null
  /** Bumped on every POST /api/runs so a re-run with the same run_id still resubscribes. */
  runNonce: number
  /** Invoice the user picked; null follows the duel automatically. */
  selectedInvoiceId: string | null
  /** The invoice the console is showing (picked or followed); the header's case-files link opens it. */
  focusedInvoiceId: string | null
  setBatch: (batch: Batch) => void
  setRun: (run: Run) => void
  /** Watch a run together with the batch it actually processes (e.g. the autopilot run's aggregate batch). */
  openRun: (batch: Batch, run: Run) => void
  /** A newer copy of the same batch (an autopilot batch grows as inbox items arrive); keeps the run. */
  refreshBatch: (batch: Batch) => void
  selectInvoice: (invoiceId: string | null) => void
  setFocused: (invoiceId: string | null) => void
  clear: () => void
}

export const useConsoleStore = create<ConsoleStore>()((set) => ({
  batch: null,
  run: null,
  runNonce: 0,
  selectedInvoiceId: null,
  focusedInvoiceId: null,
  setBatch: (batch) => set({ batch, run: null, selectedInvoiceId: null }),
  setRun: (run) => set((s) => ({ run, runNonce: s.runNonce + 1, selectedInvoiceId: null })),
  openRun: (batch, run) => set((s) => ({ batch, run, runNonce: s.runNonce + 1, selectedInvoiceId: null })),
  refreshBatch: (batch) => set((s) => (s.batch?.batch_id === batch.batch_id ? { batch } : {})),
  selectInvoice: (selectedInvoiceId) => set({ selectedInvoiceId }),
  setFocused: (focusedInvoiceId) => set({ focusedInvoiceId }),
  clear: () => set({ batch: null, run: null, selectedInvoiceId: null }),
}))
