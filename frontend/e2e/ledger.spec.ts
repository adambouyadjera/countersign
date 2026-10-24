import { expect, test } from '@playwright/test'

test('books: balanced and reconciled per the API, so Sig is pleased', async ({ page }) => {
  await page.goto('/books')
  await expect(page.getByRole('heading', { level: 1, name: 'The books agree with the bank' })).toBeVisible()
  await expect(page.getByText('BALANCED', { exact: true })).toBeVisible()
  await expect(page.getByText('RECONCILED · $0.00 DIFFERENCE')).toBeVisible()
  await expect(page.locator('[data-expression="pleased"]')).toBeVisible()
  await expect(page.getByTestId('tb-total')).toContainText('26,040.00')
  await expect(page.getByTestId('recon-bank')).toHaveText('9 · $6,040')
  await expect(page.getByRole('list', { name: 'Journal entries' }).getByRole('listitem')).toHaveCount(3)
  await page.getByRole('button', { name: '7 more entries' }).click()
  await expect(page.getByRole('list', { name: 'Journal entries' }).getByRole('listitem')).toHaveCount(10)
})

test('proof: tiles start empty and fill from the eval and red-team jobs', async ({ page }) => {
  await page.goto('/proof')
  await expect(page.locator('[data-expression="honest"]')).toBeVisible()
  await expect(page.getByTestId('kpi-matches')).toHaveText('—')
  await page.getByRole('button', { name: 'run the answer-key eval' }).click()
  await expect(page.getByTestId('kpi-matches')).toHaveText('5 / 5')
  await expect(page.getByText('completed · 5 / 5')).toBeVisible()
  await expect(page.getByRole('list', { name: 'Misses' })).toHaveCount(0)
  await expect(page.getByText('None in this run.')).toBeVisible()
  await page.getByRole('button', { name: 'Run 40 new variants' }).click()
  await expect(page.getByTestId('kpi-fooled')).toHaveText('8 / 10')
  await expect(page.getByTestId('distinct-prompts')).toHaveText('1')
})

test('audit: ask, see the answer and ES|QL; the event log follows the current run', async ({ page }) => {
  await page.goto('/console')
  await page.getByRole('button', { name: 'new batch' }).click()
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
  await page.keyboard.press('r')
  await expect(page.getByTestId('stream-status')).toContainText('run completed', { timeout: 30_000 })
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'audit' }).click()

  await expect(page.locator('[data-expression="calm"]')).toBeVisible()
  await page.getByLabel('Question').fill('Why was INV-003 held?')
  await page.getByRole('button', { name: 'Ask', exact: true }).click()
  const answer = page.getByRole('article', { name: 'Answer' })
  await expect(answer).toContainText('INV-003 was held')
  await expect(answer.getByTestId('esql')).toContainText('FROM countersign-events')
  await expect(page.getByRole('list', { name: 'Latest events' }).getByRole('listitem').first()).toBeVisible()
})

test('console header: the autopilot switch flips via POST /api/autopilot', async ({ page }) => {
  await page.goto('/console')
  const sw = page.getByRole('switch', { name: 'autopilot' })
  await expect(sw).toHaveAttribute('aria-checked', 'false')
  await sw.click()
  await expect(sw).toHaveAttribute('aria-checked', 'true')
  // Off the console it is read-only and agrees.
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'books' }).click()
  await expect(page.getByTestId('autopilot')).toHaveText('autopilot on')
  await expect(page.getByRole('switch')).toHaveCount(0)
})

for (const path of ['/books', '/proof', '/audit']) {
  test(`${path} fits 1280x720 and 1920x1080`, async ({ page }) => {
    for (const vp of [
      { width: 1280, height: 720 },
      { width: 1920, height: 1080 },
    ]) {
      await page.setViewportSize(vp)
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
    }
  })
}
