import type { ReactNode } from 'react'

/**
 * The auditor's answer arrives as Markdown. This renders the subset it uses (headings,
 * bold, inline code, bullet/numbered lists, pipe tables, paragraphs) as React elements:
 * no HTML is ever injected, so the text can't add markup to the page.
 */

function inline(text: string, key: string): ReactNode[] {
  // `code` first, then **bold** inside the remaining text.
  return text.split(/(`[^`]+`)/g).flatMap((part, i) => {
    if (/^`[^`]+`$/.test(part)) {
      return [
        <code key={`${key}c${i}`} className="rounded-[4px] bg-inner px-1 font-mono text-[0.9em]">
          {part.slice(1, -1)}
        </code>,
      ]
    }
    return part.split(/(\*\*[^*]+\*\*)/g).map((seg, j) =>
      /^\*\*[^*]+\*\*$/.test(seg) ? <strong key={`${key}b${i}-${j}`}>{seg.slice(2, -2)}</strong> : seg,
    )
  })
}

const cells = (row: string) =>
  row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim())

const isTableRule = (row: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(row)

export function AnswerText({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const out: ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]!
    const k = `l${i}`
    if (line.trim() === '') {
      i++
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      out.push(
        <p key={k} className="m-0 mt-1 font-display text-lead font-extrabold">
          {inline(h[2]!, k)}
        </p>,
      )
      i++
      continue
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && isTableRule(lines[i + 1]!)) {
      const head = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i]!.trim().startsWith('|')) rows.push(cells(lines[i++]!))
      out.push(
        <div key={k} className="overflow-x-auto">
          <table className="w-full border-collapse text-small">
            <thead>
              <tr className="border-b-2 border-ink text-left text-muted">
                {head.map((c, j) => (
                  <th key={j} className="px-1.5 py-1.5 font-medium">
                    {inline(c, `${k}h${j}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="rule-dashed">
                  {r.map((c, j) => (
                    <td key={j} className="px-1.5 py-1.5 align-top">
                      {inline(c, `${k}r${ri}c${j}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }
    const listItem = /^\s*(?:[-*]|\d+\.)\s+/
    if (listItem.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: string[] = []
      while (i < lines.length && listItem.test(lines[i]!)) items.push(lines[i++]!.replace(listItem, ''))
      const List = ordered ? 'ol' : 'ul'
      out.push(
        <List key={k} className={ordered ? 'm-0 list-decimal pl-6' : 'm-0 list-disc pl-6'}>
          {items.map((it, j) => (
            <li key={j}>{inline(it, `${k}i${j}`)}</li>
          ))}
        </List>,
      )
      continue
    }
    // A paragraph: consecutive plain lines.
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i]!.trim() !== '' &&
      !/^(#{1,4})\s/.test(lines[i]!) &&
      !listItem.test(lines[i]!) &&
      !lines[i]!.trim().startsWith('|')
    ) {
      para.push(lines[i++]!)
    }
    if (para.length === 0) {
      // A stray table-looking line without a rule row: show it as text.
      para.push(lines[i++]!)
    }
    out.push(
      <p key={k} className="m-0">
        {inline(para.join(' '), k)}
      </p>,
    )
  }
  return <div className="flex flex-col gap-2.5 leading-relaxed">{out}</div>
}
