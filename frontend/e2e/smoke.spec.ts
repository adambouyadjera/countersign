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

for (const path of ['/', '/kit', '/redteam', '/audit']) {
  test(`${path} loads with no console errors`, async ({ page }) => {
    const errors = collectConsoleErrors(page)
    await page.goto(path)
    await expect(page.getByRole('link', { name: 'Countersign home' })).toBeVisible()
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

test('/kit shows every component', async ({ page }) => {
  await page.goto('/kit')
  for (const name of [
    'Button',
    'Panel',
    'LedgerRule',
    'HandleChip',
    'Money',
    'AccountNumber',
    'TrustBadge',
    'Seal',
    'StatusDot',
    'Drawer',
    'Kbd',
    'EmptyState',
    'ErrorState',
    'MockBadge',
  ]) {
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  await page.getByRole('button', { name: 'Open drawer' }).click()
  await expect(page.getByRole('dialog', { name: 'INV-003 held' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
