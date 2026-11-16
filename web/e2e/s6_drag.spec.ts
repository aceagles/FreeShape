import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { featureRow } from './support/features.ts'
import { penStroke, sendPointer } from './support/pointer.ts'
import { readViewport } from './support/viewport.ts'

// Slice S6, the drag arrows of the Extrude dialog (card I6.4), in the real client against `freeshape serve`: the preview
// carries an arrow with a round Drag handle and a value label; the pen drags the handle, the depth and the preview follow,
// and Accept commits the dragged depth.

test.afterEach(async ({ page }) => closeAll(page.request))

const revisionOnServer = async (page: Page): Promise<number> => {
  const id = await idOf(page.request, 'Bracket')
  return ((await (await page.request.get(`/documents/${id}/tabs`)).json()).tabs as Array<{ revision: number }>)[0]!.revision
}

const handle = (page: Page) => page.locator('.viewport-manipulators [data-part="handle"]')
const label = (page: Page) => page.locator('.viewport-manipulators [data-part="label"]')

/** The handle's centre in canvas coordinates (the overlay fills the canvas). */
async function handleAt(page: Page): Promise<{ x: number; y: number }> {
  const h = handle(page)
  await expect(h).toHaveCount(1)
  return { x: Number(await h.getAttribute('cx')), y: Number(await h.getAttribute('cy')) }
}

async function openExtrude(page: Page): Promise<void> {
  await openApp(page)
  await featureRow(page, 'Extrude 1').dblclick()
  await expect(page.getByRole('dialog', { name: 'Extrude 1' })).toBeVisible()
  // the preview of the open dialog carries the arrow
  await expect(label(page)).toHaveText('40 mm')
}

test('the pen drags the depth arrow: the label and the preview follow, and Accept commits the depth', async ({ page }) => {
  await openExtrude(page)
  const rev = await revisionOnServer(page)
  const first = (await readViewport(page)).preview!
  const from = await handleAt(page)
  // the arrow runs from its base to the handle: the pen moves 40 px further along that line
  const [bx, by] = (await page.locator('.viewport-manipulators path').first().getAttribute('d'))!.match(/-?[\d.]+/g)!.map(Number)
  const len = Math.hypot(from.x - bx!, from.y - by!)
  const along = (px: number) => ({ x: from.x + ((from.x - bx!) / len) * px, y: from.y + ((from.y - by!) / len) * px })
  await sendPointer(page, [
    { phase: 'down', kind: 'pen', id: 10, ...from },
    ...[1, 2, 3, 4].map((i) => ({ phase: 'move' as const, kind: 'pen' as const, id: 10, ...along(10 * i) })),
  ])
  // mid-drag: the label has moved off 40 and the arrow is still held
  await expect(label(page)).not.toHaveText('40 mm')
  const mid = await label(page).textContent()
  await sendPointer(page, [{ phase: 'up', kind: 'pen', id: 10, ...along(40) }])
  const depth = Number(/^([\d.]+) mm$/.exec(mid!)![1])
  expect(depth).toBeGreaterThan(40)
  // the dialog's Depth field follows (the drag closes the pad, which would still hold the old text), and the preview is a newer one than the open dialog's
  await expect(page.getByRole('button', { name: new RegExp(`^Depth, ${depth} mm`) })).toBeVisible()
  await page.waitForFunction((n) => (window.__freeshapeViewport?.preview ?? 0) > n, first)
  // nothing is committed until Accept
  expect(await revisionOnServer(page)).toBe(rev)
  await page.locator('.dialog-header').getByRole('button', { name: 'Accept', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.waitForFunction((r) => (window.__freeshapeViewport?.revision ?? 0) > r && window.__freeshapeViewport?.preview === null, rev)
  await expect(page.locator('.viewport-manipulators [data-part="handle"]')).toHaveCount(0)
  expect(await revisionOnServer(page)).toBeGreaterThan(rev)
  // the committed feature has the dragged depth: its dialog opens with it
  await featureRow(page, 'Extrude 1').dblclick()
  await expect(label(page)).toHaveText(`${depth} mm`)
})

test('a pen drag that starts off the handle leaves the depth alone', async ({ page }) => {
  await openExtrude(page)
  const from = await handleAt(page)
  await penStroke(page, { x: from.x - 200, y: from.y + 150 }, { x: from.x - 200, y: from.y + 50 })
  await expect(label(page)).toHaveText('40 mm')
})

test('a tap on the value label opens the pad on Depth', async ({ page }) => {
  await openExtrude(page)
  // the pad is on Depth when the dialog opens; move it away, then bring it back by the label
  await page.getByTestId('pad').getByRole('button', { name: 'Enter', exact: true }).click()
  await expect(page.getByTestId('pad')).toHaveCount(0)
  const rect = page.locator('.viewport-manipulators rect')
  const num = async (n: string) => Number(await rect.getAttribute(n))
  const at = { x: (await num('x')) + (await num('width')) / 2, y: (await num('y')) + (await num('height')) / 2 }
  await sendPointer(page, [
    { phase: 'down', kind: 'pen', id: 11, ...at },
    { phase: 'up', kind: 'pen', id: 11, ...at },
  ])
  await expect(page.getByTestId('pad')).toBeVisible()
})
