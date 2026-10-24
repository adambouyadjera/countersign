import { expect, test, type Locator } from '@playwright/test'

/** WCAG contrast of an element's text against the background it actually sits on (ancestors blended, opacity applied). */
async function contrast(el: Locator): Promise<number> {
  return el.evaluate((node) => {
    const parse = (c: string) => {
      const m = c.match(/rgba?\(([^)]+)\)/)
      const p = (m?.[1] ?? '0,0,0,0').split(',').map(Number)
      return { r: p[0]!, g: p[1]!, b: p[2]!, a: p[3] ?? 1 }
    }
    type C = ReturnType<typeof parse>
    const blend = (top: C, under: C): C => ({
      r: top.r * top.a + under.r * (1 - top.a),
      g: top.g * top.a + under.g * (1 - top.a),
      b: top.b * top.a + under.b * (1 - top.a),
      a: 1,
    })
    const lum = ({ r, g, b }: C) => {
      const f = (v: number) => ((v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    }
    const chain: Element[] = []
    for (let n: Element | null = node; n; n = n.parentElement) chain.push(n)
    let bg: C = { r: 255, g: 255, b: 255, a: 1 }
    let opacity = 1
    for (const n of chain.reverse()) {
      const cs = getComputedStyle(n)
      const c = parse(cs.backgroundColor)
      if (c.a > 0) bg = blend(c, bg)
      opacity *= Number(cs.opacity)
    }
    const fgRaw = parse(getComputedStyle(node).color)
    const fg = blend({ ...fgRaw, a: fgRaw.a * opacity }, bg)
    const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x)
    return (hi! + 0.05) / (lo! + 0.05)
  })
}

test('/attack: the selected placement title and Sig’s line are readable on the dark theme (4.5:1)', async ({ page }) => {
  await page.goto('/attack')
  await page.getByRole('button', { name: 'draw a batch' }).click()
  const white = page.getByLabel(/White text/)
  await white.check()

  const card = page.locator('label[data-ink-row]').filter({ has: white })
  await expect(card).toHaveAttribute('data-ink-row', 'armed')
  const title = card.locator('strong')
  expect(await contrast(title)).toBeGreaterThanOrEqual(4.5)

  const sigLine = page.getByTestId('sig-island').locator('[data-sig-says] .font-prose')
  await expect(sigLine).toBeVisible()
  expect(await contrast(sigLine)).toBeGreaterThanOrEqual(4.5)

  // The disabled send button, too.
  const send = page.locator('form button[type="submit"]')
  await expect(send).toBeDisabled()
  expect(await contrast(send)).toBeGreaterThanOrEqual(4.5)
})

test('/attack: placement cards work from the keyboard and show a focus ring', async ({ page }) => {
  await page.goto('/attack')
  await page.getByRole('button', { name: 'draw a batch' }).click()
  const white = page.getByLabel(/White text/)
  await white.focus()
  await page.keyboard.press('ArrowLeft')
  const visible = page.getByLabel(/Visible note/)
  await expect(visible).toBeChecked()
  await expect(visible).toBeFocused()
  const card = page.locator('label[data-ink-row]').filter({ has: visible })
  await expect(card).toHaveAttribute('data-ink-row', 'armed')
  await expect(card).toHaveCSS('outline-style', 'solid')
})
