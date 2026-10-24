import { describe, expect, it } from 'vitest'
import graphFixture from '@fixtures/graph_INV-003.json'
import type { ProvenanceGraph } from '@/api/types'
import { maskAccount } from './account'
import { caseColumns, connectedIds, decisionFromGraph, instructionSpanIds, type CaseNode } from './caseGraph'

const graph = graphFixture as ProvenanceGraph

describe('caseColumns (graph_INV-003 fixture)', () => {
  const cols = caseColumns(graph, {
    mask: maskAccount,
    personName: (id) => (id === 'person-vendor-admin' ? 'Morgan Diaz' : id),
  })

  it('puts spans and trusted records in source, handles, checks and actions in their columns', () => {
    expect(cols.source.map((n) => n.title)).toEqual([
      'line 4',
      'line 5',
      'line 6',
      'line 7',
      'Verified vendor and account on file',
      'Open purchase order',
      'Verified vendor account history',
    ])
    expect(cols.handle.map((n) => n.title)).toEqual([
      '$INV-003.amount_cents',
      '$INV-003.due_date',
      '$INV-003.remit_account_number',
    ])
    expect(cols.check).toHaveLength(7)
    expect(cols.action.map((n) => n.status)).toEqual(['held', 'rejected'])
  })

  it('drops field and plan-step nodes', () => {
    const ids = Object.values(cols).flatMap((c: CaseNode[]) => c.map((n) => n.id))
    expect(ids.some((id) => id.includes('-field-') || id.startsWith('node-step-'))).toBe(false)
  })

  it('marks untrusted sources dashed, trusted records solid, and flags the instruction span', () => {
    const line7 = cols.source.find((n) => n.title === 'line 7')!
    expect(line7).toMatchObject({ untrusted: true, instruction: true, subtitle: 'invoice · untrusted · instructions' })
    expect(line7.span?.line).toBe(7)
    const line6 = cols.source.find((n) => n.title === 'line 6')!
    expect(line6).toMatchObject({ untrusted: true, instruction: false, subtitle: 'invoice · untrusted' })
    const vendor = cols.source.find((n) => n.id === 'node-INV-003-trusted-vendor')!
    expect(vendor).toMatchObject({ untrusted: false, subtitle: 'trusted · •••• 3333' })
    const history = cols.source.find((n) => n.id === 'node-INV-003-trusted-history')!
    expect(history.subtitle).toBe('trusted · •••• 3333 · verified by Morgan Diaz')
  })

  it('gives each handle the line its field was read from', () => {
    expect(cols.handle.find((n) => n.title.endsWith('remit_account_number'))!.subtitle).toBe('from line 6')
  })

  it('keeps check status and message', () => {
    const payee = cols.check.find((n) => n.title === 'PAYEE_ON_FILE')!
    expect(payee.status).toBe('failed')
    expect(payee.message).toBe('Requested remittance account differs from the verified account on file.')
  })
})

describe('instructionSpanIds', () => {
  it('finds spans that INSTRUCTIONS_IN_DOCUMENT points at', () => {
    expect([...instructionSpanIds(graph)]).toEqual(['node-INV-003-span-instruction'])
  })
})

describe('connectedIds', () => {
  it('follows the remit account from its line to the handle, the check, the vendor record and the hold', () => {
    const ids = connectedIds(graph, 'node-INV-003-span-remit_account_number')
    for (const id of [
      'node-INV-003-field-remit_account_number',
      'node-INV-003-handle-remit_account_number',
      'node-INV-003-check-PAYEE_ON_FILE',
      'node-INV-003-trusted-vendor',
      'node-INV-003-held',
    ]) {
      expect(ids).toContain(id)
    }
  })

  it('does not walk through the pay step or the decision to unrelated checks', () => {
    const ids = connectedIds(graph, 'node-INV-003-span-remit_account_number')
    expect(ids).not.toContain('node-INV-003-check-AMOUNT_MATCHES_PO')
    expect(ids).not.toContain('node-INV-003-span-due_date')
  })
})

describe('decisionFromGraph', () => {
  it('rebuilds the hold and its checks from the graph', () => {
    const d = decisionFromGraph(graph)!
    expect(d.outcome).toBe('HOLD')
    expect(d.checks.filter((c) => !c.passed).map((c) => c.code)).toEqual(['PAYEE_ON_FILE', 'INSTRUCTIONS_IN_DOCUMENT'])
  })
})
