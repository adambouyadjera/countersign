import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SigSays } from '@/components/sig'
import { Card } from './Card'
import { CardLabel } from './CardLabel'
import { Chip } from './Chip'
import { InkButton } from './InkButton'
import { InkRow } from './InkRow'

describe('<Card>', () => {
  it('default tone: solid ink outline, offset shadow, folded corner', () => {
    render(<Card data-testid="c">x</Card>)
    const c = screen.getByTestId('c')
    expect(c).toHaveAttribute('data-card', 'default')
    expect(c).toHaveAttribute('data-folded', 'true')
    expect(c.className).toContain('border-solid')
    // The edge token is ink on paper (and red in the Attack Lab's red team theme).
    expect(c.className).toContain('border-[var(--card-edge)]')
    expect(c.className).toContain('shadow-[var(--card-shadow)]')
    expect(c.className).toContain('bg-[image:var(--card-fold)]')
  })

  it('untrusted tone: 1.5px dashed red on pale red, never folded', () => {
    render(
      <Card tone="untrusted" data-testid="c">
        x
      </Card>,
    )
    const c = screen.getByTestId('c')
    expect(c.className).toMatch(/border-\[1\.5px\] border-dashed border-bad bg-bad-bg/)
    expect(c).not.toHaveAttribute('data-folded')
    expect(c.className).not.toContain('card-fold')
  })

  it('good tone and foldedCorner={false}', () => {
    render(
      <Card tone="good" foldedCorner={false} data-testid="c">
        x
      </Card>,
    )
    const c = screen.getByTestId('c')
    expect(c).toHaveAttribute('data-card', 'good')
    expect(c.className).toContain('border-good')
    expect(c).not.toHaveAttribute('data-folded')
  })

  it('renders the label as a dark tab before the content, and keeps the element and aria props given', () => {
    render(
      <Card as="section" aria-label="Inbox" label="inbox · 5">
        <p>rows</p>
      </Card>,
    )
    const c = screen.getByRole('region', { name: 'Inbox' })
    expect(c.tagName).toBe('SECTION')
    const tab = c.firstElementChild!
    expect(tab).toHaveAttribute('data-card-label')
    expect(tab).toHaveTextContent('inbox · 5')
    expect(tab.className).toContain('bg-[var(--tab-bg)]')
  })

  it('has no label tab when none is given', () => {
    const { container } = render(<Card>x</Card>)
    expect(container.querySelector('[data-card-label]')).toBeNull()
  })
})

describe('<CardLabel>', () => {
  it('is a small mono uppercase tab', () => {
    render(<CardLabel>trial balance</CardLabel>)
    const t = screen.getByText('trial balance')
    expect(t.className).toContain('font-mono')
    expect(t.className).toContain('uppercase')
  })
})

describe('<InkButton>', () => {
  it.each([
    ['primary', /border-solid border-ink bg-accent text-on-accent shadow-\[var\(--button-shadow\)\]/],
    ['secondary', /border-solid border-\[var\(--inner-edge\)\] bg-card text-fg shadow-\[var\(--inner-shadow\)\]/],
    ['danger', /border-dashed border-bad bg-card text-bad/],
  ] as const)('%s variant', (variant, cls) => {
    render(<InkButton variant={variant}>Go</InkButton>)
    const b = screen.getByRole('button', { name: 'Go' })
    expect(b).toHaveAttribute('data-variant', variant)
    expect(b.className).toMatch(cls)
  })

  it('is a real button: type="button" by default, a visible focus ring, keyboard-activatable', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(<InkButton onClick={onClick}>Go</InkButton>)
    const b = screen.getByRole('button', { name: 'Go' })
    expect(b.tagName).toBe('BUTTON')
    expect(b).toHaveAttribute('type', 'button')
    expect(b.className).toContain('focus-visible:outline-accent')
    await user.tab()
    expect(b).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('disabled: not clickable and drops its shadow', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(
      <InkButton disabled onClick={onClick}>
        Go
      </InkButton>,
    )
    const b = screen.getByRole('button', { name: 'Go' })
    expect(b).toBeDisabled()
    expect(b.className).toContain('disabled:shadow-none')
    await user.click(b)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('submits forms when asked', () => {
    render(<InkButton type="submit">Send</InkButton>)
    expect(screen.getByRole('button', { name: 'Send' })).toHaveAttribute('type', 'submit')
  })
})

describe('<Chip> and <InkRow>', () => {
  it('a chip is an inline ink-outlined item; as="button" makes it a real button', () => {
    render(
      <>
        <Chip>plain</Chip>
        <Chip as="button" type="button" pill>
          ask this
        </Chip>
      </>,
    )
    expect(screen.getByText('plain').className).toContain('border-[var(--inner-edge)]')
    const b = screen.getByRole('button', { name: 'ask this' })
    expect(b.className).toContain('rounded-full')
    expect(b.className).toContain('focus-visible:outline-accent')
  })

  it.each([
    ['default', /border-solid border-\[var\(--inner-edge\)\] bg-inner/],
    ['untrusted', /border-dashed border-bad bg-bad-bg/],
    ['failed', /border-2 border-solid border-bad bg-bad-bg/],
    ['selected', /border-accent bg-bubble/],
  ] as const)('InkRow %s tone', (tone, cls) => {
    render(
      <InkRow tone={tone} data-testid="r">
        x
      </InkRow>,
    )
    expect(screen.getByTestId('r').className).toMatch(cls)
  })
})

describe('<SigSays> (refined)', () => {
  it('is the cobalt bubble with the squared corner, the face, the sig label and real text', () => {
    render(<SigSays expression="honest">My report card.</SigSays>)
    const bubble = document.querySelector('[data-sig-says]')!
    expect(bubble.className).toContain('bg-bubble')
    expect(bubble.className).toContain('[border-radius:var(--bubble-radius)]')
    expect(bubble.className).toContain('shadow-[var(--bubble-shadow)]')
    expect(bubble.querySelector('[data-expression="honest"]')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByText('sig')).toBeInTheDocument()
    expect(screen.getByText('My report card.')).toBeInTheDocument()
  })
})
