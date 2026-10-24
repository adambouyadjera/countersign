import { useEffect, useRef, type MouseEvent } from 'react'
import type { PlannerTranscript } from '@/api/types'
import { Card, InkButton, InkRow } from '@/components/ui'
import { usePlannerTranscript } from '@/hooks/queries'
import { QueryView } from './Unavailable'

/** "What the planner saw": every planner call's exact prompt and response, plus the leak guard. */
export function PlannerTranscriptDialog({ runId, onClose }: { runId: string; onClose: () => void }) {
  const q = usePlannerTranscript(runId)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-fg/30 p-8" onClick={onClose}>
      <Card
        role="dialog"
        aria-modal="true"
        aria-labelledby="planner-title"
        onClick={(e: MouseEvent) => e.stopPropagation()}
        className="flex max-h-full w-full max-w-[64rem] flex-col gap-3 overflow-hidden p-5"
      >
        <div className="flex items-baseline gap-3">
          <h2 id="planner-title" className="display m-0 text-lead">
            What the planner saw
          </h2>
          <span className="font-mono text-meta text-muted">run {runId}</span>
          <InkButton ref={closeRef} variant="secondary" size="sm" onClick={onClose} className="ml-auto font-mono">
            close
          </InkButton>
        </div>
        <div className="min-h-0 overflow-y-auto">
          <QueryView query={q} what="the planner transcript" endpoint={`GET /api/runs/${runId}/planner_transcript`}>
            {(t: PlannerTranscript) => (
              <div className="flex flex-col gap-4">
                <p className={t.leak_guard.matches === 0 ? 'm-0 text-good' : 'm-0 font-bold text-bad'} data-testid="leak-guard">
                  leak guard: {t.leak_guard.strings_checked} document strings and {t.leak_guard.canaries_checked}{' '}
                  {t.leak_guard.canaries_checked === 1 ? 'canary' : 'canaries'} checked against the prompt ·{' '}
                  {t.leak_guard.matches} found
                </p>
                {t.calls.map((c) => (
                  <section key={c.call_id} aria-label={`Planner call ${c.call_id}`} className="flex flex-col gap-2">
                    <p className="m-0 font-mono text-meta text-muted">
                      {c.call_id} · {c.model} · prompt {c.prompt_hash.slice(0, 12)}
                    </p>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <div className="eyebrow">prompt</div>
                        <InkRow as="pre" className="m-0 max-h-[50vh] overflow-auto p-3 font-mono text-meta whitespace-pre-wrap">
                          {c.prompt}
                        </InkRow>
                      </div>
                      <div>
                        <div className="eyebrow">response</div>
                        <InkRow as="pre" className="m-0 max-h-[50vh] overflow-auto p-3 font-mono text-meta whitespace-pre-wrap">
                          {c.response}
                        </InkRow>
                      </div>
                    </div>
                  </section>
                ))}
              </div>
            )}
          </QueryView>
        </div>
      </Card>
    </div>
  )
}
