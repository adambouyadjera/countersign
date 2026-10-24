import { NavLink, Outlet } from 'react-router-dom'
import { MockBadge } from '@/components/ui'
import { cn } from '@/lib/cn'
import { HealthStrip } from './HealthStrip'
import { SealMark } from './SealMark'

const NAV = [
  { to: '/', label: 'Console', folio: 'I' },
  { to: '/redteam', label: 'Red Team', folio: 'II' },
  { to: '/audit', label: 'Audit', folio: 'III' },
] as const

export function AppShell() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="rule-heavy-double sticky top-0 z-40 mb-[6px] bg-paper">
        <div className="flex h-16 items-stretch gap-10 pr-6 pl-8">
          <NavLink to="/" className="flex items-center gap-3 text-ink no-underline" aria-label="Countersign home">
            <SealMark size={38} />
            <span className="display text-[2.375rem] leading-none">Countersign</span>
          </NavLink>
          <nav aria-label="Primary" className="flex items-stretch">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  cn(
                    'relative flex items-center gap-2 px-4 no-underline transition-colors',
                    isActive ? 'text-ink' : 'text-ink-soft hover:text-ink',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <span className="font-display text-[1.125rem] italic">{item.folio}.</span>
                    <span className={cn('text-[0.9375rem]', isActive ? 'font-semibold' : 'font-medium')}>
                      {item.label}
                    </span>
                    {isActive && <span aria-hidden className="absolute inset-x-3 bottom-0 h-[4px] bg-ink" />}
                  </>
                )}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-5">
            <MockBadge />
            <span className="hidden border-l border-rule-strong pl-5 text-right leading-tight xl:block">
              <span className="block font-display text-[1.25rem] text-ink italic">Fogline Festival</span>
              <span className="smallcaps block text-ink-soft">Accounts payable</span>
            </span>
          </div>
        </div>
        <HealthStrip />
      </header>
      <main className="flex min-h-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}
