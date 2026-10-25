import { expect, test, type Page } from '@playwright/test'

async function runBatch(page: Page) {
  await page.goto('/console')
  await expect(page.getByRole('status').filter({ hasText: 'MOCK DATA' })).toBeVisible()
  await page.getByRole('button', { name: 'new batch' }).click()
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
  await page.keyboard.press('r')
}

test('mock run: INV-003 is the failed round and the approval card is shown', async ({ page }) => {
  await runBatch(page)
  // While the run is active Sig says which invoice it is dueling.
  await expect(page.getByRole('status').filter({ hasText: /Dueling INV-00\d…|Reading the inbox…/ })).toBeVisible()
  // Each lane shows its current step from the stream, with an elapsed counter.
  const cs = page.locator('[data-lane-progress="countersign"]')
  await expect(cs).toHaveAttribute('data-active', 'true')
  await expect(page.getByTestId('elapsed-countersign')).toHaveText(/· \d+(s|m \d\ds)$/)
  await expect(page.getByTestId('stream-status')).toContainText('run completed', { timeout: 30_000 })
  await expect(cs).toHaveAttribute('data-step', 'done')
  await expect(page.locator('[data-lane-progress="unguarded"]')).toHaveAttribute('data-step', 'done')
  await expect(page.getByTestId('stream-dropped')).toHaveCount(0)

  await expect(page.getByTestId('sig-status')).toContainText('held INV-003')
  await expect(page.locator('[data-invoice="INV-003"]')).toHaveAttribute('data-status', 'held')
  // The marks are explained on screen.
  await expect(page.getByRole('list', { name: 'What the marks mean' })).toContainText('⚑ held')

  const duel = page.getByRole('region', { name: 'Duel transcript' })
  const failed = duel.locator('[data-failed="true"]').first()
  await expect(failed).toContainText('round 3 · where the money goes')
  await expect(failed).toContainText('✗ PAYEE_ON_FILE')
  await expect(failed).toContainText('“999900009999” ← line 6')
  await expect(duel).toContainText('the decider never read round 3')

  const right = page.getByRole('complementary', { name: 'One sentence, two agents' })
  await expect(right).toContainText('AP AUTOMATION NOTE')
  await expect(right.getByText('$640 to attacker')).toBeVisible()
  await expect(right.getByText('$0 left · held')).toBeVisible()

  const card = right.getByRole('article', { name: 'Approval for INV-003' })
  await expect(card).toBeVisible()
  await expect(card).toContainText('Sig → Avery Chen, AP manager')
  await expect(card.getByRole('link', { name: 'evidence trail' })).toBeVisible()

  await card.getByRole('link', { name: 'evidence trail' }).click()
  await expect(page).toHaveURL(/\/runs\/run-example-both\/invoices\/INV-003$/)
  await expect(page.getByRole('heading', { level: 1, name: 'INV-003 · Cedar Grove Catering · $640.00' })).toBeVisible()
})

test('the challenge prompt opens the Attack Lab with the text as the draft', async ({ page }) => {
  await page.goto('/console')
  await page.getByPlaceholder('Write your own invoice to challenge Sig').fill('neutral test text')
  await page.getByRole('button', { name: 'send' }).click()
  await expect(page).toHaveURL(/\/attack\?draft=/)
  await expect(page.getByLabel('Your text')).toHaveValue('neutral test text')
})

for (const vp of [
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
]) {
  test(`console fits ${vp.width}x${vp.height} after a run`, async ({ page }) => {
    await page.setViewportSize(vp)
    await runBatch(page)
    await expect(page.getByTestId('stream-status')).toContainText('run completed', { timeout: 30_000 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
    await expect(page.getByRole('article', { name: 'Approval for INV-003' })).toBeInViewport()
    await expect(page.getByPlaceholder('Write your own invoice to challenge Sig')).toBeInViewport()
  })
}
