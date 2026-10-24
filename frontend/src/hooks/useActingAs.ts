import type { Person } from '@/api/types'
import { useSessionStore } from '@/state/sessionStore'
import { usePeople } from './queries'

/** The person actions are taken as: the one picked in the header, else the first AP manager, else the first person. */
export function useActingAs() {
  const people = usePeople()
  const actingAsId = useSessionStore((s) => s.actingAsId)
  const setActingAs = useSessionStore((s) => s.setActingAs)
  const list: Person[] = people.data ?? []
  const person =
    list.find((p) => p.person_id === actingAsId) ?? list.find((p) => p.role === 'ap_manager') ?? list[0] ?? undefined
  return { people, person, setActingAs }
}
