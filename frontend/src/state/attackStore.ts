import { create } from 'zustand'
import { persist } from 'zustand/middleware'

/** Attacks launched from this browser: their batch, and their run once known. */
export interface Launch {
  batchId: string
  runId: string | null
}

interface AttackStore {
  launches: Launch[]
  addLaunch: (batchId: string) => void
  setLaunchRun: (batchId: string, runId: string) => void
  /**
   * Documents this browser submitted through the Attack Lab. The API has no per-item source
   * (InboxItem carries none), so this is how the console can tag them "attack". Kept across reloads.
   */
  submittedDocs: string[]
  addSubmittedDoc: (documentId: string) => void
}

export const useAttackStore = create<AttackStore>()(
  persist(
    (set) => ({
      launches: [],
      addLaunch: (batchId) => set((s) => ({ launches: [...s.launches, { batchId, runId: null }] })),
      setLaunchRun: (batchId, runId) =>
        set((s) => ({ launches: s.launches.map((l) => (l.batchId === batchId && !l.runId ? { ...l, runId } : l)) })),
      submittedDocs: [],
      addSubmittedDoc: (documentId) =>
        set((s) => (s.submittedDocs.includes(documentId) ? s : { submittedDocs: [...s.submittedDocs, documentId] })),
    }),
    { name: 'countersign-attack-submissions', partialize: (s) => ({ submittedDocs: s.submittedDocs }) },
  ),
)
