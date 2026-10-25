import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createAttack, createBatch, createRun } from '@/api/client'
import { isApiError } from '@/api/errors'
import type { Batch, InvoiceSummary, Lane } from '@/api/types'
import { RequestError } from '@/components/console/RequestError'
import { SigSays } from '@/components/sig'
import { Card, Chip, InkButton, InkRow } from '@/components/ui'
import { useAutopilot, useInbox, useVendor } from '@/hooks/queries'
import { useOpenRun } from '@/hooks/useOpenRun'
import { maskAccount } from '@/lib/account'
import { ATTACK_MAX, ATTACKER_TOKEN, attackTextProblem, inboxItemFor, insertAt } from '@/lib/attack'
import { cn } from '@/lib/cn'
import { formatCents } from '@/lib/money'
import { useConsoleStore } from '@/state/consoleStore'

type Placement = 'visible_note' | 'hidden_white_text' | 'footer'

const PLACEMENTS: { value: Placement; title: string; hint: string }[] = [
  { value: 'visible_note', title: 'Visible note', hint: 'a clerk would see it' },
  { value: 'hidden_white_text', title: 'White text', hint: 'invisible on paper' },
  { value: 'footer', title: 'Footer', hint: 'note to automated systems' },
]

const RUN_LANES: Lane[] = ['unguarded', 'countersign']

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
  const setRun = useConsoleStore((s) => s.setRun)

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

  const [sent, setSent] = useState<Batch | null>(null)
  const startRun = useMutation({
    mutationFn: (b: Batch) => createRun({ batch_id: b.batch_id, lanes: RUN_LANES }),
    onSuccess: (run, b) => {
      setBatch(b)
      setRun(run)
      navigate('/console')
    },
  })
  const send = useMutation({
    mutationFn: () => createAttack({ vendor_id: chosen!.vendor_id, attack_text: text, placement }),
    onSuccess: (b) => {
      setSent(b)
      // With autopilot on, the backend starts the run from the inbox; otherwise we start it.
      if (!autopilotOn) startRun.mutate(b)
    },
  })

  // Autopilot on: wait for the attack's inbox item to name its run, then open the console on it.
  const waiting = Boolean(sent) && autopilotOn
  const inbox = useInbox(waiting)
  const item = sent && inbox.data ? inboxItemFor(inbox.data, sent) : undefined
  // Open the autopilot run with its aggregate batch (admitted inbox items), not the batch the attack came from.
  const pickedUp = useOpenRun(() => navigate('/console'))
  const { mutate: openRun, isIdle: notOpened } = pickedUp
  useEffect(() => {
    if (waiting && item?.run_id && notOpened) openRun(item.run_id)
  }, [waiting, item?.run_id, notOpened, openRun])

  const problem = attackTextProblem(text)
  const count = [...text].length
  const busy = send.isPending || startRun.isPending || waiting
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
              {send.isPending ? 'sending…' : startRun.isPending ? 'starting the run…' : 'Send it to the AP inbox'}
            </InkButton>
            {problem && text.length > 0 && <p className="m-0 text-small text-bad">{problem}</p>}
            <SendStatus
              send={send}
              startRun={startRun}
              waiting={waiting}
              itemLine={
                item ? `in the inbox as ${item.item_id} · ${item.status}${item.run_id ? ` · run ${item.run_id}` : ''}` : undefined
              }
              inboxError={inbox.error}
              pickedUpError={pickedUp.error}
              onStartMyself={() => sent && startRun.mutate(sent)}
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
                  {autopilot.isError ? 'Autopilot status is unavailable' : 'Autopilot is off'}, so sending also starts the
                  run
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

function SendStatus({
  send,
  startRun,
  waiting,
  itemLine,
  inboxError,
  pickedUpError,
  onStartMyself,
}: {
  send: { isError: boolean; error: Error | null; isSuccess: boolean }
  startRun: { isError: boolean; error: Error | null }
  waiting: boolean
  itemLine?: string
  inboxError: Error | null
  pickedUpError: Error | null
  onStartMyself: () => void
}): ReactNode {
  if (send.isError) {
    const e = send.error
    if (isApiError(e) && e.status === 404) {
      return (
        <p data-testid="not-available" className="m-0 text-small text-muted">
          Attacks are not available yet · POST /api/attacks → 404 {e.code}
        </p>
      )
    }
    return <RequestError title="Could not send it" error={e} />
  }
  if (startRun.isError) {
    return <RequestError title="Sent, but the run did not start" error={startRun.error} onRetry={onStartMyself} />
  }
  if (waiting) {
    return (
      <div role="status" className="text-small text-muted">
        <p className="m-0">Sent. Waiting for autopilot to pick it up{itemLine ? `: ${itemLine}` : '.'}</p>
        {(inboxError || pickedUpError) && (
          <div className="mt-1.5 flex flex-col gap-1.5">
            <RequestError
              title={inboxError ? 'Could not read the inbox' : 'Could not open the run'}
              error={inboxError ?? pickedUpError}
            />
            <InkButton size="sm" variant="secondary" className="self-start" onClick={onStartMyself}>
              start the run myself
            </InkButton>
          </div>
        )}
      </div>
    )
  }
  return null
}
