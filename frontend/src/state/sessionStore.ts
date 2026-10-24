import { create } from 'zustand'

/** Who the human at the keyboard is acting as, for countersign actions (ApprovalDecision.actor_id). */
interface SessionStore {
  /** null = the default person (see useActingAs). */
  actingAsId: string | null
  setActingAs: (personId: string) => void
}

export const useSessionStore = create<SessionStore>()((set) => ({
  actingAsId: null,
  setActingAs: (actingAsId) => set({ actingAsId }),
}))
