import { cn } from '@/lib/cn'
import { Chip } from './Chip'

/** Marks data from the backend's seeded January–June vendor archive (example records, not Nessie transactions). */
export function DemoHistoryTag({ className, title }: { className?: string; title?: string }) {
  return (
    <Chip
      data-testid="demo-history"
      title={title ?? 'Seeded January–June archive: example records, not Nessie transactions'}
      className={cn('py-0 font-mono text-[0.714rem] text-muted', className)}
    >
      demo history
    </Chip>
  )
}
