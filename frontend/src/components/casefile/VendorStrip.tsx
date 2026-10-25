import type { Person, VendorDetail } from '@/api/types'
import { maskAccount } from '@/lib/account'
import { accountSeenBefore, monthStrip } from '@/lib/caseFile'
import { DemoHistoryTag } from '@/components/ui'
import { cn } from '@/lib/cn'
import { shortDate, summarizeVendor } from '@/lib/vendorHistory'

export interface VendorStripProps {
  vendor: VendorDetail
  people: readonly Person[]
  /** The invoice's month: the strip ends here. */
  endIso: string
  /** The account this invoice asks to be paid to, if known. */
  requestedAccount?: string
}

/** Vendor history as a month strip: ● payments to the account on file, ◆ when it was verified. */
export function VendorStrip({ vendor, people, endIso, requestedAccount }: VendorStripProps) {
  const cells = monthStrip(vendor, endIso)
  const s = summarizeVendor(vendor, people)
  const elsewhere = cells.some((c) => c.paidElsewhere > 0)
  const asksNew = requestedAccount && requestedAccount !== s.accountOnFile
  return (
    <div>
      <div
        role="table"
        aria-label="Payments by month"
        className="mt-3 grid gap-1 text-center font-mono text-[0.786rem] text-muted"
        style={{ gridTemplateColumns: `repeat(${cells.length}, minmax(0, 1fr))` }}
      >
        <div role="row" className="contents">
          {cells.map((c) => (
            <span role="columnheader" key={c.key} className="leading-tight whitespace-nowrap">
              {c.label}
              <span className="block text-[0.714rem]">{c.year ?? '\u00a0'}</span>
            </span>
          ))}
        </div>
        <div role="row" className="contents">
          {cells.map((c) => (
            <span
              role="cell"
              key={c.key}
              data-month={c.key}
              aria-label={`${c.paidOnFile} paid to the account on file${c.paidElsewhere ? `, ${c.paidElsewhere} elsewhere` : ''}${c.verified ? ', account verified' : ''}`}
            >
              <span className="text-good">{'●'.repeat(c.paidOnFile)}</span>
              <span className="text-muted">{'○'.repeat(c.paidElsewhere)}</span>
              {c.verified && <span className="text-accent">◆</span>}
              {!c.paidOnFile && !c.paidElsewhere && !c.verified && <span aria-hidden>·</span>}
            </span>
          ))}
        </div>
      </div>
      <p className="m-0 mt-2.5 text-small">
        <span className="text-good">●</span> paid to {maskAccount(s.accountOnFile)}
        {elsewhere && <> · ○ paid to an older account</>}
        {s.verifiedAt && (
          <>
            {' '}
            · <span className="text-accent">◆</span> verified{s.verifiedBy ? ` by ${s.verifiedBy}` : ''},{' '}
            <span className="whitespace-nowrap">{shortDate(s.verifiedAt)}</span>
          </>
        )}
      </p>
      {(s.archivedPaid > 0 || s.verificationArchived) && (
        <p data-testid="strip-archive" className="m-0 mt-1 flex flex-wrap items-center gap-1.5 text-meta text-muted">
          <DemoHistoryTag />
          {s.archivedPaid > 0 && `${s.archivedPaid} of ${s.paidCount} payments`}
          {s.archivedPaid > 0 && s.verificationArchived && ' and '}
          {s.verificationArchived && 'the verification'} are the seeded Jan–Jun archive
        </p>
      )}
      {requestedAccount && (
        <p className={cn('m-0 mt-1.5 text-small', asksNew ? 'text-bad' : 'text-muted')}>
          Today: asks for {maskAccount(requestedAccount)},{' '}
          {!asksNew
            ? 'the account on file'
            : accountSeenBefore(vendor, requestedAccount)
              ? 'an account this vendor used before'
              : 'never seen before'}
        </p>
      )}
    </div>
  )
}
