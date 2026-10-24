import { create } from 'zustand'

/** The eval and red-team jobs started from /proof, so their results survive navigating away. */
interface ProofStore {
  evalJobId: string | null
  redteamJobId: string | null
  setEvalJob: (id: string) => void
  setRedteamJob: (id: string) => void
}

export const useProofStore = create<ProofStore>()((set) => ({
  evalJobId: null,
  redteamJobId: null,
  setEvalJob: (evalJobId) => set({ evalJobId }),
  setRedteamJob: (redteamJobId) => set({ redteamJobId }),
}))
