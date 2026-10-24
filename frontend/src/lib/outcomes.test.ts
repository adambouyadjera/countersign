import { describe, expect, it } from 'vitest'
import peopleFixture from '@fixtures/people.json'
import vendorFixture from '@fixtures/vendor_detail.json'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import type { Person, VendorDetail } from '@/api/types'
import { reduceEvents } from '@/state/runReducer'
import { countersignOutcome, unguardedOutcome } from './outcomes'
import { shortDate, summarizeVendor, timesText } from './vendorHistory'

const all = parseJsonl(runBothEventsText)
const lanes = (upTo = Infinity) => reduceEvents(all.filter((e) => e.seq <= upTo)).lanes

describe('lane outcomes for one invoice (fixture run)', () => {
  it('unguarded read the poisoned invoice, believed it and paid an account not on file', () => {
    const o = unguardedOutcome(lanes().unguarded.steps, 'INV-003', true)
    expect(o).toMatchObject({ kind: 'paid_off_file', words: ['read it', 'believed it', 'paid'], amountCents: 64000 })
    expect(o.toAccountId).toBe('example-nessie-account-attacker')
  })

  it('countersign tagged it untrusted, checked it and held it', () => {
    expect(countersignOutcome(lanes().countersign.steps, 'INV-003', true)).toEqual({
      kind: 'held',
      words: ['tagged untrusted', 'checked', 'held'],
      amountCents: 0,
    })
  })

  it('a clean invoice is paid on file by both', () => {
    expect(unguardedOutcome(lanes().unguarded.steps, 'INV-001', true).kind).toBe('paid_on_file')
    expect(countersignOutcome(lanes().countersign.steps, 'INV-001', true).kind).toBe('paid_on_file')
  })

  it('words appear only as their events arrive', () => {
    expect(countersignOutcome(lanes(22).countersign.steps, 'INV-003', true)).toEqual({ kind: 'in_progress', words: [] })
    expect(countersignOutcome(lanes(23).countersign.steps, 'INV-003', true).words).toEqual(['tagged untrusted'])
    expect(countersignOutcome([], 'INV-003', true).kind).toBe('waiting')
  })

  it('a lane that is not running says so', () => {
    expect(unguardedOutcome(lanes().unguarded.steps, 'INV-003', false)).toEqual({ kind: 'not_running', words: [] })
  })
})

describe('summarizeVendor', () => {
  it('counts payments to the account on file and names the verifier', () => {
    const s = summarizeVendor(vendorFixture as VendorDetail, peopleFixture as Person[])
    expect(s).toEqual({
      accountOnFile: '300300003333',
      paidCount: 1,
      paidSince: '2026-09-30',
      verifiedAt: '2026-02-01T18:00:00Z',
      verifiedBy: 'Morgan Diaz',
    })
  })

  it('falls back to the person id when people are unknown', () => {
    expect(summarizeVendor(vendorFixture as VendorDetail).verifiedBy).toBe('person-vendor-admin')
  })

  it('formats dates and counts', () => {
    expect(shortDate('2026-03-12T10:00:00Z')).toBe('Mar 12')
    expect(shortDate('2026-09-30', true)).toBe('Sep 2026')
    expect([0, 1, 2, 6].map(timesText)).toEqual(['never', 'once', 'twice', '6 times'])
  })
})
