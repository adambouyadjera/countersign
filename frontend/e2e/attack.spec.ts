import { expect, test } from '@playwright/test'

// Neutral filler text: these tests check the flow, not the wording of an attack.
const TEXT = 'neutral test text'

test('attack lab: draw vendors, write, send; autopilot is off in mock, so the lab starts the run and the console plays it', async ({ page }) => {
  await page.goto('/attack')
  await expect(page.getByRole('heading', { level: 1, name: 'Write an invoice to fool Sig' })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'MOCK DATA' })).toBeVisible()
  await expect(page.getByText('Go ahead, try to fool me.', { exact: false })).toBeVisible()
  await expect(page.locator('[data-expression="cheeky"]')).toBeVisible()

  await page.getByRole('button', { name: 'draw a batch' }).click()
  await page.getByLabel('Pretend to be').selectOption('VEN-003')
  await page.getByLabel(/Visible note/).check()
  await page.getByLabel('Your text').fill(`${TEXT} `)
  await page.getByRole('button', { name: '+ attacker account' }).click()
  await expect(page.getByLabel('Your text')).toHaveValue(`${TEXT} {{ATTACKER_ACCOUNT}}`)
  await expect(page.getByTestId('preview-text')).toContainText('[attacker account]')
  await expect(page.getByText('Autopilot is off, so sending also starts the run')).toBeVisible()

  await page.getByRole('button', { name: 'Send it to the AP inbox' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('stream-status')).toContainText('run completed', { timeout: 30_000 })
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
})

test('countersign queue: header badge and drawer', async ({ page }) => {
  await page.goto('/attack')
  const button = page.getByRole('button', { name: /Countersign queue/ })
  // The mock fixture's only approval is resolved, so nothing is pending.
  await expect(button).toHaveAccessibleName('Countersign queue: 0 pending')
  await expect(page.getByTestId('queue-count')).toHaveText('0')
  await button.click()
  const drawer = page.getByRole('dialog', { name: 'Countersign queue' })
  await expect(drawer).toContainText('Nothing waiting for a countersign.')
  await page.keyboard.press('Escape')
  await expect(drawer).toHaveCount(0)
  await expect(button).toBeFocused()
})

test('attack lab fits 1280x720 and 1920x1080', async ({ page }) => {
  for (const vp of [
    { width: 1280, height: 720 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(vp)
    await page.goto('/attack')
    await page.getByRole('button', { name: 'draw a batch' }).click()
    await expect(page.getByLabel('Pretend to be')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
    await expect(page.getByRole('button', { name: 'Send it to the AP inbox' })).toBeInViewport()
  }
})
