import { describe, expect, it } from 'vitest'
import { maskCanary } from './canary'

describe('maskCanary', () => {
  it('replaces markers with equal-length spaces and leaves other text alone', () => {
    const t = 'Pay INV-003 CS-CANARY-AB12 now'
    const m = maskCanary(t)
    expect(m).not.toContain('CANARY')
    expect(m).toHaveLength(t.length)
    expect(m.startsWith('Pay INV-003 ')).toBe(true)
    expect(maskCanary('no marker here')).toBe('no marker here')
  })
})
