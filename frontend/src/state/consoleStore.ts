import { create } from 'zustand'
import type { Batch, Run } from '@/api/types'

/**
 * Console UI state that should survive navigating away and back: the batch on
 * the rail and the run being watched. Both hold server responses verbatim.
 */
interface ConsoleStore {
  batch: Batch | null
  run: Run | null
  /** Bumped on every POST /api/runs so a re-run with the same run_id still resubscribes. */
  runNonce: number
  setBatch: (batch: Batch) => void
  setRun: (run: Run) => void
  clear: () => void
}

export const useConsoleStore = create<ConsoleStore>()((set) => ({
  batch: null,
  run: null,
  runNonce: 0,
  setBatch: (batch) => set({ batch, run: null }),
  setRun: (run) => set((s) => ({ run, runNonce: s.runNonce + 1 })),
  clear: () => set({ batch: null, run: null }),
}))
