import { cn } from '@/lib/cn'

/** Classes for a chip: 1.5px ink outline, soft offset shadow. Shared by <Chip> and buttons that need a ref. */
export type ChipTone = 'default' | 'untrusted' | 'good' | 'bad'

const TONE: Record<ChipTone, string> = {
  default: 'border-[1.5px] border-solid border-ink bg-card text-fg shadow-[var(--inner-shadow)]',
  // Status badges: dashed red for anything that came from untrusted text, verdigris for a clean result.
  untrusted: 'border-[1.5px] border-dashed border-bad bg-bad-bg text-bad',
  good: 'border-2 border-solid border-good bg-card text-good font-bold',
  // A failed result that is not about untrusted text (e.g. NOT BALANCED): solid, not dashed.
  bad: 'border-2 border-solid border-bad bg-bad-bg text-bad font-bold',
}

export function chipClass({
  pill = false,
  button = false,
  tone = 'default',
}: { pill?: boolean; button?: boolean; tone?: ChipTone } = {}): string {
  return cn(
    'inline-flex items-center gap-1.5 px-2.5 py-1',
    TONE[tone],
    pill ? 'rounded-full px-3.5' : 'rounded-[6px]',
    button &&
      'cursor-pointer font-[inherit] hover:bg-inner focus-visible:outline-[3px] focus-visible:outline-offset-2 focus-visible:outline-accent focus-visible:outline-solid disabled:cursor-not-allowed disabled:opacity-50',
  )
}
