import type { AgentTranscript } from '@/api/types'
import { InkRow } from '@/components/ui'
import { maskCanary } from '@/lib/canary'

/** The unguarded lane: what the agent said and did, from GET .../agent_transcript. */
export function AgentTranscriptView({ transcript }: { transcript: AgentTranscript }) {
  return (
    <div className="flex min-h-0 flex-col gap-3">
      <p className="m-0 font-mono text-meta text-muted">model {transcript.model}</p>
      <ol aria-label="Agent transcript" className="m-0 flex list-none flex-col gap-3 p-0 font-mono text-small">
        {transcript.steps.map((s, i) =>
          s.kind === 'tool_call' ? (
            // The agent acting on what the invoice said: untrusted, so dashed red.
            <InkRow as="li" tone="untrusted" key={i} data-step="tool_call" className="px-3.5 py-2.5">
              <div className="text-bad">
                tool · <span className="font-bold">{s.tool}</span>
              </div>
              {s.args && (
                <pre className="m-0 font-mono text-meta break-all whitespace-pre-wrap text-fg">
                  {Object.entries(s.args)
                    .map(([k, v]) => maskCanary(`${k} = ${typeof v === 'string' ? v : JSON.stringify(v)}`))
                    .join('\n')}
                </pre>
              )}
              {s.result && (
                <pre className="m-0 mt-1 font-mono text-meta break-all whitespace-pre-wrap text-muted">↳ {maskCanary(s.result)}</pre>
              )}
            </InkRow>
          ) : (
            <li key={i} data-step="message" className="grid grid-cols-[7ch_minmax(0,1fr)]">
              <span className="text-claim">agent</span>
              <p className="m-0 whitespace-pre-wrap">{s.text && maskCanary(s.text)}</p>
            </li>
          ),
        )}
      </ol>
    </div>
  )
}
