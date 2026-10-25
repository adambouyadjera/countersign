import type { Batch } from '@/api/types'

/** The contract's placeholder: the backend swaps it for the attacker account (CONTRACT.md, POST /api/attacks). */
export const ATTACKER_TOKEN = '{{ATTACKER_ACCOUNT}}'

/** AttackRequest.attack_text bounds from contracts/openapi.yaml. */
export const ATTACK_MIN = 1
export const ATTACK_MAX = 600

export function attackTextProblem(text: string): string | null {
  const n = [...text].length
  if (n < ATTACK_MIN || text.trim().length === 0) return 'Write something first.'
  if (n > ATTACK_MAX) return `Too long: ${n} of ${ATTACK_MAX} characters.`
  return null
}

/** Insert `insert` at the selection [start, end), returning the new text and caret position. */
export function insertAt(text: string, start: number, end: number, insert: string): { text: string; caret: number } {
  const s = Math.max(0, Math.min(start, text.length))
  const e = Math.max(s, Math.min(end, text.length))
  return { text: text.slice(0, s) + insert + text.slice(e), caret: s + insert.length }
}

/** The inbox item for an attack batch: the one whose document is one of the batch's invoices. */
export function inboxItemFor<T extends { document_id: string }>(items: readonly T[], batch: Batch): T | undefined {
  const docs = new Set(batch.invoices.map((i) => i.document_id))
  return items.find((i) => docs.has(i.document_id))
}

/**
 * The document POST /api/attacks queued: only the chosen vendor's invoice goes to the
 * inbox (backend/docs/autopilot.md). The response is a plain Batch with no inbox item id.
 */
export function attackedDocument(batch: Batch, vendorId: string): string | undefined {
  return batch.invoices.find((i) => i.vendor_id === vendorId)?.document_id
}
