import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AnswerText } from './AnswerText'

const md = [
  '## Summary — run-1',
  '',
  '**Attack context:** placed in `hidden_white_text` across **12 invoices**.',
  '',
  '| Lane | Paid |',
  '|---|---:|',
  '| unguarded | 12 |',
  '| countersign | 0 |',
  '',
  '- first point',
  '- second point',
  '',
  '<img src=x onerror=alert(1)>',
].join('\n')

describe('<AnswerText>', () => {
  it('renders headings, bold, code, tables and lists without markdown symbols', () => {
    const { container } = render(<AnswerText text={md} />)
    expect(screen.getByText('Summary — run-1')).toBeInTheDocument()
    expect(screen.getByText('Attack context:').tagName).toBe('STRONG')
    expect(screen.getByText('hidden_white_text').tagName).toBe('CODE')
    expect(screen.getByRole('table')).toHaveTextContent('LanePaidunguarded12countersign0')
    expect(screen.getAllByRole('listitem').map((l) => l.textContent)).toEqual(['first point', 'second point'])
    expect(container.textContent).not.toMatch(/\*\*|##/)
  })

  it('never injects HTML from the answer', () => {
    const { container } = render(<AnswerText text={md} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})
