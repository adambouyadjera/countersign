import { expect, test } from '@playwright/test'
import replay from '../../contracts/fixtures/bank_change_replay.json' with { type: 'json' }

const label = `Recording of a live run · ${replay.recorded_at} · ${replay.run_id}`
const letter = replay.letter.requested_account_display
const onFile = replay.letter.account_on_file_display

test('bank-change screen shows the recorded run and replays it, always labelled as a recording', async ({ page }) => {
  await page.goto('/')
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
