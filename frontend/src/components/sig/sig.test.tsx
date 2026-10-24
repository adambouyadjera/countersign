import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { sigFaceLines, type SigExpression } from './face'
import { SigFace } from './SigFace'
import { SigLogo } from './SigLogo'
import { SigSays } from './SigSays'

const README_EYES: Array<[SigExpression, string]> = [
  ['default', '◉ ◉'],
  ['serious', '▬ ▬'],
  ['cheeky', '◕ ◕'],
  ['pleased', '^ ^'],
  ['honest', '• •'],
  ['calm', '- -'],
]

describe('<SigFace>', () => {
  it.each(README_EYES)('draws the %s expression with %s eyes', (expression, eyes) => {
    const { container } = render(<SigFace expression={expression} />)
    const face = container.firstElementChild!
    expect(face).toHaveAttribute('aria-hidden', 'true')
    expect(face).toHaveAttribute('data-expression', expression)
    expect(face.textContent).toBe(`╭───╮│${eyes}│╰┬─┬╯`)
  })

  it('defaults to ◉ ◉', () => {
    const { container } = render(<SigFace />)
    expect(container.textContent).toContain('◉ ◉')
  })

  it('has a four-line large variant with feet, as on the console', () => {
    expect(sigFaceLines('serious', 'large')).toEqual([' ╭─────╮', ' │ ▬ ▬ │', ' ╰─┬─┬─╯', '  ═╧═╧═'])
  })

  it('keeps every line of a face the same width', () => {
    for (const [e] of README_EYES) {
      const widths = sigFaceLines(e).map((l) => [...l].length)
      expect(new Set(widths).size).toBe(1)
    }
  })
})

describe('<SigSays>', () => {
  it('shows the face, the sig label and the line as real text', () => {
    render(<SigSays expression="cheeky">Go ahead, try to fool me.</SigSays>)
    expect(screen.getByText('sig')).toBeInTheDocument()
    expect(screen.getByText('Go ahead, try to fool me.')).toBeInTheDocument()
    const face = document.querySelector('[data-expression]')!
    expect(face).toHaveAttribute('data-expression', 'cheeky')
    expect(face).toHaveAttribute('aria-hidden', 'true')
  })
})

describe('<SigLogo>', () => {
  it('renders the decorative mark and the wordmark', () => {
    const { container } = render(<SigLogo />)
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByText('countersign')).toBeInTheDocument()
  })
})
