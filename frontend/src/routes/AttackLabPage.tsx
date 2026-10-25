import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { createAttack, createBatch } from '@/api/client'
import { isApiError } from '@/api/errors'
import type { Batch, InboxItem, InvoiceSummary } from '@/api/types'
import { RequestError } from '@/components/console/RequestError'
import { SigSays } from '@/components/sig'
import { Card, Chip, InkButton, InkRow } from '@/components/ui'
import { useAutopilot, useInbox, useVendor } from '@/hooks/queries'
import { useOpenRun } from '@/hooks/useOpenRun'
import { maskAccount } from '@/lib/account'
import { ATTACK_MAX, ATTACKER_TOKEN, attackTextProblem, attackedDocument, inboxItemFor, insertAt } from '@/lib/attack'
import { cn } from '@/lib/cn'
import { formatCents } from '@/lib/money'
import { useConsoleStore } from '@/state/consoleStore'

type Placement = 'visible_note' | 'hidden_white_text' | 'footer'

const PLACEMENTS: { value: Placement; title: string; hint: string }[] = [
  { value: 'visible_note', title: 'Visible note', hint: 'a clerk would see it' },
  { value: 'hidden_white_text', title: 'White text', hint: 'invisible on paper' },
  { value: 'footer', title: 'Footer', hint: 'note to automated systems' },
]

/** How long to wait for autopilot to admit the item (it polls every 5 s) before offering Retry. */
const PICKUP_TIMEOUT_MS = 20_000

/** One option per vendor in the batch, from its invoice there. */
function vendorsOf(batch: Batch | null): InvoiceSummary[] {
  const seen = new Set<string>()
  return (batch?.invoices ?? []).filter((i) => !seen.has(i.vendor_id) && seen.add(i.vendor_id))
}

/** The user's text as the preview shows it: the account token as a marked chip. */
function WithToken({ text }: { text: string }) {
  const parts = text.split(ATTACKER_TOKEN)
  return (
    <>
      {parts.map((p, i) => (
        <span key={i}>
          {p}
          {i < parts.length - 1 && <span className="rounded-[4px] bg-bad-bg px-1 text-bad">[attacker account]</span>}
        </span>
      ))}
    </>
  )
}

