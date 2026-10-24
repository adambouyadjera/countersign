import type { JournalEntry, Reconciliation, TrialBalance } from '@/api/types'
import type { SigExpression } from '@/components/sig/face'
import { formatCents } from './money'

export function trialTotals(tb: TrialBalance): { debit: number; credit: number } {
  return tb.accounts.reduce(
    (t, a) => ({ debit: t.debit + a.debit_cents, credit: t.credit + a.credit_cents }),
    { debit: 0, credit: 0 },
  )
}

export interface ReconSide {
  count: number
  cents: number
}

/** Bank side = items with a Nessie transfer; ledger side = items with a journal entry. */
export function reconSummary(rec: Reconciliation): {
  bank: ReconSide
  ledger: ReconSide
  unmatched: Reconciliation['items']
} {
  const side = (has: (i: Reconciliation['items'][number]) => boolean): ReconSide => {
    const items = rec.items.filter(has)
    return { count: items.length, cents: items.reduce((n, i) => n + i.amount_cents, 0) }
  }
  return {
    bank: side((i) => i.transfer_id !== null),
    ledger: side((i) => i.journal_entry_id !== null),
    unmatched: rec.items.filter((i) => i.status !== 'matched'),
  }
}

/** Newest first: by date, then later in the API's list first (the API lists in posting order). */
export function journalNewestFirst(entries: readonly JournalEntry[]): JournalEntry[] {
  return entries
    .map((e, i) => ({ e, i }))
    .sort((a, b) => (a.e.date === b.e.date ? b.i - a.i : a.e.date < b.e.date ? 1 : -1))
    .map(({ e }) => e)
}

/**
 * Sig's line on the books. Pleased only when the API itself reports a balanced
 * trial balance and a zero reconciliation difference.
 */
export function booksVerdict(
  tb: TrialBalance | undefined,
  rec: Reconciliation | undefined,
): { expression: SigExpression; line: string; agree: boolean } {
  if (!tb || !rec) return { expression: 'default', line: 'Reading the books…', agree: false }
  if (!tb.balanced) {
    const t = trialTotals(tb)
    return {
      expression: 'serious',
      line: `The books don't balance: debits ${formatCents(t.debit)}, credits ${formatCents(t.credit)}.`,
      agree: false,
    }
  }
  if (rec.difference_cents !== 0) {
    return {
      expression: 'serious',
      line: `The ledger and the bank differ by ${formatCents(Math.abs(rec.difference_cents))}. I'm not calling that reconciled.`,
      agree: false,
    }
  }
  return {
    expression: 'pleased',
    line: 'Every dollar I moved has a journal entry and a bank record, and they match.',
    agree: true,
  }
}
