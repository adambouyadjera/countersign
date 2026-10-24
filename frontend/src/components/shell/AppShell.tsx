import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { SigLogo } from '@/components/sig'
import { MockBadge } from '@/components/ui'
import { cn } from '@/lib/cn'
import attackCopy from '@/content/attackLab.json'
import { isMockMode } from '@/lib/env'
import { useConsoleStore } from '@/state/consoleStore'
import { ActingAs } from './ActingAs'
import { AutopilotStatus } from './AutopilotStatus'
import { QueueDrawer } from './QueueDrawer'
import { ServiceHealth } from './ServiceHealth'

const linkClass = (active: boolean) =>
  cn(
    'border-b-2 no-underline',
    active ? 'border-[var(--nav-active)] text-[var(--nav-active-text)]' : 'border-transparent text-muted hover:text-fg',
  )

/** "case files" opens the case for the invoice picked on the console; there is no case list yet. */
function CaseFilesLink() {
  const { pathname } = useLocation()
  const run = useConsoleStore((s) => s.run)
  const invoiceId = useConsoleStore((s) => s.focusedInvoiceId)
  const active = pathname.startsWith('/runs/')
  if (!run || !invoiceId) {
    return (
      <span
        aria-disabled="true"
        title="Run a batch and pick an invoice to open its case file"
        className={cn(linkClass(active), 'cursor-default')}
      >
        case files
      </span>
    )
  }
  return (
    <Link
      to={`/runs/${encodeURIComponent(run.run_id)}/invoices/${encodeURIComponent(invoiceId)}`}
      aria-current={active ? 'page' : undefined}
      className={linkClass(active)}
    >
      case files
    </Link>
  )
}

const NAV = [
  { to: '/attack', label: 'attack lab' },
  { to: '/books', label: 'books' },
  { to: '/proof', label: 'proof' },
  { to: '/audit', label: 'audit' },
] as const

export function AppShell() {
  const { pathname } = useLocation()
  const onCase = pathname.startsWith('/runs/')
  // The Attack Lab is the only dark page ("red team mode"); the theme is scoped to this route.
  const redTeam = pathname === '/attack'
  return (
    <div
      className={cn('flex h-dvh flex-col', redTeam && 'bg-[image:var(--page-ground)] bg-bg font-prose')}
      data-theme={redTeam ? 'redteam' : undefined}
    >
      {redTeam && (
        // Static hazard stripe: nothing animates, so reduced motion needs no special case.
        <div
          aria-hidden="true"
          data-testid="hazard-stripe"
          className="h-3 shrink-0 bg-[repeating-linear-gradient(-45deg,var(--red)_0_14px,var(--bg)_14px_28px)]"
        />
      )}
      <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-b-2 border-[var(--header-rule)] px-7 py-3.5 font-mono">
        <Link to="/" aria-label="Countersign home" className="no-underline">
          <SigLogo />
        </Link>
        <nav aria-label="Main" className="flex flex-wrap gap-[18px]">
          <NavLink to="/" end className={({ isActive }) => linkClass(isActive)}>
            console
          </NavLink>
          <CaseFilesLink />
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => linkClass(isActive)}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        {/* The case file mockup's header has no autopilot status; it shows who you act as. */}
        {!onCase && !redTeam && <AutopilotStatus interactive={pathname === '/'} />}
        <QueueDrawer />
        <MockBadge />
        {onCase && <ActingAs className="ml-auto" />}
        {redTeam && (
          <span data-testid="red-team-mode" className="ml-auto font-bold tracking-[0.08em] text-[var(--red)]">
            {isMockMode() ? attackCopy.headerBadgeMock : attackCopy.headerBadgeLive}
          </span>
        )}
        <ServiceHealth className={onCase || redTeam ? undefined : 'ml-auto'} />
      </header>
      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
