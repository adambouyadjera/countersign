import { describe, expect, it } from 'vitest'
import { handleNamespaces, splitHandle } from './handle'

describe('splitHandle', () => {
  it('splits at the last dot', () => {
    expect(splitHandle('$INV-003.remit_account_number')).toEqual(['$INV-003.', 'remit_account_number'])
    expect(splitHandle('$inv_5f3c.9a.amount_cents')).toEqual(['$inv_5f3c.9a.', 'amount_cents'])
  })
  it('leaves a name without a field whole', () => {
    expect(splitHandle('$INV-003')).toEqual(['$INV-003', ''])
  })
})

describe('handleNamespaces', () => {
  it('maps namespaces to the invoice the handles were extracted from', () => {
    const m = handleNamespaces([
      { invoice_id: 'inv_aaa', handles: [{ name: '$inv_aaa_long.amount_cents' }, { name: '$inv_aaa_long.due_date' }] },
      { invoice_id: 'inv_bbb', handles: [{ name: '$inv_bbb_long.remit_account_number' }] },
    ])
    expect(m.get('$inv_aaa_long.')).toBe('inv_aaa')
    expect(m.get('$inv_bbb_long.')).toBe('inv_bbb')
    expect(m.get('$VENDOR-HARBOR.')).toBeUndefined()
  })
})
