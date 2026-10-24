import type { GraphNode, ProvenanceGraph, SourceSpan } from '@/api/types'

/**
 * The case file's provenance graph as four columns, as in CaseFile.dc.html:
 * source (document spans and trusted records) → handle → checks → action.
 * Field nodes fold into the handle derived from them; plan steps are not drawn.
 * Pure: everything comes from GET /api/runs/{id}/invoices/{id}/graph.
 */

export type ColumnKey = 'source' | 'handle' | 'check' | 'action'

export interface CaseNode {
  id: string
  column: ColumnKey
  /** Untrusted nodes are drawn dashed red; trusted and corroborated solid. */
  untrusted: boolean
  title: string
  subtitle?: string
  /** Source spans: the document line they point at. */
  span?: SourceSpan
  /** Source spans that a check flagged as instruction-like text. */
  instruction?: boolean
  status?: GraphNode['status']
  message?: string
}

export interface CaseColumns {
  source: CaseNode[]
  handle: CaseNode[]
  check: CaseNode[]
  action: CaseNode[]
}

const COLUMN: Partial<Record<GraphNode['kind'], ColumnKey>> = {
  span: 'source',
  trusted_record: 'source',
  handle: 'handle',
  policy_check: 'check',
  action: 'action',
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined
}

/** Span node ids that an INSTRUCTIONS_IN_DOCUMENT check points at. */
export function instructionSpanIds(graph: ProvenanceGraph): Set<string> {
  const checks = new Set(
    graph.nodes.filter((n) => n.kind === 'policy_check' && n.label === 'INSTRUCTIONS_IN_DOCUMENT').map((n) => n.id),
  )
  const spans = new Set(graph.nodes.filter((n) => n.kind === 'span').map((n) => n.id))
  return new Set(graph.edges.filter((e) => checks.has(e.source) && spans.has(e.target)).map((e) => e.target))
}

export function caseColumns(
  graph: ProvenanceGraph,
  opts: { mask?: (account: string) => string; personName?: (id: string) => string } = {},
): CaseColumns {
  const mask = opts.mask ?? ((a: string) => a)
  const personName = opts.personName ?? ((id: string) => id)
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const instructions = instructionSpanIds(graph)
  const cols: CaseColumns = { source: [], handle: [], check: [], action: [] }

  for (const n of graph.nodes) {
    const column = COLUMN[n.kind]
    if (!column) continue
    const untrusted = n.trust === 'untrusted'
    const d = n.detail ?? {}
    const node: CaseNode = { id: n.id, column, untrusted, title: n.label, status: n.status }

    if (n.kind === 'span' && n.span) {
      node.span = n.span
      node.instruction = instructions.has(n.id)
      node.title = `line ${n.span.line}`
      node.subtitle = `invoice · ${untrusted ? 'untrusted' : n.trust}${node.instruction ? ' · instructions' : ''}`
    } else if (n.kind === 'trusted_record') {
      const parts: string[] = [n.trust]
      const account = str(d.account_display)
      const po = str(d.purchase_order)
      const verifiedBy = str(d.verified_by)
      if (account) parts.push(mask(account))
      if (po) parts.push(po)
      if (verifiedBy) parts.push(`verified by ${personName(verifiedBy)}`)
      node.subtitle = parts.join(' · ')
    } else if (n.kind === 'handle') {
      // The field this handle names, and the line it was read from.
      const field = graph.edges
        .filter((e) => e.source === n.id && e.kind === 'derived_from')
        .map((e) => byId.get(e.target))
        .find((t) => t?.kind === 'field')
      const span = field
        ? graph.edges
            .filter((e) => e.source === field.id && e.kind === 'derived_from')
            .map((e) => byId.get(e.target))
            .find((t) => t?.kind === 'span')?.span
        : undefined
      node.subtitle = span ? `from line ${span.line}` : undefined
    } else if (n.kind === 'policy_check') {
      node.message = str(d.message)
    } else if (n.kind === 'action') {
      node.subtitle = n.status
    }
    cols[column].push(node)
  }
  return cols
}

/**
 * Nodes linked to `id` through the graph's edges, in either direction.
 * Plan steps and actions are endpoints, not hubs: the pay step and the decision
 * touch every check, so walking through them would light up the whole graph.
 */
export function connectedIds(graph: ProvenanceGraph, id: string): Set<string> {
  const kind = new Map(graph.nodes.map((n) => [n.id, n.kind]))
  const adj = new Map<string, string[]>()
  for (const e of graph.edges) {
    adj.set(e.source, [...(adj.get(e.source) ?? []), e.target])
    adj.set(e.target, [...(adj.get(e.target) ?? []), e.source])
  }
  const seen = new Set([id])
  const queue = [id]
  while (queue.length > 0) {
    const cur = queue.shift()!
    const k = kind.get(cur)
    if (cur !== id && (k === 'plan_step' || k === 'action')) continue
    for (const next of adj.get(cur) ?? []) {
      if (!seen.has(next)) {
        seen.add(next)
        queue.push(next)
      }
    }
  }
  return seen
}

/**
 * The decision as the graph records it (check nodes and the first action), for
 * when GET /api/runs/{id}/decisions is unavailable. Undefined if the graph has no checks.
 */
export function decisionFromGraph(
  graph: ProvenanceGraph,
): { outcome: 'ALLOW' | 'HOLD' | 'BLOCK'; checks: { code: string; passed: boolean; message: string }[] } | undefined {
  const checks = graph.nodes
    .filter((n) => n.kind === 'policy_check' && (n.status === 'passed' || n.status === 'failed'))
    .map((n) => ({ code: n.label, passed: n.status === 'passed', message: str(n.detail?.message) ?? n.label }))
  if (checks.length === 0) return undefined
  const action = graph.nodes.find((n) => n.kind === 'action')?.status
  const outcome = action === 'blocked' ? 'BLOCK' : action === 'held' ? 'HOLD' : action === 'paid' ? 'ALLOW' : undefined
  if (!outcome) return undefined
  return { outcome, checks }
}
