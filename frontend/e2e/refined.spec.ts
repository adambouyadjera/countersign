import { expect, test, type Page } from '@playwright/test'

/** Elements with a 1px solid #D3CDC1 border on any side: the old plain grey box. */
async function greyBoxes(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = []
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const cs = getComputedStyle(el)
      for (const side of ['Top', 'Right', 'Bottom', 'Left'] as const) {
        const width = cs.getPropertyValue(`border-${side.toLowerCase()}-width`)
        const style = cs.getPropertyValue(`border-${side.toLowerCase()}-style`)
        const color = cs.getPropertyValue(`border-${side.toLowerCase()}-color`)
        if (width === '1px' && style === 'solid' && color === 'rgb(211, 205, 193)') {
          out.push(`${el.tagName.toLowerCase()}.${el.className.toString().slice(0, 60)} (${side})`)
          break
        }
      }
    }
    return out
  })
}

async function runBatch(page: Page) {
  await page.goto('/console')
  await page.getByRole('button', { name: 'new batch' }).click()
  await expect(page.getByRole('list', { name: 'Invoices in batch' }).getByRole('listitem')).toHaveCount(5)
  await page.keyboard.press('r')
  await expect(page.getByTestId('stream-status')).toContainText('run completed', { timeout: 30_000 })
}

test('console: no 1px solid #D3CDC1 border anywhere, and the cards are ink-outlined paper', async ({ page }) => {
  await runBatch(page)
  expect(await greyBoxes(page)).toEqual([])

  // The three console panels are Cards: 1.5px solid ink with the offset shadow.
  for (const name of ['Inbox', 'One sentence, two agents']) {
    const card = page.getByRole('complementary', { name })
    await expect(card).toHaveCSS('border-top-style', 'solid')
    await expect(card).toHaveCSS('border-top-color', 'rgb(26, 28, 34)')
    await expect(card).toHaveCSS('box-shadow', /rgb\(226, 220, 207\) 3px 3px 0px/)
  }
  const transcript = page.getByRole('region', { name: 'Duel transcript' })
  // Authored as 1.5px; at device-pixel-ratio 1 Chromium snaps it to 1px, so check colour and style instead.
  await expect(transcript).toHaveCSS('border-top-color', 'rgb(26, 28, 34)')
  await expect(transcript).toHaveCSS('border-top-style', 'solid')
  // The lost round is dashed red.
  await expect(transcript.locator('[data-failed="true"]').first()).toHaveCSS('border-top-style', 'dashed')
  // The selected inbox row has the cobalt outline.
  await expect(page.locator('[data-invoice="INV-003"] button')).toHaveCSS('border-top-color', 'rgb(36, 72, 200)')
  // Header: 2px ink rule. Page: dot grid.
  await expect(page.locator('header')).toHaveCSS('border-bottom', /2px solid rgb\(26, 28, 34\)/)
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).backgroundImage)).toContain('radial-gradient')
})

test('the other pages and the queue drawer have no 1px solid #D3CDC1 border either', async ({ page }) => {
  await runBatch(page)
  await page.getByRole('article', { name: 'Approval for INV-003' }).getByRole('link', { name: 'evidence trail' }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('INV-003 · Cedar Grove Catering · $640.00')
  expect(await greyBoxes(page)).toEqual([])
  for (const label of ['books', 'proof', 'audit', 'attack lab']) {
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label }).click()
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    expect(await greyBoxes(page), label).toEqual([])
  }
  await page.getByRole('button', { name: /Countersign queue/ }).click()
  await expect(page.getByRole('dialog', { name: 'Countersign queue' })).toBeVisible()
  expect(await greyBoxes(page)).toEqual([])
})

test('fonts: Bricolage Grotesque titles, Instrument Sans body, Space Mono labels', async ({ page }) => {
  await page.goto('/books')
  await expect(page.getByRole('heading', { level: 1 })).toHaveCSS('font-family', /Bricolage Grotesque/)
  await expect(page.getByTestId('books-verdict')).toHaveCSS('font-family', /Instrument Sans/)
  await expect(page.getByRole('navigation', { name: 'Main' })).toHaveCSS('font-family', /Space Mono/)
  await expect(page.locator('[data-card-label]').first()).toHaveCSS('font-family', /Space Mono/)
  expect(await page.evaluate(() => document.fonts.check('800 20px "Bricolage Grotesque"'))).toBe(true)
})
