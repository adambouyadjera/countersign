import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HandleChip, type HandleTrust } from './HandleChip'

describe('<HandleChip>', () => {
  const cases: Array<[HandleTrust, RegExp]> = [
    ['trusted', /text-fg/],
    ['untrusted', /text-claim/],
    ['corroborated', /text-fg/],
  ]

  it.each(cases)('renders the %s variant', (trust, cls) => {
    render(<HandleChip name="$INV-003.remit_account" trust={trust} />)
    const chip = screen.getByTitle(`$INV-003.remit_account (${trust})`)
    expect(chip).toHaveAttribute('data-trust', trust)
    expect(chip.className).toMatch(cls)
    expect(chip.className).toContain('font-mono')
    expect(chip.className).toContain('bg-panel')
    expect(chip).toHaveTextContent(`$INV-003.remit_account, ${trust}`)
  })

  it('emphasises the field after the last dot', () => {
    render(<HandleChip name="$INV-003.remit_account" trust="untrusted" />)
    expect(screen.getByText('remit_account')).toHaveClass('font-medium')
    expect(screen.getByText('$INV-003.')).toBeInTheDocument()
  })

  it('shows the invoice number and field when given, with the full handle in the tooltip', () => {
    const name = '$inv_5f3c9a2e4b1d4e8a9c2f1a2b3c4d5e6f.remit_account_number'
    render(<HandleChip name={name} trust="untrusted" invoiceNumber="INV-003" />)
    const chip = screen.getByTitle(`${name} (untrusted)`)
    expect(chip).toHaveTextContent('INV-003 · remit_account_number, untrusted')
    expect(chip).not.toHaveTextContent('$inv_5f3c')
    expect(screen.getByText('remit_account_number')).toHaveClass('font-medium')
  })
})
