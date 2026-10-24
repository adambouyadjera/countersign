import { createContext, useContext, type ReactNode } from 'react'
import { AccountNumber, HandleChip, Money, Seal } from '@/components/ui'
import type { EventOf, Mode, PlanStep } from '@/api/types'
import { cn } from '@/lib/cn'
import type { LaneEvent } from '@/state/runReducer'
import { ModeTag } from './ModeTag'

export interface StepRowProps {
  event: LaneEvent
  /** invoice_id → invoice_number from the batch; falls back to the id. */
  invoiceLabel: (invoiceId: string) => string
  /** The lane header already shows this mode; rows only tag a mode that differs. */
  laneMode?: Mode
}

/** HH:MM:SS of an ISO timestamp, as sent (UTC). */
function clock(ts: string): string {
  const m = ts.match(/T(\d{2}:\d{2}:\d{2})/)
  return m?.[1] ?? ts
}

/** The lane's current mode, so rows tag only events whose mode differs from the lane header. */
const LaneModeContext = createContext<Mode | undefined>(undefined)

function Row({
  event,
  kind,
  tone = 'plain',
  children,
}: {
  event: LaneEvent
  kind: string
  tone?: 'plain' | 'quiet' | 'verified' | 'taint' | 'hold'
  children: ReactNode
}) {
  const laneMode = useContext(LaneModeContext)
  return (
    <li
      data-seq={event.seq}
      data-type={event.type}
      className={cn(
        'grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-2 border-b border-rule py-1.5 last:border-b-0',
        tone === 'taint' && 'bg-[color-mix(in_srgb,var(--taint)_7%,var(--paper))]',
        tone === 'hold' && 'bg-[color-mix(in_srgb,var(--hold)_8%,var(--paper))]',
      )}
    >
      {/* Ledger line number: the event's seq; its timestamp on hover. */}
      <span
        title={`seq ${event.seq} · ${clock(event.ts)} UTC`}
        className="pt-[1px] text-right font-mono tabular text-[0.6875rem] leading-tight text-ink-soft"
      >
        {event.seq}
      </span>
      <div className="min-w-0">
        <p
          className={cn(
            'smallcaps m-0 flex items-center gap-2 !text-[0.6875rem]',
            tone === 'verified' && 'text-verified',
            tone === 'taint' && 'text-taint',
            tone === 'hold' && 'text-[#7a4f0f]',
            (tone === 'plain' || tone === 'quiet') && 'text-ink-soft',
          )}
        >
          {kind}
          {event.mode !== laneMode && (
            <ModeTag mode={event.mode} className="!h-4 !text-[0.625rem] normal-case tracking-normal" />
          )}
        </p>
        <div className={cn('text-[0.8125rem] leading-snug', tone === 'quiet' ? 'text-ink-soft' : 'text-ink')}>
          {children}
        </div>
      </div>
    </li>
  )
}

