import type { Page } from '@playwright/test'

/** Hold the tab button (the first one by default) until its menu opens (it does by itself after 500 ms of holding). */
export async function longPressTab(page: Page, which: 'first' | 'last' = 'first'): Promise<void> {
  const buttons = page.locator('.tab-button')
  const box = await (which === 'first' ? buttons.first() : buttons.last()).boundingBox()
  if (!box) throw new Error('no tab')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.getByRole('menu').waitFor()
  await page.mouse.up()
}
