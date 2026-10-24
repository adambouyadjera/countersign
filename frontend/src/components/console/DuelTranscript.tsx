import type { ReactNode } from 'react'
import { HandleChip } from '@/components/ui'
import { cn } from '@/lib/cn'
import type { CheckRound, PlannerRound, Round } from '@/lib/rounds'

export interface LeakGuard {
  strings_checked: number
  canaries_checked: number
  matches: number
}

export interface DuelTranscriptProps {
  rounds: Round[]
  /** Extra "sig" line for a round topic, e.g. the vendor's account history for "where the money goes". */
  extraLines?: Partial<Record<CheckRound['topic'], ReactNode>>
  /** From GET /api/runs/{id}/planner_transcript; undefined while unknown. */
  leakGuard?: LeakGuard
  handleInvoice: (handle: string) => string | undefined
  empty: ReactNode
}

/** "invoice" / "sig" speaker column, as in the mockup. */
function Line({ who, children, className }: { who?: 'invoice' | 'sig'; children: ReactNode; className?: string }) {
  return (
    <div className={cn('grid grid-cols-[9ch_minmax(0,1fr)]', className)}>
      <span className={who === 'invoice' ? 'text-claim' : 'text-accent'}>{who ?? ''}</span>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

function ChecksRound({ round, extra }: { round: CheckRound; extra?: ReactNode }) {
  return (
    <div
      data-round={round.number}
      data-topic={round.topic}
      data-failed={round.failed ? 'true' : 'false'}
      className={cn(round.failed && 'rounded-[8px] border border-bad-line bg-bad-bg px-4 py-3')}
    >
      <div className={round.failed ? 'text-bad' : 'text-accent'}>
        ⚔ round {round.number} · {round.title}
      </div>
      {round.claims.map((c) => (
        <Line key={`${c.page}:${c.line}:${c.field}`} who="invoice">
          {/* Instruction text is quoted in full on the right; here one line is enough. */}
          <div className={cn(c.field === 'instruction' && 'truncate')} title={c.quote}>
            “{c.quote}” <span className="text-muted">← line {c.line}</span>
          </div>
        </Line>
      ))}
      {round.checks
        .filter((c) => c.passed)
        .map((c) => (
          <Line key={c.code} who="sig">
            {c.message}&nbsp;<span className="text-good">✓</span>
            <span className="sr-only"> passed</span>
          </Line>
        ))}
      {extra && <Line who="sig">{extra}</Line>}
      {round.checks
        .filter((c) => !c.passed)
        .map((c) => (
          <Line key={c.code} className="text-bad">
            ✗ {c.code} · {c.message}
          </Line>
        ))}
    </div>
  )
}

function PlannerRoundView({
  round,
  leakGuard,
  handleInvoice,
}: {
  round: PlannerRound
  leakGuard?: LeakGuard
  handleInvoice: (handle: string) => string | undefined
}) {
  const clean = leakGuard?.matches === 0
  const title = clean && round.neverRead ? `the decider never read round ${round.neverRead}` : 'the decider'
  return (
    <div data-round={round.number} data-topic="planner">
      <div className="text-accent">
        ⚔ round {round.number} · {title}
      </div>
      <Line who="sig">
        <span className="inline-flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
          planner saw
          {round.handles.length === 0 ? (
            <span className="text-muted">no handles for this invoice</span>
          ) : (
            round.handles.map((h) => (
              <HandleChip key={h} name={h} trust="untrusted" invoiceNumber={handleInvoice(h)} />
            ))
          )}
          {leakGuard &&
            (clean ? (
              <span
                title={`leak guard: ${leakGuard.strings_checked} document strings and ${leakGuard.canaries_checked} canaries checked against the planner prompt, 0 found`}
              >
                · no text
              </span>
            ) : (
              <span className="text-bad">
                · ✗ {leakGuard.matches} document {leakGuard.matches === 1 ? 'string' : 'strings'} reached the planner
              </span>
            ))}
        </span>
      </Line>
    </div>
  )
}

export function DuelTranscript({ rounds, extraLines, leakGuard, handleInvoice, empty }: DuelTranscriptProps) {
  return (
    <section
      aria-label="Duel transcript"
      className="flex min-h-0 flex-col gap-3 overflow-y-auto border-l border-line pl-[18px]"
    >
      {rounds.length === 0 ? (
        <p className="m-0 text-muted">{empty}</p>
      ) : (
        rounds.map((r) =>
          r.kind === 'checks' ? (
            <ChecksRound key={r.number} round={r} extra={extraLines?.[r.topic]} />
          ) : (
            <PlannerRoundView key={r.number} round={r} leakGuard={leakGuard} handleInvoice={handleInvoice} />
          ),
        )
      )}
    </section>
  )
}