function Inv({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[0.8125rem] font-medium">{children}</span>
}

function DocumentLoaded({ e, label }: { e: EventOf<'document.loaded'>; label: string }) {
  const p = e.payload
  return (
    <>
      <Inv>{label}</Inv> · <span className="font-mono text-[0.75rem] text-ink-soft">{p.document_id}</span> ·{' '}
      {p.pages} {p.pages === 1 ? 'page' : 'pages'}, {p.line_count} lines
    </>
  )
}

function FieldsExtracted({ e, label }: { e: EventOf<'fields.extracted'>; label: string }) {
  const p = e.payload
  return (
    <>
      <p className="m-0">
        <Inv>{label}</Inv> · {p.fields.length} fields by <span className="font-mono text-[0.75rem]">{p.extractor_model}</span>
      </p>
      <div className="mt-1 flex flex-wrap gap-1">
        {p.handles.map((h) => (
          <HandleChip key={h.name} name={h.name} trust={h.trust} className="!text-[0.75rem]" />
        ))}
      </div>
      {p.instruction_like_spans.length > 0 && (
        <div className="mt-1.5 border border-dashed border-taint px-2 py-1 text-[0.75rem] text-taint">
          <p className="smallcaps m-0 !text-[0.625rem]">
            {p.instruction_like_spans.length} instruction-like{' '}
            {p.instruction_like_spans.length === 1 ? 'span' : 'spans'} · kept from the planner
          </p>
          {p.instruction_like_spans.map((s) => (
            <p key={`${s.page}:${s.line}:${s.char_start}`} className="m-0 line-clamp-2 font-mono">
              <span className="text-ink-soft">
                p{s.page} l{s.line}:
              </span>{' '}
              “{s.quote}”
            </p>
          ))}
        </div>
      )}
    </>
  )
}

const OP_ORDER: PlanStep['op'][] = ['resolve_vendor', 'match_po', 'due_within', 'pay', 'hold', 'skip']

function PlanProposed({ e, invoiceLabel }: { e: EventOf<'plan.proposed'>; invoiceLabel: (id: string) => string }) {
  const p = e.payload
  const counts = OP_ORDER.map((op) => [op, p.steps.filter((s) => s.op === op).length] as const).filter(
    ([, n]) => n > 0,
  )
  const pays = p.steps.filter((s) => s.op === 'pay')
  return (
    <>
      <p className="m-0">
        {p.steps.length} steps by <span className="font-mono text-[0.75rem]">{p.planner_model}</span> · prompt{' '}
        <span className="font-mono text-[0.75rem]" title={p.prompt_hash}>
          {p.prompt_hash.slice(0, 10)}
        </span>
      </p>
      <p className="m-0 font-mono text-[0.6875rem] text-ink-soft">
        {counts.map(([op, n]) => `${op}×${n}`).join('  ')}
      </p>
      {pays.length > 0 && (
        <details className="mt-1">
          <summary className="cursor-pointer text-[0.75rem] text-ink-soft">
            {pays.length} pay {pays.length === 1 ? 'step' : 'steps'}, over handles
          </summary>
          <ul className="m-0 mt-1 list-none space-y-1 p-0">
            {pays.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-1">
                <Inv>{invoiceLabel(s.invoice_id)}</Inv>
                {Object.entries(s.args)
                  .filter(([, a]) => a.kind === 'handle' && a.ref)
                  .map(([k, a]) => (
                    <HandleChip key={k} name={a.ref!} trust="untrusted" className="!text-[0.6875rem]" />
                  ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  )
}

const OUTCOME_TONE = { ALLOW: 'verified', HOLD: 'hold', BLOCK: 'taint' } as const

function PolicyDecisionBody({ e, label }: { e: EventOf<'policy.decision'>; label: string }) {
  const d = e.payload.decision
  const failed = d.checks.filter((c) => !c.passed)
  const passed = d.checks.length - failed.length
  return (
    <>
      <p className="m-0 flex flex-wrap items-baseline gap-x-2">
        <Inv>{label}</Inv>
        <span
          className={cn(
            'border px-1 font-mono text-[0.75rem] font-medium tracking-[0.12em]',
            d.outcome === 'ALLOW' && 'border-verified text-verified',
            d.outcome === 'HOLD' && 'border-hold text-[#7a4f0f]',
            d.outcome === 'BLOCK' && 'border-taint text-taint',
          )}
        >
          {d.outcome}
        </span>
        <span className="text-ink-soft">
          {passed}/{d.checks.length} checks passed
        </span>
      </p>
      {failed.length > 0 && (
        <ul className="m-0 mt-0.5 list-none space-y-0.5 p-0">
          {failed.map((c) => (
            <li key={c.code} className="text-[0.75rem]">
              <span className="font-mono text-taint">✕ {c.code}</span> <span className="text-ink-soft">{c.message}</span>
            </li>
          ))}
        </ul>
      )}
      {d.reason_codes.length > 0 && (
        <p className="m-0 mt-0.5 font-mono text-[0.6875rem] text-ink-soft">reason: {d.reason_codes.join(', ')}</p>
      )}
    </>
  )
}

function PaymentExecuted({ e, label }: { e: EventOf<'payment.executed'>; label: string }) {
  const { payment: p, via } = e.payload
  return (
    <>
      <p className="m-0 flex flex-wrap items-baseline gap-x-1.5">
        <Inv>{label}</Inv>
        <Money cents={p.amount_cents} className="font-medium" />
        <span aria-hidden>→</span>
        <span className={cn(!p.payee_on_file && 'font-medium text-taint')}>{p.payee_label}</span>
        <AccountNumber value={p.to_display_number} className="text-[0.75rem]" />
      </p>
      <p className="m-0 flex flex-wrap items-center gap-x-2 text-[0.75rem] text-ink-soft">
        <span>
          transfer <span className="font-mono text-ink">{p.transfer_id}</span>
        </span>
        <span>· {p.transfer_status}</span>
        <span>· via {via}</span>
        <ModeTag mode={p.bank_mode} service="bank" className="!h-4 !text-[0.625rem]" />
      </p>
      {!p.payee_on_file && (
        <p className="smallcaps m-0 mt-0.5 !text-[0.6875rem] text-taint">Payee is not an account on file</p>
      )}
    </>
  )
}

function PaymentHeld({ e, label }: { e: EventOf<'payment.held'>; label: string }) {
  const p = e.payload
  return (
    <div className="flex items-center gap-3">
      <Seal label="HELD" size={64} stampKey={e.seq} />
      <div className="min-w-0">
        <p className="m-0">
          <Inv>{label}</Inv> held for a human countersign
        </p>
        <p className="m-0 font-mono text-[0.75rem] text-[#7a4f0f]">{p.reason_codes.join(', ')}</p>
        <p className="m-0 font-mono text-[0.6875rem] text-ink-soft">{p.approval_id}</p>
      </div>
    </div>
  )
}

const DECISION_TEXT = {
  reject: 'rejected',
  pay_vendor_on_file: 'paid to the vendor on file',
  approve_new_account: 'new account approved',
} as const

export function StepRow({ laneMode, ...rest }: StepRowProps) {
  return (
    <LaneModeContext.Provider value={laneMode}>
      <StepBody {...rest} />
    </LaneModeContext.Provider>
  )
}

function StepBody({ event: e, invoiceLabel }: Omit<StepRowProps, 'laneMode'>) {
  switch (e.type) {
    case 'lane.status':
      return (
        <Row event={e} kind={`Lane ${e.payload.status.replace('_', ' ')}`} tone={e.payload.status === 'failed' ? 'taint' : 'quiet'}>
          {e.payload.detail ?? null}
        </Row>
      )
    case 'document.loaded':
      return (
        <Row event={e} kind="Document loaded" tone="quiet">
          <DocumentLoaded e={e} label={invoiceLabel(e.payload.invoice_id)} />
        </Row>
      )
    case 'fields.extracted':
      return (
        <Row event={e} kind="Fields extracted · quarantined">
          <FieldsExtracted e={e} label={invoiceLabel(e.payload.invoice_id)} />
        </Row>
      )
    case 'plan.proposed':
      return (
        <Row event={e} kind="Plan proposed">
          <PlanProposed e={e} invoiceLabel={invoiceLabel} />
        </Row>
      )
    case 'agent.message':
      return (
        <Row event={e} kind="Agent says">
          <Inv>{invoiceLabel(e.payload.invoice_id)}</Inv>{' '}
          <span className="font-display text-[0.9375rem] italic">“{e.payload.text}”</span>
        </Row>
      )
    case 'agent.tool_call':
      return (
        <Row event={e} kind="Tool call">
          <p className="m-0 font-mono text-[0.75rem] break-all">
            <span className="font-medium">{e.payload.tool}</span>(
            {Object.entries(e.payload.args)
              .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
              .join(', ')}
            )
          </p>
          <p className="m-0 text-[0.75rem] text-ink-soft">↳ {e.payload.result_summary}</p>
        </Row>
      )
    case 'policy.decision':
      return (
        <Row event={e} kind="Policy decision" tone={OUTCOME_TONE[e.payload.decision.outcome]}>
          <PolicyDecisionBody e={e} label={invoiceLabel(e.payload.decision.invoice_id)} />
        </Row>
      )
    case 'payment.executed':
      return (
        <Row
          event={e}
          kind={e.payload.payment.payee_on_file ? 'Payment executed' : 'Paid to unknown account'}
          tone={e.payload.payment.payee_on_file ? 'verified' : 'taint'}
        >
          <PaymentExecuted e={e} label={invoiceLabel(e.payload.invoice_id)} />
        </Row>
      )
    case 'payment.held':
      return (
        <Row event={e} kind="Payment held" tone="hold">
          <PaymentHeld e={e} label={invoiceLabel(e.payload.invoice_id)} />
        </Row>
      )
    case 'approval.resolved':
      return (
        <Row event={e} kind="Approval resolved" tone="quiet">
          <Inv>{invoiceLabel(e.payload.invoice_id)}</Inv> {DECISION_TEXT[e.payload.decision]}
          {e.payload.note && <span className="block text-[0.75rem] italic">“{e.payload.note}”</span>}
        </Row>
      )
    case 'ledger.posted': {
      const entry = e.payload.entry
      const debit = entry.lines.reduce((n, l) => n + l.debit_cents, 0)
      return (
        <Row event={e} kind="Journal entry posted" tone="quiet">
          <span className="font-mono text-[0.75rem]">{entry.entry_id}</span> · {entry.lines.length} lines ·{' '}
          <Money cents={debit} className="text-[0.75rem]" /> debited
        </Row>
      )
    }
    case 'error':
      return (
        <Row event={e} kind={e.payload.recoverable ? 'Error · recoverable' : 'Error'} tone="taint">
          {e.payload.invoice_id && <Inv>{invoiceLabel(e.payload.invoice_id)} </Inv>}
          {e.payload.message}
        </Row>
      )
    default:
      return null
  }
}
