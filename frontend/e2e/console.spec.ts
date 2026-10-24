import { expect, test, type Page } from '@playwright/test'

async function startRun(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('status').filter({ hasText: 'MOCK DATA' })).toBeVisible()
  await page.getByRole('button', { name: 'New batch' }).click()
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
  await page.getByRole('button', { name: /Run batch/ }).click()
}

test('mock run plays out: Countersign pays 4 and holds 1; unguarded pays 1 to an unknown account', async ({ page }) => {
  await startRun(page)
  const countersign = page.getByRole('region', { name: 'Lane B: Countersign' })
  const unguarded = page.getByRole('region', { name: 'Lane A: Unguarded agent' })

  await expect(page.getByTestId('countersign-tally-paid')).toHaveText('4', { timeout: 30_000 })
  await expect(page.getByTestId('countersign-tally-held')).toHaveText('1')
  await expect(page.getByTestId('countersign-tally-unknown')).toHaveText('0')
  await expect(page.getByTestId('unguarded-tally-unknown')).toHaveText('1')
  await expect(page.getByTestId('unguarded-tally-paid')).toHaveText('5')

  await expect(countersign.getByRole('img', { name: 'Payment held seal' })).toBeVisible()
  await expect(countersign.getByText('Completed', { exact: true })).toBeVisible()
  await expect(unguarded.getByText('Completed', { exact: true })).toBeVisible()
  await expect(page.getByTestId('stream-status')).toContainText('run completed')

  const cedar = page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem').filter({ hasText: 'INV-003' })
  await expect(cedar.getByLabel('Unguarded: paid to unknown account')).toBeVisible()
  await expect(cedar.getByLabel(/Countersign: held/)).toBeVisible()
})

test('service health labels offline services', async ({ page }) => {
  await page.goto('/')
  const strip = page.getByLabel('Service health')
  for (const name of ['nessie', 'elastic', 'anthropic', 'gemini', 'bitdeer']) {
    await expect(strip.locator(`[data-service="${name}"]`)).toHaveAttribute('data-status', 'offline')
  }
  await expect(page.getByRole('region', { name: 'Ledger balances' }).getByText('bank · offline mode')).toBeVisible()
})

// Regenerates docs/console-mock.png. Not part of the normal run:
//   CONSOLE_SCREENSHOT=1 npx playwright test console --grep screenshot
test('screenshot of the console mid-run', async ({ page }) => {
  test.skip(!process.env.CONSOLE_SCREENSHOT, 'set CONSOLE_SCREENSHOT=1 to regenerate docs/console-mock.png')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await startRun(page)
  // Mid-run: the hold has landed but the Countersign lane has not finished.
  await expect(page.getByRole('img', { name: 'Payment held seal' })).toBeVisible({ timeout: 30_000 })
  await page.waitForTimeout(400) // let the stamp settle
  await page.screenshot({ path: 'docs/console-mock.png' })
})
