import { create } from 'zustand'

/** Attacks launched from this browser: their batch, and their run once known. */
export interface Launch {
  batchId: string
  runId: string | null
}

interface AttackStore {
  launches: Launch[]
  addLaunch: (batchId: string) => void
  setLaunchRun: (batchId: string, runId: string) => void
}

export const useAttackStore = create<AttackStore>()((set) => ({
  launches: [],
  addLaunch: (batchId) => set((s) => ({ launches: [...s.launches, { batchId, runId: null }] })),
  setLaunchRun: (batchId, runId) =>
    set((s) => ({ launches: s.launches.map((l) => (l.batchId === batchId && !l.runId ? { ...l, runId } : l)) })),
}))
