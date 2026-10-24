import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MockBadge } from './MockBadge'

describe('<MockBadge>', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('is visible when VITE_USE_MOCK=1', () => {
    vi.stubEnv('VITE_USE_MOCK', '1')
    render(<MockBadge />)
    expect(screen.getByRole('status')).toHaveTextContent('MOCK DATA')
  })

  it.each(['0', ''])('is hidden when VITE_USE_MOCK=%j', (v) => {
    vi.stubEnv('VITE_USE_MOCK', v)
    render(<MockBadge />)
    expect(screen.queryByText('MOCK DATA')).toBeNull()
  })

  it('honours an explicit enabled prop', () => {
    vi.stubEnv('VITE_USE_MOCK', '0')
    render(<MockBadge enabled />)
    expect(screen.getByText('MOCK DATA')).toBeInTheDocument()
  })
})
