import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, idOf } from './support/docs.ts'
import { gizmoOf, openAssemblyTab, readAssembly, settledAssembly } from './support/assembly.ts'
import { sendPointer } from './support/pointer.ts'
import { viewCanvas } from './support/viewport.ts'
import type { PointerKind } from './support/pointer.ts'

// Slice S8, moving an instance by its Drag handle (card I8.2) against `freeshape serve`: select the hinge's free leaf,
// drag a rotation ring of its gizmo (the leaf is held by a revolute mate to the base, so it swings about the pin), and
// check that the view redraws from the server's reply and that ONE undo on the engine puts the leaf back. Edits stay in
// the server's memory; the next openApp closes the Document and throws them away.

test.afterEach(async ({ page }) => closeAll(page.request))

const LEAF = 'I2'

async function pose(page: Page): Promise<number[][]> {
  return (await readAssembly(page)).placements[LEAF]!
}
const differs = (a: number[][], b: number[][]) => a.some((row, i) => row.some((v, j) => Math.abs(v - b[i]![j]!) > 0.05))

async function selectLeaf(page: Page) {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 3)
  await page.locator(`[data-instance="${LEAF}"]`).click()
  await page.getByTestId('gizmo').waitFor()
  return settledAssembly(page)
}

test('a free leaf shows its gizmo and a fixed instance shows none', async ({ page }) => {
  await selectLeaf(page)
  const shape = await gizmoOf(page)
  expect(shape.instance).toBe(LEAF)
  expect(shape.axes.filter((a) => a.move && a.turn).length).toBeGreaterThanOrEqual(2)
  await page.locator('.instance-row[data-fixed] [data-instance]').first().click()
  await expect(page.getByTestId('gizmo')).toHaveCount(0)
  await expect.poll(async () => (await readAssembly(page)).gizmo).toBeNull()
})

for (const kind of ['touch', 'pen'] as PointerKind[]) {
  test(`a ${kind} drag on a ring swings the hinge leaf about the pin, and one undo brings it back`, async ({ page }) => {
    await selectLeaf(page)
    const before = await pose(page)
    const camera = (await readAssembly(page)).camera
    const base = (await readAssembly(page)).placements['I1']!
    let turned = false
    for (const axis of [2]) { // the pin stands along the leaf's own Z, so that ring turns it
      const g = await gizmoOf(page)
      const a = g.axes[axis]!
      if (!a.turn) continue
      const o = g.origin
      const from = { x: a.turn.x, y: a.turn.y }
      // swing the finger a quarter turn about the gizmo's origin, in 8 steps
      const dx = from.x - o.x
      const dy = from.y - o.y
      const steps = []
      for (let k = 0; k <= 8; k++) {
        const ang = (Math.PI / 2) * (k / 8)
        const p = { x: o.x + dx * Math.cos(ang) - dy * Math.sin(ang), y: o.y + dx * Math.sin(ang) + dy * Math.cos(ang) }
        steps.push({ phase: k === 0 ? ('down' as const) : ('move' as const), kind, id: 5, ...p })
      }
      steps.push({ phase: 'up' as const, kind, id: 5, x: steps[8]!.x, y: steps[8]!.y })
      await sendPointer(page, steps)
      await expect.poll(async () => (await readAssembly(page)).gizmo?.active ?? null).toBeNull()
      await settledAssembly(page)
      try {
        await expect.poll(async () => differs(await pose(page), before), { timeout: 20_000 }).toBe(true)
        turned = true
        break
      } catch {
        // the leaf did not turn
      }
    }
    expect(turned).toBe(true)
    // the fixed Base plate stays where it is while the leaf swings
    expect(differs((await readAssembly(page)).placements['I1']!, base)).toBe(false)
    // the drag did not orbit the view
    expect((await readAssembly(page)).camera.back).toEqual(camera.back)
    // the label shows the angle the drag made
    await expect(page.getByTestId('gizmo-label')).toContainText('°')
    const after = await pose(page)
    expect(differs(after, before)).toBe(true)

    // the engine holds ONE undo step for the drag (and one per ring tried that moved it): undo until the leaf is back
    const doc = await idOf(page.request, 'Hinge')
    const undo = () => page.request.post(`/documents/${doc}/tabs/T2/edit`, { data: { op: 'undo' } })
    expect((await undo()).ok()).toBe(true)
    await expect.poll(async () => differs(await pose(page), before), { timeout: 10_000 }).toBe(false)
  })
}

