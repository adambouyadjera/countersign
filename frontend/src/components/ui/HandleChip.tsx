import { cn } from '@/lib/cn'
import { splitHandle } from '@/lib/handle'

export type HandleTrust = 'trusted' | 'untrusted' | 'corroborated'

export interface HandleChipProps {
  /** e.g. "$INV-003.remit_account_number" */
  name: string
  trust: HandleTrust
  /**
   * Invoice number the handle was extracted from. When given, the chip reads
   * "INV-003 · remit_account_number" and the full handle moves to the tooltip.
   */
  invoiceNumber?: string
  className?: string
}

// The mockup's chip: panel fill, small radius. Untrusted values (they come from
// the invoice) take the claim colour, the colour of everything the invoice says.
const tone: Record<HandleTrust, string> = {
  trusted: 'text-fg',
  untrusted: 'text-claim',
  corroborated: 'text-fg',
}

export function HandleChip({ name, trust, invoiceNumber, className }: HandleChipProps) {
  const [rawNs, field] = splitHandle(name)
  const ns = invoiceNumber && field ? `${invoiceNumber} · ` : rawNs
  return (
    <span
      data-trust={trust}
      title={`${name} (${trust})`}
      className={cn(
        'inline-flex max-w-full items-baseline rounded-[4px] bg-panel px-1.5 py-px font-mono whitespace-nowrap',
        tone[trust],
        className,
      )}
    >
      <span className="truncate">
        <span className="opacity-80">{ns}</span>
        <span className="font-medium">{field}</span>
      </span>
      <span className="sr-only">, {trust}</span>
    </span>
  )
}
