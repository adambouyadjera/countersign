import { describe, expect, it } from 'vitest'
import { formatCents, MINUS } from './money'

describe('formatCents', () => {
  it('formats whole and fractional cents', () => {
    expect(formatCents(0)).toBe('$0.00')
    expect(formatCents(7)).toBe('$0.07')
    expect(formatCents(99)).toBe('$0.99')
    expect(formatCents(100)).toBe('$1.00')
    expect(formatCents(482_150)).toBe('$4,821.50')
  })

  it('formats negatives with a true minus sign', () => {
    expect(formatCents(-1)).toBe(`${MINUS}$0.01`)
    expect(formatCents(-129_999)).toBe(`${MINUS}$1,299.99`)
  })

  it('stays exact for large values', () => {
    expect(formatCents(987_654_321_00)).toBe('$987,654,321.00')
    expect(formatCents(Number.MAX_SAFE_INTEGER)).toBe('$90,071,992,547,409.91')
  })

  it('rounds non-integer input and rejects non-finite values', () => {
    expect(formatCents(150.6)).toBe('$1.51')
    expect(formatCents(Number.NaN)).toBe('—')
  })
})
