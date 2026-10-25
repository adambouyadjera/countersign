import { expect, test, type Page } from '@playwright/test'

/** Open event-stream connections, as counted by the app's shared run-stream registry. */
const openStreams = (page: Page) =>
  page.evaluate(() => (window as unknown as { __countersignOpenStreams?: () => number }).__countersignOpenStreams?.() ?? -1)

async function navTo(page: Page, name: string, url: RegExp) {
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name }).click()
  await expect(page).toHaveURL(url)
}

test('at most one event stream is open while moving between pages', async ({ page }) => {
  await page.goto('/console')
  await page.getByRole('button', { name: 'new batch' }).click()
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
  await page.getByRole('button', { name: /run batch/ }).click()
  await expect(page.getByTestId('stream-status')).toContainText('streaming')
  expect(await openStreams(page)).toBe(1)

  await navTo(page, 'attack lab', /\/attack$/)
  // The Console unmounted: its stream is closed, not left open in the background.
  await expect.poll(() => openStreams(page)).toBe(0)

  await navTo(page, 'console', /\/console$/)
  await expect(page.getByTestId('stream-status')).toContainText(/streaming|run completed/)
  expect(await openStreams(page)).toBeLessThanOrEqual(1)

  await navTo(page, 'Bank-change letter', /\/bank-change$/)
  await expect(page.getByTestId('run-source-label')).toBeVisible()
  await expect.poll(() => openStreams(page)).toBe(0)

  await navTo(page, 'console', /\/console$/)
  await expect(page.getByTestId('stream-status')).toContainText(/streaming|run completed/)
  expect(await openStreams(page)).toBeLessThanOrEqual(1)
  expect(await openStreams(page)).toBe(1)
})
