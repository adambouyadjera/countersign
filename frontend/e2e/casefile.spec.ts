import { expect, test } from '@playwright/test'

const CASE = '/runs/run-example-both/invoices/INV-003'

test('open INV-003 from the console, click the instruction line node, see the line highlighted', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'new batch' }).click()
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
  await page.keyboard.press('r')
  await expect(page.getByTestId('stream-status')).toContainText('run completed', { timeout: 30_000 })
  await page.getByRole('article', { name: 'Approval for INV-003' }).getByRole('link', { name: 'evidence trail' }).click()
  await expect(page).toHaveURL(new RegExp(`${CASE}$`))

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('INV-003 · Cedar Grove Catering · $640.00')
  await expect(page.getByTestId('outcome-badge')).toHaveText('HELD · BANK_DETAILS_MISMATCH')
  await expect(page.getByTestId('decision-line')).toContainText("I didn't pay this one.")

  // The fixture invoice's instruction-like text is on line 7 (the mockup's sample put it on line 31).
  const lines = page.getByRole('list', { name: 'Document lines' })
  const line7 = lines.locator('[data-line="7"]')
  await expect(line7).toHaveAttribute('data-instruction', 'true')
  await expect(line7).not.toHaveAttribute('aria-current', 'true')

  const source = page.getByRole('region', { name: 'Provenance graph' }).getByRole('group', { name: 'Source' })
  const node = source.getByRole('button', { name: /^line 7/ })
  await expect(node).toHaveAttribute('data-trust', 'untrusted')
  await node.click()
  await expect(line7).toHaveAttribute('aria-current', 'true')
  await expect(line7).toBeInViewport()
  await expect(lines.locator('[aria-current="true"]')).toHaveCount(1)
  await expect(page.locator('[data-node="node-INV-003-trusted-po"]')).toHaveClass(/opacity-35/)
})

test('switch lanes: the unguarded lane shows the agent transcript instead of the graph', async ({ page }) => {
  await page.goto(CASE)
  await expect(page.getByRole('region', { name: 'Provenance graph' })).toBeVisible()
  await page.getByRole('navigation', { name: 'Lane' }).getByRole('link', { name: 'unguarded lane' }).click()
  await expect(page).toHaveURL(/\?lane=unguarded$/)
  const transcript = page.getByRole('list', { name: 'Agent transcript' })
  await expect(transcript).toBeVisible()
  await expect(transcript).toContainText('nessie.create_transfer')
  await expect(page.getByRole('region', { name: 'Provenance graph' })).toHaveCount(0)
  await page.getByRole('navigation', { name: 'Lane' }).getByRole('link', { name: 'countersign lane' }).click()
  await expect(page.getByRole('region', { name: 'Provenance graph' })).toBeVisible()
})

test('header switcher picks who you act as; the planner transcript opens and closes', async ({ page }) => {
  await page.goto(CASE)
  const actingAs = page.getByRole('combobox')
  await expect(actingAs).toHaveValue('person-ap-manager')
  await actingAs.selectOption('person-controller')
  await expect(actingAs).toHaveValue('person-controller')

  await page.getByRole('button', { name: 'what the planner saw →' }).click()
  const dialog = page.getByRole('dialog', { name: 'What the planner saw' })
  await expect(dialog.getByTestId('leak-guard')).toContainText('0 found')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

test('the case file fits 1280x720 and 1920x1080 without horizontal scroll', async ({ page }) => {
  for (const vp of [
    { width: 1280, height: 720 },
    { width: 1920, height: 1080 },
  ]) {
    await page.setViewportSize(vp)
    await page.goto(CASE)
    await expect(page.getByTestId('decision-line')).toContainText("I didn't pay")
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  }
})
