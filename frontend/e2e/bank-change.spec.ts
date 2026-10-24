import { expect, test } from '@playwright/test'
import replay from '../../contracts/fixtures/bank_change_replay.json' with { type: 'json' }

const label = `Recording of a live run · ${replay.recorded_at} · ${replay.run_id}`
const letter = replay.letter.requested_account_display
const onFile = replay.letter.account_on_file_display

test('bank-change screen shows the recorded run and replays it, always labelled as a recording', async ({ page }) => {
  await page.goto('/console')
  await page.getByRole('link', { name: 'Bank-change letter' }).click()
  await expect(page).toHaveURL(/\/bank-change$/)
  await expect(page.getByTestId('run-source-label')).toHaveText(label)

  const ung = page.getByRole('region', { name: 'Unguarded agent' })
  await expect(ung.getByTestId('letter-payment')).toContainText(`on file ${onFile} · paid ${letter}`)
  const cs = page.getByRole('region', { name: 'Countersign' })
  await expect(cs.getByTestId('reason-code')).toHaveText('BANK_DETAILS_MISMATCH')
  await expect(cs.getByTestId('no-payment')).toBeVisible()
  await expect(page.getByTestId('transfer-status')).toHaveCount(
    replay.lanes.unguarded.payments.length + replay.lanes.countersign.payments.length,
  )
  // Mock mode never starts a live run.
  await expect(page.getByRole('button', { name: 'Run live (~6 min)' })).toBeDisabled()

  await page.getByRole('button', { name: 'Replay recorded run' }).click()
  await expect(page.getByTestId('replay-progress')).toBeVisible()
  await expect(page.getByTestId('run-source-label')).toHaveText(label)
  // Steps forward on its own, then lands on the final state.
  await expect(ung.locator('[data-transfer]').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('button', { name: 'Replay recorded run' })).toBeVisible({ timeout: 20_000 })
  await expect(cs.getByTestId('no-payment')).toBeVisible()
  await expect(page.getByTestId('run-source-label')).toHaveText(label)
})

test('at 1280x720 both lanes fit without scrolling; the full letter is a toggle away', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto('/')
  await expect(page).toHaveURL(/\/bank-change$/)
  await expect(page.getByTestId('no-payment')).toBeVisible()
  for (const name of ['Unguarded agent', 'Countersign']) {
    const box = await page.getByRole('region', { name }).boundingBox()
    expect(box, name).not.toBeNull()
    expect(box!.y + box!.height, `${name} bottom`).toBeLessThanOrEqual(720)
  }
  const letter = page.getByRole('region', { name: 'Vendor letter' })
  await expect(letter).toHaveAttribute('data-card', 'untrusted')
  await expect(letter.getByTestId('letter-account')).toHaveText(replay.letter.requested_account_display)
  await expect(letter.getByRole('list', { name: 'Document lines' })).toHaveCount(0)
  const toggle = letter.getByRole('button', { name: 'Show full letter' })
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await expect(letter.getByRole('list', { name: 'Document lines' })).toBeVisible()
  await letter.getByRole('button', { name: 'Hide full letter' }).click()
  await expect(letter.getByRole('list', { name: 'Document lines' })).toHaveCount(0)
})
