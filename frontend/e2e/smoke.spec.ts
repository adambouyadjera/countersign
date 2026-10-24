import { expect, test, type Page } from '@playwright/test'

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text())
  })
  page.on('pageerror', (err) => errors.push(err.message))
  return errors
}

const viewports = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
]

const PAGES = [
  { path: '/', heading: 'Sig · AP clerk' },
  { path: '/bank-change', heading: 'Bank-change letter' },
  { path: '/runs/run-example-both/invoices/INV-003', heading: 'INV-003 · Cedar Grove Catering · $640.00' },
  { path: '/attack', heading: 'Write an invoice to fool Sig' },
  { path: '/books', heading: 'The books agree with the bank' },
  { path: '/proof', heading: 'How we know it works' },
  { path: '/audit', heading: 'Ask the auditor' },
]

for (const { path, heading } of PAGES) {
  test(`${path} loads with the header, the MOCK DATA badge and no console errors`, async ({ page }) => {
    const errors = collectConsoleErrors(page)
    await page.goto(path)
    await expect(page.getByRole('link', { name: 'Countersign home' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await expect(page.getByRole('status').filter({ hasText: 'MOCK DATA' })).toBeVisible()
    await page.waitForLoadState('networkidle')
    expect(errors).toEqual([])
  })

  for (const vp of viewports) {
    test(`${path} has no horizontal scroll at ${vp.width}x${vp.height}`, async ({ page }) => {
      await page.setViewportSize(vp)
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow).toBeLessThanOrEqual(0)
    })
  }
}

test('an unknown address says so through Sig', async ({ page }) => {
  await page.goto('/no-such-page')
  await expect(page.getByRole('heading', { level: 1, name: 'Not found' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'back to the console' })).toBeVisible()
})

test('header nav: six sections, health on the right', async ({ page }) => {
  await page.goto('/')
  const nav = page.getByRole('navigation', { name: 'Main' })
  await expect(nav).toHaveText(/console\s*case files\s*attack lab\s*books\s*proof\s*audit/)
  await expect(nav.getByRole('link', { name: 'console' })).toHaveAttribute('aria-current', 'page')
  await nav.getByRole('link', { name: 'books' }).click()
  await expect(page).toHaveURL(/\/books$/)
  const health = page.getByRole('list', { name: 'Service health' })
  for (const name of ['nessie', 'elastic', 'anthropic', 'gemini', 'bitdeer']) {
    await expect(health.locator(`[data-service="${name}"]`)).toHaveAttribute('data-status', 'offline')
  }
  await expect(health).toContainText('offline')
})
