import { describe, expect, it } from 'vitest'
import batchFixture from '@fixtures/batch.json'
import inboxFixture from '@fixtures/inbox.json'
import type { Batch } from '@/api/types'
import { ATTACK_MAX, ATTACKER_TOKEN, attackTextProblem, inboxItemFor, insertAt } from './attack'

describe('attackTextProblem', () => {
  it('accepts 1 to 600 characters', () => {
    expect(attackTextProblem('x')).toBeNull()
    expect(attackTextProblem('x'.repeat(ATTACK_MAX))).toBeNull()
  })
  it('rejects empty, blank and over-long text', () => {
    expect(attackTextProblem('')).toBe('Write something first.')
    expect(attackTextProblem('   ')).toBe('Write something first.')
    expect(attackTextProblem('x'.repeat(601))).toBe('Too long: 601 of 600 characters.')
  })
})

describe('insertAt', () => {
  it('inserts the token at the caret', () => {
    expect(insertAt('ab', 1, 1, ATTACKER_TOKEN)).toEqual({ text: `a${ATTACKER_TOKEN}b`, caret: 1 + ATTACKER_TOKEN.length })
  })
  it('replaces a selection and clamps out-of-range positions', () => {
    expect(insertAt('abcd', 1, 3, 'X')).toEqual({ text: 'aXd', caret: 2 })
    expect(insertAt('ab', 9, 9, 'X')).toEqual({ text: 'abX', caret: 3 })
  })
})

describe('inboxItemFor', () => {
  it("finds the inbox item holding one of the batch's documents", () => {
    expect(inboxItemFor(inboxFixture, batchFixture as Batch)?.item_id).toBe('inbox-example-1')
    expect(inboxItemFor(inboxFixture, { ...(batchFixture as Batch), invoices: [] })).toBeUndefined()
  })
})
