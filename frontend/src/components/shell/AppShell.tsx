import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { SigLogo } from '@/components/sig'
import { MockBadge } from '@/components/ui'
import { cn } from '@/lib/cn'
import { useConsoleStore } from '@/state/consoleStore'
import { ActingAs } from './ActingAs'
import { AutopilotStatus } from './AutopilotStatus'
import { ServiceHealth } from './ServiceHealth'

const linkClass = (active: boolean) =>
  cn('border-b-2 no-underline', active ? 'border-accent text-fg' : 'border-transparent text-muted hover:text-fg')

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
  const onCase = useLocation().pathname.startsWith('/runs/')
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-b border-line px-7 py-3.5">
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
        {!onCase && <AutopilotStatus />}
        <MockBadge />
        {onCase && <ActingAs className="ml-auto" />}
        <ServiceHealth className={onCase ? undefined : 'ml-auto'} />
      </header>
      <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
