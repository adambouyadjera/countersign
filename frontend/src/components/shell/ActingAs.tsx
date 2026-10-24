import { isApiError } from '@/api/errors'
import { useActingAs } from '@/hooks/useActingAs'
import { cn } from '@/lib/cn'
import { ROLE_LABEL } from '@/lib/roles'

/** Header switcher: who countersign actions are taken as (from GET /api/people). */
export function ActingAs({ className }: { className?: string }) {
  const { people, person, setActingAs } = useActingAs()
  if (people.isError) {
    return (
      <span className={cn('text-muted', className)} title="GET /api/people failed">
        acting as · people {isApiError(people.error) && people.error.status === 404 ? 'not available yet' : 'unavailable'}
      </span>
    )
  }
  return (
    <label className={cn('flex items-center gap-2 text-muted', className)}>
      acting as
      <select
        value={person?.person_id ?? ''}
        onChange={(e) => setActingAs(e.target.value)}
        disabled={!people.data}
        className="ink-field px-2 py-1 font-[inherit] font-bold text-fg"
      >
        {!people.data && <option value="">…</option>}
        {people.data?.map((p) => (
          <option key={p.person_id} value={p.person_id}>
            {p.name} · {ROLE_LABEL[p.role]}
          </option>
        ))}
      </select>
    </label>
  )
}
