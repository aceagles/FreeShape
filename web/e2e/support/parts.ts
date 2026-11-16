import type { Locator, Page } from '@playwright/test'

/** A part row of the Parts list (the button that selects), by its text. */
export const partRow = (page: Page, name: string | RegExp): Locator => page.locator('.part-select[data-part]', { hasText: name })

/** The eye button of a part, by the part's name. */
export const partEye = (page: Page, name: string): Locator => page.getByRole('button', { name: new RegExp(`^(Hide|Show) ${name}$`) })

/** Hold a part row with the mouse until its menu opens (500 ms), then let go. */
export async function longPressPart(page: Page, name: string | RegExp): Promise<void> {
  const box = await partRow(page, name).boundingBox()
  if (!box) throw new Error(`no part row ${String(name)}`)
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.getByRole('menu').waitFor()
  await page.mouse.up()
}

/** Long press a part row and tap an item of its menu. */
export async function partMenu(page: Page, name: string | RegExp, item: string): Promise<void> {
  await longPressPart(page, name)
  await page.getByRole('menuitem', { name: item, exact: true }).click()
}
