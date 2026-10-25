import { expect, test, type Page } from '@playwright/test'

/** Gaps between the knob and the track's inner box (track box minus its border), in CSS px. */
async function gaps(page: Page) {
  return page.evaluate(() => {
    const track = document.querySelector<HTMLElement>('[data-testid=autopilot-track]')!
    const knob = document.querySelector<HTMLElement>('[data-testid=autopilot-knob]')!
    const t = track.getBoundingClientRect()
    const k = knob.getBoundingClientRect()
    const cs = getComputedStyle(track)
    const inner = {
      left: t.left + parseFloat(cs.borderLeftWidth),
      right: t.right - parseFloat(cs.borderRightWidth),
      top: t.top + parseFloat(cs.borderTopWidth),
      bottom: t.bottom - parseFloat(cs.borderBottomWidth),
    }
    return {
      boxSizing: cs.boxSizing,
      left: k.left - inner.left,
      right: inner.right - k.right,
      top: k.top - inner.top,
      bottom: inner.bottom - k.bottom,
      knobHeight: k.height,
      innerHeight: inner.bottom - inner.top,
    }
  })
}

async function expectInside(page: Page, on: boolean) {
  // Wait for the knob's slide to finish before measuring.
  await page.waitForTimeout(400)
  const g = await gaps(page)
  expect(g.boxSizing).toBe('border-box')
  expect(g.knobHeight).toBeLessThan(g.innerHeight)
  // Fully inside the inner box on every side.
  for (const side of ['left', 'right', 'top', 'bottom'] as const) expect(g[side], `${side} gap`).toBeGreaterThan(0)
  // The same gap to the border on the three sides the knob rests against; the fourth is the travel.
  const resting = on ? (['right', 'top', 'bottom'] as const) : (['left', 'top', 'bottom'] as const)
  for (const side of resting) expect(g[side], `${side} gap`).toBeCloseTo(2, 1)
  const travel = on ? g.left : g.right
  expect(travel, 'travel side').toBeGreaterThan(2)
}

for (const zoom of [1, 2]) {
  test.describe(`autopilot switch at ${zoom * 100}% zoom`, () => {
    // Browser zoom: CSS pixels scale by the zoom and the viewport shrinks by the same factor.
    test.use({ deviceScaleFactor: zoom, viewport: { width: Math.round(1440 / zoom), height: Math.round(900 / zoom) } })

    test('the knob stays inside the track with an even gap, off and on', async ({ page }) => {
      await page.goto('/console')
      const sw = page.getByRole('switch', { name: 'autopilot' })
      await expect(sw).toHaveAttribute('aria-checked', 'false')
      await expectInside(page, false)
      await page.getByTestId('autopilot-track').screenshot({ path: `test-results/autopilot-off-${zoom}x.png` })
      await sw.click()
      await expect(sw).toHaveAttribute('aria-checked', 'true')
      await expectInside(page, true)
      await page.getByTestId('autopilot-track').screenshot({ path: `test-results/autopilot-on-${zoom}x.png` })
    })
  })
}
