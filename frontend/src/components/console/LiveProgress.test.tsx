import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { parseJsonl, runBothEventsText } from '@/api/mock/fixtures'
import { reduceEvents } from '@/state/runReducer'
import { LiveProgress } from './LiveProgress'

const events = parseJsonl(runBothEventsText)
// Mid-run: up to the plan, so the countersign lane is "checking policy".
const mid = reduceEvents(events.slice(0, events.findIndex((e) => e.type === 'plan.proposed') + 1))

function renderIt(props: Partial<Parameters<typeof LiveProgress>[0]> = {}) {
  return render(
    <LiveProgress
      lanes={[
        { lane: mid.lanes.unguarded, status: 'running' },
        { lane: mid.lanes.countersign, status: 'running' },
      ]}
      stream="open"
      lastEventAt={Date.now()}
      reconnect={() => {}}
      invoiceLabel={(id) => id}
      {...props}
    />,
  )
}

describe('<LiveProgress>', () => {
  it("shows each lane's current step with an elapsed counter", () => {
    renderIt()
    const cs = document.querySelector('[data-lane-progress="countersign"]')!
    expect(cs).toHaveAttribute('data-step', 'checking policy')
    expect(cs).toHaveTextContent('Countersign checking policy')
    expect(screen.getByTestId('elapsed-countersign').textContent).toMatch(/· (\d+s|\d+m \d\ds)$/)
    expect(screen.queryByTestId('still-working')).toBeNull()
  })

  it('says it is still working after 15 s without an event', () => {
    renderIt({ lastEventAt: Date.now() - 16_000 })
    expect(screen.getByTestId('still-working')).toHaveTextContent('Still working: live model calls can take a minute')
  })

  it('shows a dropped stream and reconnects on request', async () => {
    const reconnect = vi.fn()
    renderIt({ stream: 'reconnecting', reconnect })
    expect(screen.getByRole('alert')).toHaveTextContent("Lost the connection to the run's event stream.")
    await userEvent.setup().click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(reconnect).toHaveBeenCalledOnce()
  })

  it('waits for the first event of a run that has just started', () => {
    const empty = reduceEvents([])
    renderIt({ lanes: [{ lane: empty.lanes.countersign, status: 'running' }], lastEventAt: null })
    expect(document.querySelector('[data-lane-progress="countersign"]')).toHaveTextContent('waiting for the first event')
  })

  it('says the stream may have stopped when the backend is unreachable, even if the socket looks open', () => {
    renderIt({ backendUnreachable: true, lastEventAt: Date.now() - 60_000 })
    expect(screen.getByRole('alert')).toHaveTextContent("Can't reach the backend (GET /api/health is failing)")
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument()
    // Not "still working": we don't know that.
    expect(screen.queryByTestId('still-working')).toBeNull()
  })
})
