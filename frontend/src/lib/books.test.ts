import { describe, expect, it } from 'vitest'
import ledgerFixture from '@fixtures/ledger_entries.json'
import reconFixture from '@fixtures/reconciliation.json'
import tbFixture from '@fixtures/trial_balance.json'
import type { JournalEntry, Reconciliation, TrialBalance } from '@/api/types'
import { booksVerdict, journalNewestFirst, reconSummary, trialTotals } from './books'

const tb = tbFixture as TrialBalance
const rec = reconFixture as Reconciliation

describe('books helpers', () => {
  it('totals the trial balance', () => {
    expect(trialTotals(tb)).toEqual({ debit: 2604000, credit: 2604000 })
  })

  it('summarises both sides of the reconciliation', () => {
    const s = reconSummary(rec)
    expect(s.bank).toEqual({ count: 9, cents: 604000 })
    expect(s.ledger).toEqual({ count: 9, cents: 604000 })
    expect(s.unmatched).toEqual([])
    const off = reconSummary({
      ...rec,
      items: [...rec.items, { transfer_id: 't-x', journal_entry_id: null, amount_cents: 500, status: 'bank_only' }],
    })
    expect(off.bank.count).toBe(10)
    expect(off.ledger.count).toBe(9)
    expect(off.unmatched).toHaveLength(1)
  })

  it('lists the journal newest first', () => {
    const j = journalNewestFirst(ledgerFixture as JournalEntry[])
    expect(j[0]!.entry_id).toBe('journal-example-nessie-transfer-9')
    expect(j.at(-1)!.entry_id).toBe('journal-session-seed')
  })
})

describe('booksVerdict', () => {
  it('is pleased only when balanced and the difference is zero', () => {
    expect(booksVerdict(tb, rec)).toMatchObject({ expression: 'pleased', agree: true })
  })
  it('is serious when the trial balance is not balanced', () => {
    const v = booksVerdict({ ...tb, balanced: false }, rec)
    expect(v).toMatchObject({ expression: 'serious', agree: false })
    expect(v.line).toBe("The books don't balance: debits $26,040.00, credits $26,040.00.")
  })
  it('is serious when the reconciliation has a difference, even if balanced', () => {
    const v = booksVerdict(tb, { ...rec, difference_cents: -1250 })
    expect(v).toMatchObject({ expression: 'serious', agree: false })
    expect(v.line).toContain('differ by $12.50')
  })
  it('waits while either is missing', () => {
    expect(booksVerdict(tb, undefined)).toMatchObject({ expression: 'default', agree: false })
  })
})
