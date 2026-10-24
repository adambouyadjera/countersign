import { describe, expect, it } from 'vitest'
import vendorFixture from '@fixtures/vendor_detail.json'
import decisionsFixture from '@fixtures/policy_decisions.json'
import type { PolicyDecision, SourceSpan, VendorDetail } from '@/api/types'
import { accountSeenBefore, decisionLine, marksByLine, monthStrip, splitLine } from './caseFile'

const vendor = vendorFixture as VendorDetail
const inv003 = (decisionsFixture as PolicyDecision[]).find((d) => d.invoice_id === 'INV-003')!

describe('decisionLine', () => {
  it("opens with the outcome and then every failed check's message", () => {
    expect(decisionLine(inv003)).toBe(
      "I didn't pay this one. Requested remittance account differs from the verified account on file. Instruction-like text is present and excluded from the planner prompt.",
    )
  })
  it('says so when nothing failed', () => {
    expect(decisionLine({ outcome: 'ALLOW', checks: [{ ...inv003.checks[0]!, passed: true }] })).toBe(
      'I paid this one. Every check passed.',
    )
  })
  it('covers a block', () => {
    expect(decisionLine({ outcome: 'BLOCK', checks: [{ ...inv003.checks[0]!, passed: false, message: 'Unknown vendor.' }] })).toBe(
      'I blocked this one. Unknown vendor.',
    )
  })
})

describe('span highlighting', () => {
  const span = (line: number, char_start: number, char_end: number): SourceSpan => ({
    document_id: 'doc-1',
    page: 1,
    line,
    char_start,
    char_end,
    quote: '',
    span_verified: true,
  })

  it('cuts a line into plain and marked pieces', () => {
    expect(splitLine('Remit account: 999900009999', [{ start: 15, end: 27, kind: 'field' }])).toEqual([
      { text: 'Remit account: ' },
      { text: '999900009999', kind: 'field' },
    ])
  })

  it('lets an instruction span win where it overlaps a field', () => {
    expect(
      splitLine('abcdef', [
        { start: 0, end: 4, kind: 'field' },
        { start: 2, end: 6, kind: 'instruction' },
      ]),
    ).toEqual([
      { text: 'ab', kind: 'field' },
      { text: 'cdef', kind: 'instruction' },
    ])
  })

  it('clamps spans to the line and leaves unmarked lines whole', () => {
    expect(splitLine('short', [{ start: 3, end: 99, kind: 'field' }])).toEqual([
      { text: 'sho' },
      { text: 'rt', kind: 'field' },
    ])
    expect(splitLine('plain')).toEqual([{ text: 'plain' }])
  })

  it('groups spans by line for one document page only', () => {
    const m = marksByLine(
      [
        { span: span(6, 15, 27), kind: 'field' },
        { span: span(7, 0, 10), kind: 'instruction' },
        { span: { ...span(7, 0, 3), document_id: 'other' }, kind: 'field' },
      ],
      'doc-1',
      1,
    )
    expect(m.get(6)).toEqual([{ start: 15, end: 27, kind: 'field' }])
    expect(m.get(7)).toEqual([{ start: 0, end: 10, kind: 'instruction' }])
  })
})

describe('monthStrip', () => {
  it('runs from the verification of the account on file to the invoice month', () => {
    const cells = monthStrip(vendor, '2026-10-23T17:00:45Z')
    expect(cells.map((c) => c.key)).toEqual([
      '2026-02',
      '2026-03',
      '2026-04',
      '2026-05',
      '2026-06',
      '2026-07',
      '2026-08',
      '2026-09',
      '2026-10',
    ])
    expect(cells[0]).toMatchObject({ label: 'Feb', year: '2026', verified: true, paidOnFile: 0 })
    expect(cells.find((c) => c.key === '2026-09')).toMatchObject({ label: 'Sep', paidOnFile: 1, verified: false })
    expect(cells.find((c) => c.key === '2026-09')!.year).toBeUndefined()
  })

  it('shows at least six and at most twelve months', () => {
    expect(monthStrip({ ...vendor, payments: [], account_history: [] }, '2026-10-01')).toHaveLength(6)
    const old = { ...vendor, payments: [{ ...vendor.payments[0]!, date: '2020-01-01' }] }
    expect(monthStrip(old, '2026-10-01')).toHaveLength(12)
  })

  it('knows which accounts the vendor has used', () => {
    expect(accountSeenBefore(vendor, '300300003333')).toBe(true)
    expect(accountSeenBefore(vendor, '300300001010')).toBe(true)
    expect(accountSeenBefore(vendor, '999900009999')).toBe(false)
  })
})
