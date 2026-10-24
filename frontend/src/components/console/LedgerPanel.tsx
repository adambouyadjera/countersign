import type { UseQueryResult } from '@tanstack/react-query'
import { AccountNumber, EmptyState, Money, Panel } from '@/components/ui'
import type { AccountBalance, Balances, ServiceHealth } from '@/api/types'
import { cn } from '@/lib/cn'
import { ModeTag } from './ModeTag'
import { RequestError } from './RequestError'

export interface LedgerPanelProps {
  balances: UseQueryResult<Balances, Error>
  /** Nessie's entry from GET /api/health, so the ledger can say when the bank is offline. */
  nessie?: ServiceHealth
}

const ROLE_ORDER: Record<AccountBalance['role'], number> = { company: 0, vendor: 1, attacker: 2 }

function AccountRow({ a }: { a: AccountBalance }) {
  return (
    <li
      data-role={a.role}
      className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 border-b border-rule-strong py-1.5 last:border-b-0"
    >
      <div className="min-w-0">
        <p className="m-0 truncate text-[0.8125rem] text-ink" title={a.label}>
          {a.label}
        </p>
        <AccountNumber value={a.display_number} className="text-[0.6875rem] text-ink-soft" />
      </div>
      <div className="flex flex-col items-end justify-end">
        <Money cents={a.balance_cents} flow className="text-[0.9375rem] text-ink" />
        {a.received_this_session_cents !== 0 && (
          <span className="text-[0.6875rem] text-ink-soft">
            +<Money cents={a.received_this_session_cents} flow /> this session
          </span>
        )}
      </div>
    </li>
  )
}

function AttackerCard({ a }: { a: AccountBalance }) {
  const hit = a.received_this_session_cents > 0
  return (
    <section
      aria-label={`Attacker account: ${a.label}`}
      data-role="attacker"
      className={cn(
        'mt-2 shrink-0 border-2 border-dashed border-taint px-2.5 py-1.5',
        hit && 'bg-[color-mix(in_srgb,var(--taint)_8%,var(--paper))]',
      )}
    >
      <p className="smallcaps m-0 !text-[0.6875rem] text-taint">Not on file · attacker</p>
      <p className="m-0 flex items-baseline justify-between gap-2">
        <span className="truncate text-[0.8125rem] text-ink" title={a.label}>
          {a.label}
        </span>
        <AccountNumber value={a.display_number} className="text-[0.6875rem] text-ink-soft" />
      </p>
      <p className="m-0 mt-0.5 flex items-baseline justify-between gap-2">
        <span className="text-[0.75rem] text-taint">Received this session</span>
        <Money
          cents={a.received_this_session_cents}
          flow
          className={cn('text-[1.5rem] leading-tight', hit ? 'text-taint' : 'text-ink-soft')}
        />
      </p>
      <p className="m-0 text-[0.6875rem] text-ink-soft">
        {a.transfers_this_session.length} {a.transfers_this_session.length === 1 ? 'transfer' : 'transfers'}
        {a.transfers_this_session.length > 0 && (
          <span className="font-mono"> · {a.transfers_this_session.map((t) => t.transfer_id).join(', ')}</span>
        )}{' '}
        · balance <Money cents={a.balance_cents} flow />
      </p>
    </section>
  )
}

export function LedgerPanel({ balances, nessie }: LedgerPanelProps) {
  const data = balances.data
  const accounts = data ? [...data.accounts].sort((x, y) => ROLE_ORDER[x.role] - ROLE_ORDER[y.role]) : []
  const ledger = accounts.filter((a) => a.role !== 'attacker')
  const attackers = accounts.filter((a) => a.role === 'attacker')
  const nessieOffline = nessie?.mode === 'offline'

  return (
    <Panel
      dense
      eyebrow="Bank · Nessie"
      title="Ledger"
      aria-label="Ledger balances"
      className="flex min-h-0 flex-col"
      bodyClassName="flex min-h-0 flex-1 flex-col"
    >
      {(data || nessieOffline) && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          {data && <ModeTag mode={data.bank_mode} service="bank" />}
          {nessieOffline && !(data && data.bank_mode === 'offline') && <ModeTag mode="offline" service="nessie" />}
          {balances.isFetching && data && (
            <span role="status" className="text-[0.6875rem] text-ink-soft">
              refreshing…
            </span>
          )}
        </div>
      )}
      {/* Accounts on file scroll; the attacker card stays pinned below them. */}
      <div className="relative min-h-0 flex-1 overflow-y-auto">
        {balances.isError ? (
          <RequestError
            title="Could not read balances"
            error={balances.error}
            onRetry={() => void balances.refetch()}
            retrying={balances.isFetching}
          />
        ) : balances.isPending ? (
          <p role="status" className="m-0 py-6 text-center text-sm text-ink-soft">
            Reading balances…
          </p>
        ) : accounts.length === 0 ? (
          <EmptyState title="No accounts" body="GET /api/balances returned no accounts." />
        ) : (
          <ul aria-label="Accounts" className="m-0 list-none p-0">
            {ledger.map((a) => (
              <AccountRow key={a.account_id} a={a} />
            ))}
          </ul>
        )}
      </div>
      {!balances.isError &&
        attackers.map((a) => (
          <AttackerCard key={a.account_id} a={a} />
        ))}
    </Panel>
  )
}
