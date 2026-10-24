import { cn } from '@/lib/cn'

export type HandleTrust = 'trusted' | 'untrusted' | 'corroborated'

export interface HandleChipProps {
  /** e.g. "$INV-003.remit_account" */
  name: string
  trust: HandleTrust
  className?: string
}

const styles: Record<HandleTrust, string> = {
  trusted: 'border-solid border-ink text-ink bg-paper',
  untrusted:
    'border-dashed border-taint text-taint bg-[color-mix(in_srgb,var(--taint)_7%,var(--paper))]',
  corroborated:
    'border-solid border-verified text-verified bg-[color-mix(in_srgb,var(--verified)_8%,var(--paper))] shadow-[inset_0_0_0_1px_var(--verified)]',
}

const label: Record<HandleTrust, string> = {
  trusted: 'trusted',
  untrusted: 'untrusted',
  corroborated: 'corroborated',
}

function Glyph({ trust }: { trust: HandleTrust }) {
  // Small drawn marks instead of icons: solid tick for trusted, broken ring for
  // untrusted, double tick for corroborated.
  return (
    <svg aria-hidden viewBox="0 0 12 12" className="size-[0.7rem] shrink-0">
      {trust === 'trusted' && (
        <path d="M2.5 6.5 5 9l4.5-6" fill="none" stroke="currentColor" strokeWidth="1.6" />
      )}
      {trust === 'untrusted' && (
        <circle cx="6" cy="6" r="4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2 1.6" />
      )}
      {trust === 'corroborated' && (
        <g fill="none" stroke="currentColor" strokeWidth="1.4">
          <path d="M1 6.5 3.4 9 7.4 3" />
          <path d="M5 8.2 5.8 9l4-6" />
        </g>
      )}
    </svg>
  )
}

/** Splits "$INV-003.remit_account" so the field part reads heavier than the namespace. */
function splitHandle(name: string): [string, string] {
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return [name, '']
  return [name.slice(0, dot + 1), name.slice(dot + 1)]
}

export function HandleChip({ name, trust, className }: HandleChipProps) {
  const [ns, field] = splitHandle(name)
  return (
    <span
      data-trust={trust}
      title={`${name} (${label[trust]})`}
      className={cn(
        'inline-flex max-w-full items-center gap-1.5 rounded-[2px] border px-1.5 py-[1px] align-baseline',
        'font-mono text-[0.8125rem] leading-5 whitespace-nowrap',
        styles[trust],
        className,
      )}
    >
      <Glyph trust={trust} />
      <span className="truncate">
        <span className="opacity-70">{ns}</span>
        <span className="font-medium">{field}</span>
      </span>
      <span className="sr-only">, {label[trust]}</span>
    </span>
  )
}
