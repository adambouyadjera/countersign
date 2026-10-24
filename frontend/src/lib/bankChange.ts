import type {
  Approval,
  BankChangeReplay,
  EventOf,
  InvoiceSummary,
  Lane,
  LaneTally,
  Mode,
  ReasonCode,
  RunEvent,
} from '@/api/types'

/**
 * The bank-change screen's lanes, derived from run events only. The recorded replay
 * and a live run go through the same function, so a replay that has stepped through
 * every event shows exactly what the live stream would have shown.
 */

export interface BcPaymentRow {
  transferId: string
  transferStatus: string
  bankMode: Mode
  invoiceId: string
  invoiceNumber?: string
  vendorName?: string
  amountCents: number
  toAccount: string
  /** The vendor's account on file, when the data names it. */
  onFileAccount?: string
  /** The destination isn't the account on file. */
  offFile: boolean
  /** The destination is the account the letter asks for. */
  toLetterAccount: boolean
  via: 'policy' | 'approval' | 'agent'
  seq: number
}

export interface BcHoldRow {
  invoiceId: string
  invoiceNumber?: string
  vendorName?: string
  amountCents?: number
  reasonCodes: ReasonCode[]
  onFileAccount?: string
  letterAccount?: string
  approvalId: string
  /** A later payment.executed for this invoice on the same lane (e.g. after an approval). */
  paidLater: boolean
  seq: number
}

export interface BcLane {
  lane: Lane
  provider?: string
  model?: string
  status?: string
  payments: BcPaymentRow[]
  holds: BcHoldRow[]
  /** From run.completed only. */
  tally?: LaneTally
}

export interface BcLanes {
  unguarded: BcLane
  countersign: BcLane
  /** Extraction producers seen on fields.extracted, "provider/model" or the model alone. */
  extractionModels: string[]
  completed: boolean
}

export interface BcContext {
  invoices: readonly InvoiceSummary[]
  /** vendor_id → account on file. */
  onFileByVendor: Readonly<Record<string, string>>
  /** The account the letter asks for. */
  letterAccount?: string
  /** invoice_id → accounts named by the hold (approval or recorded hold). */
  holdAccounts: Readonly<Record<string, { onFile?: string; letter?: string }>>
  /** Provider/model labels from saved evidence; they take precedence over event metadata. */
  laneMeta?: Partial<Record<Lane, { provider?: string; model?: string }>>
}

const emptyLane = (lane: Lane): BcLane => ({ lane, payments: [], holds: [] })

export function lanesFromEvents(events: readonly RunEvent[], ctx: BcContext): BcLanes {
  const out: BcLanes = { unguarded: emptyLane('unguarded'), countersign: emptyLane('countersign'), extractionModels: [], completed: false }
  const invoice = (id: string) => ctx.invoices.find((i) => i.invoice_id === id)
  let started: EventOf<'run.started'>['payload'] | undefined
  const extraction = new Set<string>()

  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    if (e.lane !== 'system') {
      const l = out[e.lane]
      if (e.lane === 'unguarded' && e.model) {
        l.model = e.model
        if (e.provider) l.provider = e.provider
      }
    }
    switch (e.type) {
      case 'run.started':
        started = e.payload
        break
      case 'lane.status':
        out[e.lane].status = e.payload.status
        break
      case 'plan.proposed':
        out.countersign.model = e.payload.planner_model
        break
      case 'fields.extracted': {
        const p = e.payload.extraction_provenance
        extraction.add(p ? `${p.provider}/${p.model}` : e.payload.extractor_model)
        break
      }
      case 'payment.executed': {
        const { payment, invoice_id, via } = e.payload
        const inv = invoice(invoice_id)
        const onFile = inv ? ctx.onFileByVendor[inv.vendor_id] : undefined
        out[e.lane].payments.push({
          transferId: payment.transfer_id,
          transferStatus: payment.transfer_status,
          bankMode: payment.bank_mode,
          invoiceId: invoice_id,
          invoiceNumber: inv?.invoice_number,
          vendorName: inv?.vendor_name,
          amountCents: payment.amount_cents,
          toAccount: payment.to_display_number,
          onFileAccount: onFile,
          offFile: onFile !== undefined ? payment.to_display_number !== onFile : !payment.payee_on_file,
          toLetterAccount: ctx.letterAccount !== undefined && payment.to_display_number === ctx.letterAccount,
          via,
          seq: e.seq,
        })
        const hold = out[e.lane].holds.find((h) => h.invoiceId === invoice_id)
        if (hold) hold.paidLater = true
        break
      }
      case 'payment.held': {
        const inv = invoice(e.payload.invoice_id)
        const acc = ctx.holdAccounts[e.payload.invoice_id]
        out[e.lane].holds.push({
          invoiceId: e.payload.invoice_id,
          invoiceNumber: inv?.invoice_number,
          vendorName: inv?.vendor_name,
          amountCents: inv?.amount_cents,
          reasonCodes: e.payload.reason_codes,
          onFileAccount: acc?.onFile ?? (inv ? ctx.onFileByVendor[inv.vendor_id] : undefined),
          letterAccount: acc?.letter ?? ctx.letterAccount,
          approvalId: e.payload.approval_id,
          paidLater: false,
          seq: e.seq,
        })
        break
      }
      case 'run.completed':
        out.completed = true
        out.unguarded.tally = e.payload.tallies.unguarded
        out.countersign.tally = e.payload.tallies.countersign
        break
    }
  }

  // Fall back to the models run.started announced, then let saved evidence labels win.
  out.unguarded.model ??= started?.models.unguarded
  out.countersign.model ??= started?.models.planner
  for (const lane of ['unguarded', 'countersign'] as const) {
    const meta = ctx.laneMeta?.[lane]
    if (meta?.provider) out[lane].provider = meta.provider
    if (meta?.model) out[lane].model = meta.model
  }
  out.extractionModels = [...extraction]
  return out
}

