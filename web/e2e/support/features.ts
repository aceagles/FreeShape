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

/** The y (page pixels) to drag the Drag handle to: the line above the row `above`, or just below the last row. */
async function rollbackTargetY(page: Page, above: string | RegExp | null): Promise<number> {
  if (above === null) {
    const last = await page.locator('.panel-row[data-feature]').last().boundingBox()
    if (!last) throw new Error('no rows')
    return last.y + last.height + 4
  }
  const row = await featureRow(page, above).boundingBox()
  if (!row) throw new Error(`no feature row ${String(above)}`)
  return row.y + 1
}

/**
 * Drag the rollback bar's Drag handle to the line above the row `above`, or below the last row. `by` is the pointer:
 * 'mouse' holds the button with the real mouse; 'touch' and 'pen' are PointerEvents dispatched on the handle with that
 * pointerType (WebKit under Playwright has no touch or pen device), one frame apart so the panel draws each move.
 */
export async function dragRollbackTo(page: Page, above: string | RegExp | null, by: 'mouse' | 'touch' | 'pen' = 'mouse'): Promise<void> {
  const handle = await page.getByTestId('rollback-handle').boundingBox()
  if (!handle) throw new Error('no Drag handle')
  const y = await rollbackTargetY(page, above)
  const x = handle.x + handle.width / 2
  const y0 = handle.y + handle.height / 2
  if (by === 'mouse') {
    await page.mouse.move(x, y0)
    await page.mouse.down()
    await page.mouse.move(x, y, { steps: 6 })
    await page.mouse.up()
    return
  }
  await page.evaluate(
    async ({ kind, x, y0, y }) => {
      const handle = document.querySelector('[data-testid="rollback-handle"]') as HTMLElement
      const frame = () => new Promise<void>((r) => requestAnimationFrame(() => r()))
      const send = (type: string, at: number, pressed: boolean) =>
        handle.dispatchEvent(
          new PointerEvent(type, { bubbles: true, cancelable: true, composed: true, pointerId: 7, pointerType: kind, isPrimary: true, clientX: x, clientY: at, button: type === 'pointermove' ? -1 : 0, buttons: pressed ? 1 : 0, pressure: pressed ? 0.5 : 0 }),
        )
      send('pointerdown', y0, true)
      await frame()
      for (let i = 1; i <= 6; i++) {
        send('pointermove', y0 + ((y - y0) * i) / 6, true)
        await frame()
      }
      send('pointerup', y, false)
      await frame()
    },
    { kind: by, x, y0, y },
  )
}
