import { expect, test } from '@playwright/test'
import { readAssembly } from './support/assembly.ts'
import { closeAll } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { hingeWithoutMates, mateDialog, mateRows, pickBothHoles } from './support/mates.ts'

// Slice S10, card I10.6: the mate dialog and the mate tools, on the served hinge Document, under WebKit, in Desktop mode. The two mates
// of the example are deleted from the Mates list, then a Revolute is made between the Base plate's pin hole and the Arm's, with the
// toolbar button; the view draws the preview while the dialog is open; Accept adds the mate and the list shows it. Every check polls the
// page: the server answers each pick and edit, the client then redraws. No pointer press lands within 12 px of the last click.
// 1920 x 1080: the dialog floats over the view's top left, and the pins must be clear of it for a click to reach them
test.use({ ...desktopMode, viewport: { width: 1920, height: 1080 } })
test.afterEach(async ({ page }) => closeAll(page.request))

const dialog = mateDialog

test('a Revolute between the Base plate\'s and the Arm\'s pin edges: the preview moves the Arm, Accept adds the mate to the list', async ({ page }) => {
  await hingeWithoutMates(page)
  const before = (await readAssembly(page)).placements['I2']!
  await page.locator('.desktop-toolbar [data-tool="m_revolute"]').click()
  await expect(dialog(page)).toBeVisible()
  await expect(dialog(page).getByRole('radio', { name: 'Revolute' })).toHaveAttribute('aria-checked', 'true')
  await expect(dialog(page).getByTestId('mate-help')).toContainText('Revolute')
  await expect(dialog(page).getByRole('group', { name: 'First mate connector' })).toHaveAttribute('data-active', '')
  // the dialog floats at (12, 12) of the view, and the toolbar's button shows pressed
  const box = (await dialog(page).boundingBox())!
  const view = (await page.getByTestId('shell-viewport').boundingBox())!
  expect(Math.abs(box.x - view.x - 12)).toBeLessThan(2)
  expect(Math.abs(box.y - view.y - 12)).toBeLessThan(2)
  await expect(page.locator('.desktop-toolbar [data-tool="m_revolute"]')).toHaveAttribute('aria-pressed', 'true')

  // the Base plate's pin hole, then the Arm's: each a circular edge, and the Second field is the active one after the first pick
  await pickBothHoles(page)
  // the view draws the preview: the Arm has moved onto the Base plate's pin, and the two ends of the mate are drawn whether J is on or not
  await expect.poll(async () => JSON.stringify((await readAssembly(page)).placements['I2'])).not.toBe(JSON.stringify(before))
  await expect.poll(async () => (await readAssembly(page)).mateEnds).toBe(2)
  await expect(dialog(page).getByTestId('mate-values')).toContainText('Value')
  await expect(dialog(page).getByTestId('mate-limits')).toBeDisabled()
  await expect(dialog(page).getByTestId('mate-error')).toHaveCount(0)
  // nothing is stored until Accept
  await expect(mateRows(page)).toHaveCount(0)

  await dialog(page).getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(dialog(page)).toHaveCount(0)
  await expect(mateRows(page)).toHaveCount(1)
  await expect(mateRows(page).first()).toContainText('Revolute 1')
  // the stored mate's two ends are drawn (J is on), and the dialog's preview ends are gone
  await expect.poll(async () => (await readAssembly(page)).mateEnds).toBe(2)
  // a double click on the row opens the dialog for that mate, with both of its ends in the fields; Esc closes it and the mate stays
  await mateRows(page).first().dblclick()
  await expect(dialog(page)).toBeVisible()
  await expect(dialog(page).getByRole('radio', { name: 'Revolute' })).toHaveAttribute('aria-checked', 'true')
  await expect(dialog(page).getByRole('group', { name: 'First mate connector' }).getByTestId('sel-chip')).toContainText('circular edge')
  await expect(dialog(page).getByRole('group', { name: 'Second mate connector' }).getByTestId('sel-chip')).toContainText('circular edge')
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
  await expect(mateRows(page)).toHaveCount(1)
})

test('Flip and an Offset typed in the dialog reach the preview, and Esc cancels without a mate', async ({ page }) => {
  await hingeWithoutMates(page)
  const start = JSON.stringify((await readAssembly(page)).placements['I2'])
  await page.keyboard.press('Shift+F')
  await expect(dialog(page)).toBeVisible()
  await expect(dialog(page).getByRole('radio', { name: 'Fastened' })).toHaveAttribute('aria-checked', 'true')
  await pickBothHoles(page)
  const was = JSON.stringify((await readAssembly(page)).placements['I2'])
  await dialog(page).getByRole('button', { name: /Flip/ }).click()
  await expect(dialog(page).getByRole('button', { name: /Flip/ })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(async () => JSON.stringify((await readAssembly(page)).placements['I2'])).not.toBe(was)
  const flipped = JSON.stringify((await readAssembly(page)).placements['I2'])
  const offset = dialog(page).getByRole('textbox', { name: 'Offset' })
  await offset.fill('5')
  await offset.press('Tab')
  await expect.poll(async () => JSON.stringify((await readAssembly(page)).placements['I2'])).not.toBe(flipped)
  await page.keyboard.press('Escape')
  await expect(dialog(page)).toHaveCount(0)
  await expect(mateRows(page)).toHaveCount(0)
  // the Assembly is as it was before the dialog: the Arm is back where it started
  await expect.poll(async () => JSON.stringify((await readAssembly(page)).placements['I2'])).toBe(start)
})
