import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { lastFour, maskAccount } from '@/lib/account'
import { AccountNumber } from './AccountNumber'

describe('account masking', () => {
  it('keeps only the last four digits', () => {
    expect(lastFour('021000021 4401 9932')).toBe('9932')
    expect(maskAccount('021000021 4401 9932')).toBe('•••• 9932')
    expect(maskAccount('8890-3317')).toBe('•••• 3317')
  })

  it('handles values with no digits', () => {
    expect(maskAccount('n/a')).toBe('••••')
  })
})

describe('<AccountNumber>', () => {
  it('is masked by default and never shows the full number', () => {
    render(<AccountNumber value="000123456789" />)
    const el = screen.getByLabelText('Account ending 6789')
    expect(el).toHaveTextContent('•••• 6789')
    expect(el).not.toHaveTextContent('000123456789')
  })

  it('reveals on hover and focus, re-masks on leave and blur', () => {
    render(<AccountNumber value="000123456789" />)
    const el = screen.getByLabelText('Account ending 6789')
    fireEvent.mouseEnter(el)
    expect(el).toHaveTextContent('000123456789')
    fireEvent.mouseLeave(el)
    expect(el).toHaveTextContent('•••• 6789')
    fireEvent.focus(el)
    expect(el).toHaveTextContent('000123456789')
    fireEvent.blur(el)
    expect(el).toHaveTextContent('•••• 6789')
  })

  it('does not reveal when revealable is false', () => {
    render(<AccountNumber value="000123456789" revealable={false} />)
    const el = screen.getByLabelText('Account ending 6789')
    fireEvent.mouseEnter(el)
    expect(el).toHaveTextContent('•••• 6789')
  })
})
