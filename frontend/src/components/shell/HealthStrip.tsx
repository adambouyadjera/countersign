import { StatusDot } from '@/components/ui'

// Service names come from the contract's Health schema. Status is deliberately
// unknown until this strip reads GET /api/health.
const SERVICES = ['nessie', 'elastic', 'anthropic', 'gemini', 'bitdeer'] as const

export function HealthStrip() {
  return (
    <div
      data-placeholder
      aria-label="Service health (not yet connected)"
      className="flex h-8 items-center gap-5 overflow-hidden border-t border-ink bg-paper-2 pr-6 pl-8 text-[0.75rem]"
    >
      <span className="smallcaps shrink-0 text-ink-soft">Services</span>
      <ul className="m-0 flex min-w-0 list-none items-center gap-4 p-0">
        {SERVICES.map((s) => (
          <li key={s} className="flex shrink-0 items-center gap-1.5">
            <StatusDot tone="offline" label={`${s}: not checked`} />
            <span className="font-mono text-ink">{s}</span>
            <span className="text-ink-soft">unchecked</span>
          </li>
        ))}
      </ul>
      <span className="ml-auto shrink-0 truncate font-mono text-[0.6875rem] text-ink-soft">
        placeholder · will read GET /api/health
      </span>
    </div>
  )
}