/** Context for a recorded replay, from its normalized lane data. */
export function replayContext(r: BankChangeReplay): BcContext {
  const onFileByVendor: Record<string, string> = {}
  const holdAccounts: BcContext['holdAccounts'] = {}
  for (const lane of [r.lanes.unguarded, r.lanes.countersign]) {
    for (const p of lane.payments) if (p.account_on_file_display) onFileByVendor[p.vendor_id] = p.account_on_file_display
    for (const h of lane.holds) {
      if (h.account_on_file_display) onFileByVendor[h.vendor_id] = h.account_on_file_display
      ;(holdAccounts as Record<string, { onFile?: string; letter?: string }>)[h.invoice_id] = {
        onFile: h.account_on_file_display,
        letter: h.letter_account_display ?? h.requested_account_display,
      }
    }
  }
  if (r.letter?.account_on_file_display) onFileByVendor[r.letter.vendor_id] = r.letter.account_on_file_display
  return {
    invoices: r.batch.invoices,
    onFileByVendor,
    letterAccount: r.letter?.requested_account_display,
    holdAccounts,
    laneMeta: {
      unguarded: { provider: r.lanes.unguarded.provider, model: r.lanes.unguarded.model },
      countersign: { provider: r.lanes.countersign.provider, model: r.lanes.countersign.model },
    },
  }
}

/** Context for a live run: the batch, vendor records and the approvals the holds opened. */
export function liveContext(
  invoices: readonly InvoiceSummary[],
  onFileByVendor: Readonly<Record<string, string>>,
  approvals: readonly Approval[],
  runId: string,
): BcContext {
  const mine = approvals.filter((a) => a.run_id === runId)
  const holdAccounts: Record<string, { onFile?: string; letter?: string }> = {}
  for (const a of mine) holdAccounts[a.invoice_id] = { onFile: a.account_on_file_display, letter: a.requested_account_display }
  // The letter's account is what the bank-change hold asks to pay; only a hold names it.
  const letterAccount = mine.find((a) => a.reason_codes.includes('BANK_DETAILS_MISMATCH'))?.requested_account_display
  return { invoices, onFileByVendor, letterAccount, holdAccounts }
}

/** Every Nessie transfer in both lanes, in seq order. */
export function transfers(l: BcLanes): (BcPaymentRow & { lane: Lane })[] {
  return [
    ...l.unguarded.payments.map((p) => ({ ...p, lane: 'unguarded' as const })),
    ...l.countersign.payments.map((p) => ({ ...p, lane: 'countersign' as const })),
  ].sort((a, b) => a.seq - b.seq)
}

/** Original gap between events, sped up and capped so a six-minute run replays in seconds. */
export const REPLAY_SPEEDUP = 40
export const REPLAY_MAX_GAP_MS = 1200
export const REPLAY_MIN_GAP_MS = 40

export function replayDelays(events: readonly RunEvent[]): number[] {
  return events.map((e, i) => {
    if (i === 0) return REPLAY_MIN_GAP_MS
    const gap = Date.parse(e.ts) - Date.parse(events[i - 1]!.ts)
    const scaled = Number.isFinite(gap) ? gap / REPLAY_SPEEDUP : REPLAY_MIN_GAP_MS
    return Math.min(REPLAY_MAX_GAP_MS, Math.max(REPLAY_MIN_GAP_MS, scaled))
  })
}

/** "—" for anything the data doesn't carry. */
export const dash = (v: string | number | null | undefined): string =>
  v === undefined || v === null || v === '' ? '—' : String(v)