// F17: with a mouse (Adam's desktop report) the leaf follows the pointer while the button is held, not only after release.
// The view draws the gizmo's motion at once on each pointer move, so the hook reports the new placement as soon as the move
// is dispatched, with no waiting for the server.
test('a mouse drag on a ring moves the leaf before the button is released', async ({ page }) => {
  await selectLeaf(page)
  const before = await pose(page)
  const base = (await readAssembly(page)).placements['I1']!
  const g = await gizmoOf(page)
  const a = g.axes[2]! // the pin stands along the leaf's own Z
  const box = (await viewCanvas(page).boundingBox())!
  const o = g.origin
  const dx = a.turn!.x - o.x
  const dy = a.turn!.y - o.y
  const at = (ang: number) => ({ x: box.x + o.x + dx * Math.cos(ang) - dy * Math.sin(ang), y: box.y + o.y + dx * Math.sin(ang) + dy * Math.cos(ang) })
  await page.mouse.move(at(0).x, at(0).y)
  await page.mouse.down()
  await page.mouse.move(at(0.35).x, at(0.35).y, { steps: 4 })
  // still held: the leaf has moved already
  const held = await pose(page)
  expect((await readAssembly(page)).gizmo?.active?.kind).toBe('turn')
  expect(differs(held, before)).toBe(true)
  await page.mouse.move(at(0.7).x, at(0.7).y, { steps: 4 })
  const further = await pose(page)
  expect(differs(further, held)).toBe(true)
  await page.mouse.up()
  await expect.poll(async () => (await readAssembly(page)).gizmo?.active ?? null).toBeNull()
  await settledAssembly(page)
  await expect.poll(async () => differs(await pose(page), before), { timeout: 20_000 }).toBe(true)
  expect(differs((await readAssembly(page)).placements['I1']!, base)).toBe(false)
  // one undo step for the whole drag
  const doc = await idOf(page.request, 'Hinge')
  expect((await page.request.post(`/documents/${doc}/tabs/T2/edit`, { data: { op: 'undo' } })).ok()).toBe(true)
  await expect.poll(async () => differs(await pose(page), before), { timeout: 10_000 }).toBe(false)
})

test('the pad types an exact angle on the value label', async ({ page }) => {
  await selectLeaf(page)
  const before = await pose(page)
  const g = await gizmoOf(page)
  const o = g.origin
  {
    const a = g.axes[2]! // the pin stands along the leaf's own Z

    const dx = a.turn!.x - o.x
    const dy = a.turn!.y - o.y
    const q = (ang: number) => ({ x: o.x + dx * Math.cos(ang) - dy * Math.sin(ang), y: o.y + dx * Math.sin(ang) + dy * Math.cos(ang) })
    await sendPointer(page, [
      { phase: 'down', kind: 'touch', id: 6, ...q(0) },
      { phase: 'move', kind: 'touch', id: 6, ...q(0.3) },
      { phase: 'move', kind: 'touch', id: 6, ...q(0.6) },
      { phase: 'up', kind: 'touch', id: 6, ...q(0.6) },
    ])
    await expect.poll(async () => differs(await pose(page), before), { timeout: 20_000 }).toBe(true)
  }
  await page.getByTestId('gizmo-label').click()
  await page.getByTestId('pad').waitFor()
  const key = (n: string) => page.getByTestId('pad').getByRole('button', { name: n, exact: true }).click()
  for (const n of ['3', '0']) await key(n)
  await key('Enter')
  await expect(page.getByTestId('gizmo-label')).toHaveText('30°')
  await expect(page.getByTestId('pad')).toHaveCount(0)
})
