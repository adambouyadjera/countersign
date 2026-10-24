import type { DocumentText, PolicyDecision, SourceSpan, VendorDetail } from '@/api/types'

// ── The decision in one line ─────────────────────────────────────────────────

const OPENER: Record<PolicyDecision['outcome'], string> = {
  ALLOW: 'I paid this one.',
  HOLD: "I didn't pay this one.",
  BLOCK: 'I blocked this one.',
}

/** Sig's one line: the outcome, then every failed check's message, as the policy wrote it. */
export function decisionLine(decision: {
  outcome: PolicyDecision['outcome']
  checks: readonly { passed: boolean; message: string }[]
}): string {
  const failed = decision.checks.filter((c) => !c.passed).map((c) => c.message.trim())
  if (failed.length === 0) return `${OPENER[decision.outcome]} Every check passed.`
  return [OPENER[decision.outcome], ...failed].join(' ')
}

// ── Document spans ───────────────────────────────────────────────────────────

export interface LineMark {
  start: number
  end: number
  kind: 'field' | 'instruction'
}

/** One document line's text cut into plain and marked pieces. Overlaps resolve to "instruction". */
export interface LinePiece {
  text: string
  kind?: LineMark['kind']
}

export function marksByLine(
  spans: readonly { span: SourceSpan; kind: LineMark['kind'] }[],
  documentId: string,
  page: number,
): Map<number, LineMark[]> {
  const out = new Map<number, LineMark[]>()
  for (const { span, kind } of spans) {
    if (span.document_id !== documentId || span.page !== page) continue
    out.set(span.line, [...(out.get(span.line) ?? []), { start: span.char_start, end: span.char_end, kind }])
  }
  return out
}

export function splitLine(text: string, marks: readonly LineMark[] = []): LinePiece[] {
  const chars = [...text]
  const kinds: (LineMark['kind'] | undefined)[] = chars.map(() => undefined)
  for (const m of marks) {
    for (let i = Math.max(0, m.start); i < Math.min(chars.length, m.end); i++) {
      if (kinds[i] !== 'instruction') kinds[i] = m.kind
    }
  }
  const pieces: LinePiece[] = []
  chars.forEach((ch, i) => {
    const last = pieces[pieces.length - 1]
    if (last && last.kind === kinds[i]) last.text += ch
    else pieces.push(kinds[i] ? { text: ch, kind: kinds[i] } : { text: ch })
  })
  return pieces
}

export function documentLineCount(doc: DocumentText): number {
  return doc.pages.reduce((n, p) => n + p.lines.length, 0)
}

// ── Vendor month strip ───────────────────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export interface MonthCell {
  key: string // YYYY-MM
  label: string // "Feb"
  /** "2026" on the first month and on each January, else undefined. */
  year?: string
  paidOnFile: number
  paidElsewhere: number
  verified: boolean
}

function ym(iso: string): [number, number] | null {
  const m = iso.match(/^(\d{4})-(\d{2})/)
  return m ? [Number(m[1]), Number(m[2]) - 1] : null
}

/**
 * One cell per month from the earliest payment or verification of the account
 * on file up to `endIso` (the invoice's month), at least `min` and at most `max` months.
 */
export function monthStrip(vendor: VendorDetail, endIso: string, opts: { min?: number; max?: number } = {}): MonthCell[] {
  const min = opts.min ?? 6
  const max = opts.max ?? 12
  const end = ym(endIso)
  if (!end) return []
  const endIdx = end[0] * 12 + end[1]
  const onFile = vendor.account_on_file_display
  const verifications = vendor.account_history.filter((h) => h.account_display === onFile).map((h) => h.verified_at)
  const dates = [...vendor.payments.map((p) => p.date), ...verifications]
  const idxs = dates.map(ym).filter((x): x is [number, number] => x !== null).map(([y, m]) => y * 12 + m)
  const earliest = idxs.length ? Math.min(...idxs) : endIdx
  const span = Math.min(max, Math.max(min, endIdx - earliest + 1))
  const startIdx = endIdx - span + 1

  const cells: MonthCell[] = []
  for (let i = startIdx; i <= endIdx; i++) {
    const y = Math.floor(i / 12)
    const m = i % 12
    const key = `${y}-${String(m + 1).padStart(2, '0')}`
    const showYear = i === startIdx || m === 0
    cells.push({
      key,
      label: MONTHS[m]!,
      ...(showYear ? { year: String(y) } : {}),
      paidOnFile: vendor.payments.filter((p) => p.date.startsWith(key) && p.account_display === onFile).length,
      paidElsewhere: vendor.payments.filter((p) => p.date.startsWith(key) && p.account_display !== onFile).length,
      verified: verifications.some((v) => v.startsWith(key)),
    })
  }
  return cells
}

/** Has this vendor ever had, or been paid at, this account? */
export function accountSeenBefore(vendor: VendorDetail, account: string): boolean {
  return (
    vendor.account_history.some((h) => h.account_display === account) ||
    vendor.payments.some((p) => p.account_display === account)
  )
}
