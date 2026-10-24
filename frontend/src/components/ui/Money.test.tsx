import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Money } from './Money'

describe('<Money>', () => {
  it('renders formatted cents with tabular numerals', () => {
    render(<Money cents={123_456} />)
    const el = screen.getByLabelText('$1,234.56')
    expect(el).toHaveTextContent('$1,234.56')
    expect(el.className).toContain('tabular')
  })

  it('marks signed negatives in taint colour', () => {
    render(<Money cents={-500} signed />)
    expect(screen.getByLabelText('−$5.00').className).toContain('text-taint')
  })

  it('updates to the new value when not flowing', () => {
    const { rerender } = render(<Money cents={100} />)
    rerender(<Money cents={250} />)
    expect(screen.getByLabelText('$2.50')).toHaveTextContent('$2.50')
  })
})
