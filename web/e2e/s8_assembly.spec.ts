import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { gizmoOf, openAssemblyTab, readAssembly, settledAssembly } from './support/assembly.ts'
import { sendPointer } from './support/pointer.ts'

// Slice S8 gate (card I8.3) against `freeshape serve`: the hinge's Assembly tab lists its three instances, the eye hides
// one, and a drag of the free leaf's gizmo moves it and one undo puts it back. Edits stay in the server's memory; the
// next openApp closes the Document and throws them away.

test.afterEach(async ({ page }) => closeAll(page.request))

const LEAF = 'I2'
const ARM = 'I3'

async function pose(page: Page, id: string): Promise<number[][]> {
  return (await readAssembly(page)).placements[id]!
}
const differs = (a: number[][], b: number[][]) => a.some((row, i) => row.some((v, j) => Math.abs(v - b[i]![j]!) > 0.05))

test('the Assembly tab shows the hinge instances, the eye hides one, and a gizmo drag moves the leaf until undo', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  const first = await openAssemblyTab(page, 3)
  expect([first.instances, first.drawn, first.hidden]).toEqual([3, 3, 0])
  await expect(page.getByTestId('instance-list').locator('li')).toHaveCount(3)

  // the eye hides one instance and shows it again
  const name = (await page.locator(`[data-instance-row="${ARM}"] .row-name`).innerText()).trim()
  await page.getByRole('button', { name: `Hide ${name}`, exact: true }).click()
  await expect.poll(async () => (await readAssembly(page)).drawn).toBe(2)
  expect((await readAssembly(page)).drawnIds).not.toContain(ARM)
  await page.getByRole('button', { name: `Show ${name}`, exact: true }).click()
  await expect.poll(async () => (await readAssembly(page)).drawn).toBe(3)

  // select the free leaf; its gizmo appears
  await page.locator(`[data-instance="${LEAF}"]`).click()
  await page.getByTestId('gizmo').waitFor()
  await settledAssembly(page)
  const before = await pose(page, LEAF)
  const base = await pose(page, 'I1')

  // a quarter turn of the finger about the gizmo's origin on the ring along the leaf's Z (the pin)
  const g = await gizmoOf(page)
  const ring = g.axes[2]!.turn!
  const o = g.origin
  const dx = ring.x - o.x
  const dy = ring.y - o.y
  const at = (k: number) => {
    const ang = (Math.PI / 2) * (k / 8)
    return { x: o.x + dx * Math.cos(ang) - dy * Math.sin(ang), y: o.y + dx * Math.sin(ang) + dy * Math.cos(ang) }
  }
  const steps = []
  for (let k = 0; k <= 8; k++) steps.push({ phase: k === 0 ? ('down' as const) : ('move' as const), kind: 'touch' as const, id: 5, ...at(k) })
  steps.push({ phase: 'up' as const, kind: 'touch' as const, id: 5, ...at(8) })
  await sendPointer(page, steps)
  await expect.poll(async () => (await readAssembly(page)).gizmo?.active ?? null).toBeNull()
  await expect.poll(async () => differs(await pose(page, LEAF), before), { timeout: 20_000 }).toBe(true)
  expect(differs(await pose(page, 'I1'), base)).toBe(false) // the fixed base stays

  // one undo on the engine brings the leaf back
  const doc = await idOf(page.request, 'Hinge')
  expect((await page.request.post(`/documents/${doc}/tabs/T2/edit`, { data: { op: 'undo' } })).ok()).toBe(true)
  await expect.poll(async () => differs(await pose(page, LEAF), before), { timeout: 10_000 }).toBe(false)
})
