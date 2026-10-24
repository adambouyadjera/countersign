import type { Lane } from '@/api/types'
import { cn } from '@/lib/cn'
import type { InvoiceLaneState, InvoiceStatus } from '@/state/runReducer'
import { LANE_META } from './lanes'

export type ChipStatus = InvoiceStatus | 'not_running' | 'idle'

const LABEL: Record<ChipStatus, string> = {
  idle: 'no run',
  not_running: 'not running',
  pending: 'pending',
  extracting: 'extracting',
  cleared: 'cleared',
  paid: 'paid',
  unknown_account: 'paid to unknown account',
  held: 'held',
  blocked: 'blocked',
  failed: 'failed',
}

const TONE: Record<ChipStatus, string> = {
  idle: 'border-rule text-ink-soft',
  not_running: 'border-rule border-dashed text-ink-soft',
  pending: 'border-rule-strong text-ink-soft',
  extracting: 'border-ink text-ink',
  cleared: 'border-verified text-verified',
  paid: 'border-verified bg-[color-mix(in_srgb,var(--verified)_12%,var(--paper))] text-verified',
  unknown_account: 'border-taint bg-taint text-paper',
  held: 'border-hold bg-[color-mix(in_srgb,var(--hold)_14%,var(--paper))] text-[#7a4f0f]',
  blocked: 'border-taint text-taint',
  failed: 'border-taint border-dashed text-taint',
}

const RESOLUTION: Record<NonNullable<InvoiceLaneState['resolution']>, string> = {
  reject: 'rejected',
  pay_vendor_on_file: 'paying on file',
  approve_new_account: 'new account approved',
}

export function InvoiceStatusChip({
  lane,
  status,
  resolution,
  className,
}: {
  lane: Lane
  status: ChipStatus
  resolution?: InvoiceLaneState['resolution']
  className?: string
}) {
  const label = LABEL[status] + (status === 'held' && resolution ? ` · ${RESOLUTION[resolution]}` : '')
  return (
    <span
      data-lane={lane}
      data-status={status}
      aria-label={`${LANE_META[lane].short}: ${label}`}
      className={cn(
        'flex min-w-0 items-start gap-1 rounded-[2px] border px-1 py-[1px] text-[0.6875rem] leading-tight',
        TONE[status],
        className,
      )}
    >
      <span aria-hidden className="font-display text-[0.875rem] leading-none italic">
        {LANE_META[lane].letter}
      </span>
      <span aria-hidden className="min-w-0 font-medium">
        {status === 'extracting' && (
          <span className="mr-1 inline-block size-1.5 -translate-y-px rounded-full bg-current motion-safe:animate-pulse" />
        )}
        {label}
      </span>
    </span>
  )
}
