import type { Locator, Page } from '@playwright/test'

/** A feature row of the panel, by its text. */
export const featureRow = (page: Page, name: string | RegExp): Locator => page.locator('.panel-row[data-feature]', { hasText: name })

/** Hold a feature row with the mouse until its menu opens (500 ms), then let go. */
export async function longPressFeature(page: Page, name: string | RegExp): Promise<void> {
  const box = await featureRow(page, name).boundingBox()
  if (!box) throw new Error(`no feature row ${String(name)}`)
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.getByRole('menu').waitFor()
  await page.mouse.up()
}

/** Long press a row and tap an item of its menu. */
export async function featureMenu(page: Page, name: string | RegExp, item: string): Promise<void> {
  await longPressFeature(page, name)
  await page.getByRole('menuitem', { name: item, exact: true }).click()
}

/** Drag the rollback bar's Drag handle (a mouse, with the button held) to the line above the row `above`, or below the last row. */
export async function dragRollbackTo(page: Page, above: string | RegExp | null): Promise<void> {
  const handle = await page.getByTestId('rollback-handle').boundingBox()
  if (!handle) throw new Error('no Drag handle')
  let y: number
  if (above === null) {
    const last = await page.locator('.panel-row[data-feature]').last().boundingBox()
    if (!last) throw new Error('no rows')
    y = last.y + last.height + 4
  } else {
    const row = await featureRow(page, above).boundingBox()
    if (!row) throw new Error(`no feature row ${String(above)}`)
    y = row.y + 1
  }
  const x = handle.x + handle.width / 2
  await page.mouse.move(x, handle.y + handle.height / 2)
  await page.mouse.down()
  await page.mouse.move(x, y, { steps: 6 })
  await page.mouse.up()
}
