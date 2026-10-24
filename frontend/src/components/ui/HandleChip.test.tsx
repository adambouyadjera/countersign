import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HandleChip, type HandleTrust } from './HandleChip'

describe('<HandleChip>', () => {
  const cases: Array<[HandleTrust, RegExp]> = [
    ['trusted', /border-solid border-ink/],
    ['untrusted', /border-dashed border-taint/],
    ['corroborated', /border-solid border-verified/],
  ]

  it.each(cases)('renders the %s variant', (trust, cls) => {
    render(<HandleChip name="$INV-003.remit_account" trust={trust} />)
    const chip = screen.getByTitle(`$INV-003.remit_account (${trust})`)
    expect(chip).toHaveAttribute('data-trust', trust)
    expect(chip.className).toMatch(cls)
    expect(chip.className).toContain('font-mono')
    expect(chip).toHaveTextContent(`$INV-003.remit_account, ${trust}`)
  })

  it('emphasises the field after the last dot', () => {
    render(<HandleChip name="$INV-003.remit_account" trust="untrusted" />)
    expect(screen.getByText('remit_account')).toHaveClass('font-medium')
    expect(screen.getByText('$INV-003.')).toBeInTheDocument()
  })
})