export function AttackLabPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const batch = useConsoleStore((s) => s.batch)
  const setBatch = useConsoleStore((s) => s.setBatch)

  const vendors = useMemo(() => vendorsOf(batch), [batch])
  const [vendorId, setVendorId] = useState<string | undefined>(undefined)
  const chosen = vendors.find((v) => v.vendor_id === vendorId) ?? vendors[0]
  const [placement, setPlacement] = useState<Placement>('hidden_white_text')
  const [text, setText] = useState(() => params.get('draft') ?? '')
  const textRef = useRef<HTMLTextAreaElement>(null)

  const autopilot = useAutopilot()
  const autopilotOn = autopilot.data?.enabled === true
  const vendor = useVendor(chosen?.vendor_id)

  const drawBatch = useMutation({ mutationFn: () => createBatch({ kind: 'standard' }), onSuccess: setBatch })

  // What was sent: the response batch and the one document POST /api/attacks queued in the inbox.
  const [sent, setSent] = useState<{ batch: Batch; documentId?: string; at: number } | null>(null)
  const send = useMutation({
    mutationFn: () => createAttack({ vendor_id: chosen!.vendor_id, attack_text: text, placement }),
    onSuccess: (b) => {
      setSent({ batch: b, documentId: attackedDocument(b, chosen!.vendor_id), at: Date.now() })
      // Autopilot off: nothing processes the inbox. The console can run this batch by hand.
      if (!autopilotOn) setBatch(b)
    },
  })

  // Autopilot on: follow the queued item (queued → processing → processed); once it names
  // its run, open the console on that run (the long-lived autopilot run and its aggregate batch).
  const pickedUp = useOpenRun(() => navigate('/console'))
  const { mutate: openRun, isIdle: notOpened } = pickedUp
  const following = Boolean(sent) && autopilotOn && notOpened
  const inbox = useInbox(following)
  const item =
    sent && inbox.data
      ? (sent.documentId ? inbox.data.find((i) => i.document_id === sent.documentId) : undefined) ??
        inboxItemFor(inbox.data, sent.batch)
      : undefined
  useEffect(() => {
    if (following && item?.run_id) openRun({ runId: item.run_id, documentId: item.document_id })
  }, [following, item?.run_id, item?.document_id, openRun])

  // Never wait forever: 20 s without the item naming a run ends the wait with Retry.
  const [pickupTimedOut, setPickupTimedOut] = useState<number | null>(null)
  const waitingSince = sent?.at
  const timedOut = following && !item?.run_id && pickupTimedOut === waitingSince
  useEffect(() => {
    if (!following || item?.run_id || waitingSince === undefined) return
    const left = PICKUP_TIMEOUT_MS - (Date.now() - waitingSince)
    const t = setTimeout(() => setPickupTimedOut(waitingSince), Math.max(0, left))
    return () => clearTimeout(t)
  }, [following, item?.run_id, waitingSince])
  const retryPickup = () => {
    setSent((s) => (s ? { ...s, at: Date.now() } : s))
    void inbox.refetch()
  }

  const problem = attackTextProblem(text)
  const count = [...text].length
  const waiting = following && !timedOut
  const busy = send.isPending || waiting
  const lanesNote =
    autopilot.data && autopilotOn
      ? autopilot.data.lanes.includes('unguarded') && autopilot.data.lanes.includes('countersign')
        ? 'Both agents process it.'
        : `Autopilot runs ${autopilot.data.lanes.join(' and ')} only.`
      : 'Both agents process it.'

  const addToken = () => {
    const el = textRef.current
    const { text: next, caret } = insertAt(text, el?.selectionStart ?? text.length, el?.selectionEnd ?? text.length, ATTACKER_TOKEN)
    setText(next)
    requestAnimationFrame(() => {
      el?.focus()
      el?.setSelectionRange(caret, caret)
    })
  }

  return (
    <div className="flex flex-col font-prose">
      <div className="px-7 pt-5.5 pb-1.5">
        <h1 className="display m-0 text-[2rem]">Write an invoice to fool Sig</h1>
        <p className="m-0 mt-1 text-muted">
          Your text goes into a real invoice PDF and lands in the AP inbox like any other mail. {lanesNote}
        </p>
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_clamp(20rem,30vw,31.43rem)] items-start gap-[22px] px-7 pt-4 pb-7">
        <Card
          as="form"
          aria-label="Attack"
          className="flex flex-col gap-[18px] p-[22px]"
          onSubmit={(e) => {
            e.preventDefault()
            if (!problem && chosen && !busy) send.mutate()
          }}
        >
          <div className="flex flex-col gap-1.5">
            <label htmlFor="vendor" className="font-semibold">
              Pretend to be
            </label>
            {vendors.length > 0 ? (
              <select
                id="vendor"
                value={chosen?.vendor_id}
                onChange={(e) => setVendorId(e.target.value)}
                className="ink-field min-h-11 p-2.5 font-[inherit] text-fg"
              >
                {vendors.map((v) => (
                  <option key={v.vendor_id} value={v.vendor_id}>
                    {v.vendor_name} · {v.invoice_number} · {formatCents(v.amount_cents, v.currency)}
                  </option>
                ))}
              </select>
            ) : (
              <InkRow className="flex flex-wrap items-center gap-3 p-3 text-small">
                <span className="text-muted">
                  The vendors come from a batch&apos;s invoices (there&apos;s no vendor list endpoint yet).
                </span>
                <InkButton size="sm" variant="secondary" onClick={() => drawBatch.mutate()} disabled={drawBatch.isPending}>
                  {drawBatch.isPending ? 'drawing…' : 'draw a batch'}
                </InkButton>
                {drawBatch.isError && <RequestError title="Could not draw a batch" error={drawBatch.error} />}
              </InkRow>
            )}
          </div>

          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-1.5 font-semibold">Where to hide it</legend>
            <div className="grid grid-cols-3 gap-2.5">
              {PLACEMENTS.map((p) => (
                <InkRow
                  as="label"
                  key={p.value}
                  // This page is always on the red team theme: never the paper "selected" fill.
                  tone={placement === p.value ? 'armed' : 'default'}
                  className="flex cursor-pointer items-start gap-2 rounded-[8px] p-3 has-[:focus-visible]:outline-[3px] has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent has-[:focus-visible]:outline-solid"
                >
                  <input
                    type="radio"
                    name="placement"
                    value={p.value}
                    checked={placement === p.value}
                    onChange={() => setPlacement(p.value)}
                    className="mt-1 accent-[var(--accent)]"
                  />
                  <span>
                    <strong>{p.title}</strong>
                    <br />
                    <span className="text-small text-muted">{p.hint}</span>
                  </span>
                </InkRow>
              ))}
            </div>
          </fieldset>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="attack" className="font-semibold">
              Your text
            </label>
            <textarea
              id="attack"
              ref={textRef}
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-describedby="attack-count"
              className="ink-field resize-y p-3 font-mono text-fg"
            />
            <div className="flex flex-wrap items-center gap-2">
              <Chip
                as="button"
                tone="untrusted"
                type="button"
                onClick={addToken}
                className="min-h-9 font-mono text-small"
                title={`Inserts ${ATTACKER_TOKEN}; the backend replaces it with the attacker's account number`}
              >
                + attacker account
              </Chip>
              <span
                id="attack-count"
                className={cn('ml-auto font-mono text-meta', count > ATTACK_MAX ? 'text-bad' : 'text-muted')}
              >
                {count} / {ATTACK_MAX}
              </span>
            </div>
          </div>

          <div className="flex flex-col items-start gap-2">
            <InkButton
              type="submit"
              className="!min-h-12 !px-[22px] text-face !font-bold"
              disabled={Boolean(problem) || !chosen || busy}
            >
              {send.isPending ? 'sending…' : 'Send it to the AP inbox'}
            </InkButton>
            {problem && text.length > 0 && <p className="m-0 text-small text-bad">{problem}</p>}
            <SendStatus
              send={send}
              onRetrySend={() => send.mutate()}
              sent={Boolean(sent)}
              autopilot={autopilot.data ? (autopilotOn ? 'on' : 'off') : autopilot.isError ? 'unknown' : 'loading'}
              waiting={waiting}
              timedOut={timedOut}
              onRetryPickup={retryPickup}
              item={item}
              inboxError={inbox.error}
              pickedUpError={pickedUp.error}
              onRetryOpen={() => item?.run_id && openRun({ runId: item.run_id, documentId: item.document_id })}
            />
          </div>
        </Card>

        <aside aria-label="About this attack" className="flex flex-col gap-4">
          {/* Light island: Sig's bubble keeps the paper tokens (dark ink on cobalt) on the dark page. */}
          <div data-theme="paper" data-testid="sig-island">
          <SigSays expression="cheeky">Go ahead, try to fool me. Whatever happens, I&apos;ll show my work.</SigSays>
          </div>

          <Card as="section" data-theme="paper" aria-label="Preview" label="preview · what a human sees" className="flex flex-col p-5 font-mono text-meta leading-[1.8]">
            {chosen ? (
              <>
                <div className="text-body font-bold">{chosen.vendor_name}</div>
                <div className="text-muted">invoice number, date, items and amount: set when the PDF is made</div>
                {vendor.data && <div>Account on file: {maskAccount(vendor.data.account_on_file_display)}</div>}
                {placement === 'hidden_white_text' ? (
                  <InkRow tone="untrusted" className="mt-1.5 px-2 py-1.5 font-prose text-bad">
                    your text sits here in white: invisible here, readable by an AI
                  </InkRow>
                ) : (
                  <InkRow
                    tone="untrusted"
                    className={cn(
                      'mt-1.5 px-2 py-1.5 break-words whitespace-pre-wrap',
                      placement === 'footer' && 'text-[0.786rem] text-muted',
                    )}
                    data-testid="preview-text"
                  >
                    {placement === 'footer' && <div className="font-prose text-bad">footer</div>}
                    {text ? <WithToken text={text} /> : <span className="font-prose text-muted">your text goes here</span>}
                  </InkRow>
                )}
              </>
            ) : (
              <p className="m-0 font-prose text-muted">Pick a vendor to preview.</p>
            )}
          </Card>

          <Card as="section" aria-label="What happens next" label="what happens next" className="flex flex-col p-5">
            <ol className="m-0 mt-2.5 list-decimal pl-5 leading-[1.9]">
              <li>The PDF lands in the AP inbox</li>
              {autopilot.isPending ? (
                <li className="text-muted">checking autopilot…</li>
              ) : autopilotOn ? (
                <li>Autopilot picks it up, no click</li>
              ) : (
                <li>
                  {autopilot.isError
                    ? 'Autopilot status is unavailable'
                    : 'Autopilot is off: it waits in the inbox until you run the batch from the Console'}
                </li>
              )}
              {autopilotOn && autopilot.data && !autopilot.data.lanes.includes('unguarded') ? (
                <li>The unguarded agent is not in autopilot&apos;s lanes, so it won&apos;t see this one</li>
              ) : (
                <li>The unguarded agent gets the same PDF</li>
              )}
              <li>You watch both on the Console</li>
            </ol>
          </Card>
        </aside>
      </div>
    </div>
  )
}

