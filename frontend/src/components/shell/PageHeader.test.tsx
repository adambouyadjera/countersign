import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageHeader } from './PageHeader'

describe('<PageHeader>', () => {
  it('renders the title as the page h1 with its dek', () => {
    render(<PageHeader folio="II" title="Attack Lab" dek="Write an invoice that lies." />)
    expect(screen.getByRole('heading', { level: 1, name: 'Attack Lab' })).toBeInTheDocument()
    expect(screen.getByText('Write an invoice that lies.')).toBeInTheDocument()
  })

  it('keeps the folio and blind seal out of the accessible name', () => {
    const { container } = render(<PageHeader folio="III" title="Ask the record" />)
    expect(screen.queryByText('III')).toHaveAttribute('aria-hidden')
    expect(container.querySelector('[data-blind-seal]')).toHaveAttribute('aria-hidden')
  })

  it('renders the aside slot', () => {
    render(<PageHeader folio="I" title="Console" aside={<button type="button">Run</button>} />)
    expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument()
  })
})
