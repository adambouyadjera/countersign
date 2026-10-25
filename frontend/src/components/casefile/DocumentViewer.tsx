import { useEffect, useRef } from 'react'
import { documentPdfUrl } from '@/api/client'
import type { DocumentText, SourceSpan } from '@/api/types'
import { cn } from '@/lib/cn'
import { marksByLine, splitLine, type LineMark } from '@/lib/caseFile'
import { maskCanary } from '@/lib/canary'
import { isMockMode } from '@/lib/env'

export interface DocumentViewerProps {
  doc: DocumentText
  /** Spans to mark: extracted fields are underlined, instruction-like text highlighted. */
  spans: { span: SourceSpan; kind: LineMark['kind'] }[]
  /** "page:line" of the line to highlight (picked in the graph). */
  selected?: string
}

/** The invoice as received: GET /api/documents/{id}, line-numbered. */
export function DocumentViewer({ doc, spans, selected }: DocumentViewerProps) {
  const ref = useRef<HTMLOListElement>(null)
  useEffect(() => {
    if (!selected) return
    const el = ref.current?.querySelector<HTMLElement>(`[data-line-key="${selected}"]`)
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [selected])

  return (
    <>
      <ol ref={ref} aria-label="Document lines" className="m-0 list-none p-0 font-mono text-[0.893rem] leading-[2]">
        {doc.pages.flatMap((page) => {
          const marks = marksByLine(spans, doc.document_id, page.page)
          return page.lines.map((l) => {
            const key = `${page.page}:${l.line}`
            const lineMarks = marks.get(l.line) ?? []
            const instruction = lineMarks.some((m) => m.kind === 'instruction')
            const isSelected = key === selected
            return (
              <li
                key={key}
                data-line-key={key}
                data-line={l.line}
                data-instruction={instruction ? 'true' : undefined}
                aria-current={isSelected ? 'true' : undefined}
                className={cn(
                  'flex px-4',
                  instruction ? 'bg-bad-bg text-bad' : isSelected && 'bg-[color-mix(in_srgb,var(--accent)_9%,white)]',
                  // An outline shows on any background, including an instruction line's red.
                  isSelected && 'outline-2 -outline-offset-2 outline-accent outline-solid',
                )}
              >
                <span className={cn('w-8 shrink-0 select-none', instruction ? 'text-bad' : 'text-muted')}>
                  {doc.pages.length > 1 ? `${page.page}.${l.line}` : l.line}
                </span>
                <span className="min-w-0 break-words">
                  {splitLine(maskCanary(l.text), lineMarks).map((p, i) =>
                    p.kind === 'field' ? (
                      <span
                        key={i}
                        data-mark="field"
                        title="extracted value · untrusted"
                        className="underline decoration-bad decoration-dashed decoration-[1.5px] underline-offset-4"
                      >
                        {p.text}
                      </span>
                    ) : p.kind === 'instruction' ? (
                      <mark key={i} data-mark="instruction" title="instruction-like text · kept from the planner" className="bg-transparent text-bad">
                        {p.text}
                      </mark>
                    ) : (
                      <span key={i}>{p.text}</span>
                    ),
                  )}
                </span>
              </li>
            )
          })
        })}
      </ol>
      {isMockMode() ? (
        <p className="m-0 px-4 pt-2.5 font-prose text-meta text-muted">original PDF: not in the mock fixtures</p>
      ) : (
        <a href={documentPdfUrl(doc.document_id)} target="_blank" rel="noreferrer" className="block px-4 pt-2.5 font-prose text-accent">
          open original PDF
        </a>
      )}
    </>
  )
}