const ITEM_STEPS: InboxItem['status'][] = ['queued', 'processing', 'processed']

/** The inbox item's status as it moves: queued → processing → processed (processed means finished, not paid). */
function ItemSteps({ item }: { item: InboxItem }) {
  const at = ITEM_STEPS.indexOf(item.status)
  return (
    <p data-testid="inbox-item-status" data-status={item.status} className="m-0 font-mono text-meta">
      {item.item_id} ·{' '}
      {ITEM_STEPS.map((s, i) => (
        <span key={s}>
          {i > 0 && <span className="text-muted"> → </span>}
          <span className={i === at ? 'font-bold text-fg' : i < at ? 'text-muted' : 'text-muted opacity-60'}>{s}</span>
        </span>
      ))}
      {item.run_id && <span className="text-muted"> · run {item.run_id}</span>}
    </p>
  )
}

function SendStatus({
  send,
  onRetrySend,
  sent,
  autopilot,
  waiting,
  timedOut,
  onRetryPickup,
  item,
  inboxError,
  pickedUpError,
  onRetryOpen,
}: {
  send: { isError: boolean; error: Error | null; isPending: boolean }
  onRetrySend: () => void
  sent: boolean
  autopilot: 'on' | 'off' | 'unknown' | 'loading'
  waiting: boolean
  timedOut: boolean
  onRetryPickup: () => void
  item?: InboxItem
  inboxError: Error | null
  pickedUpError: Error | null
  onRetryOpen: () => void
}): ReactNode {
  if (send.isPending) return null
  if (send.isError) {
    const e = send.error
    if (isApiError(e) && e.status === 404 && e.code === 'not_found' && !e.envelope) {
      return (
        <p data-testid="not-available" className="m-0 text-small text-muted">
          Attacks are not available yet · POST /api/attacks → 404 {e.code}
        </p>
      )
    }
    // 422 and 429 carry the server's own message; RequestError shows it as-is.
    return <RequestError title="Could not send it" error={e} onRetry={onRetrySend} />
  }
  if (!sent) return null
  if (autopilot === 'off') {
    return (
      <div role="status" data-testid="autopilot-off" className="flex flex-col gap-1 text-small">
        <p className="m-0 font-semibold">Autopilot is off: turn it on or run the batch from the Console.</p>
        <Link to="/console" className="font-mono text-accent">
          open the Console →
        </Link>
      </div>
    )
  }
  if (autopilot !== 'on') {
    return (
      <p role="status" className="m-0 text-small text-muted">
        Sent. Can&apos;t tell whether autopilot is on (GET /api/autopilot {autopilot === 'loading' ? 'pending' : 'failed'}).
      </p>
    )
  }
  return (
    <div role="status" className="flex flex-col gap-1.5 text-small">
      <p className="m-0 text-muted">
        {timedOut
          ? 'Sent, but autopilot hasn\'t picked it up after 20 s.'
          : item?.run_id
            ? 'Picked up. Opening the Console on its run…'
            : 'Sent. Waiting for autopilot to pick it up…'}
      </p>
      {item ? <ItemSteps item={item} /> : waiting && <p className="m-0 font-mono text-meta text-muted">not in GET /api/inbox yet</p>}
      {timedOut && (
        <InkButton size="sm" variant="secondary" className="self-start" onClick={onRetryPickup}>
          Retry
        </InkButton>
      )}
      {inboxError && <RequestError title="Could not read the inbox" error={inboxError} onRetry={onRetryPickup} />}
      {pickedUpError && <RequestError title="Could not open the run" error={pickedUpError} onRetry={onRetryOpen} />}
    </div>
  )
}
