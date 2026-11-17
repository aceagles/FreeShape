import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support/app.ts'
import { openAssemblyTab, readAssembly } from './support/assembly.ts'
import { closeAll, idOf } from './support/docs.ts'
import { desktopMode } from './support/mode.ts'
import { readViewport } from './support/viewport.ts'

// Slice S10, card I10.3: mate connectors drawn and picked, on the served example Documents, under WebKit. A mate connector is made by an
// edit of the Part Studio (the card that makes them from the browser is I10.4), then drawn as a triad in the Part Studio and on the
// Assembly's instances; K and J hide them; a pick that names `connector` returns one ahead of the face behind it. Every check polls the
// page's state: the server answers an edit, the client then redraws.
test.use(desktopMode)
test.afterEach(async ({ page }) => closeAll(page.request))

/** A Mate connector at the origin of part `owner`, added to Part Studio T1 of the open Document by an edit. */
async function addConnector(page: Page, doc: string, owner: string, fid: string, name: string): Promise<void> {
  const params = { name, ref: { origin: true }, kind: 'face', click: null, snap: null, x_ref: null, flip: false, owner }
  const res = await page.request.post(`/documents/${doc}/tabs/T1/edit`, { data: { op: 'add_feature', feature: { id: fid, type: 'mate_connector', name, suppressed: false, params } } })
  expect(res.ok()).toBe(true)
}

test.describe('Part Studio', () => {
  test('a connector is a triad in the view; K hides it and K again shows it; a pick that names connector finds it before the face', async ({ page }) => {
    await openApp(page)
    const doc = await idOf(page.request, 'Bracket')
    const part = (await readViewport(page)).visiblePartIds[0]!
    await addConnector(page, doc, part, 'F90', 'Pin')
    await expect.poll(async () => (await readViewport(page)).connectors).toBe(1)
    await page.keyboard.press('k')
    await expect.poll(async () => (await readViewport(page)).connectors).toBe(0)
    await page.keyboard.press('k')
    await expect.poll(async () => (await readViewport(page)).connectors).toBe(1)
    const at = await page.evaluate(() => window.__freeshapeViewport!.connectorScreens()[0]!)
    expect(at.id).toBe('F90')
    const hit = await page.evaluate(([x, y]) => window.__freeshapeViewport!.pickAt(x!, y!, 'mouse', ['face', 'edge', 'connector']), [at.x, at.y])
    expect(hit).toMatchObject({ kind: 'connector', key: 'F90' })
    // the kinds a click takes in Desktop mode are the Select menu's: a connector is never one of them
    const plain = await page.evaluate(([x, y]) => window.__freeshapeViewport!.pickAt(x!, y!, 'mouse'), [at.x, at.y])
    expect(plain?.kind).not.toBe('connector')
  })
})

test.describe('Assembly', () => {
  test('the instances offer the connectors of their Part Studio, and each mate end is a triad; J and K hide them', async ({ page }) => {
    await openApp(page, { document: 'Hinge', model: false })
    const doc = await idOf(page.request, 'Hinge')
    await addConnector(page, doc, 'F2/1', 'F90', 'Top')
    await addConnector(page, doc, 'F4/1', 'F91', 'Hub')
    await openAssemblyTab(page, 2)
    await expect.poll(async () => (await readAssembly(page)).connectors).toBeGreaterThanOrEqual(2)
    const was = await readAssembly(page)
    expect(was.mateEnds).toBeGreaterThanOrEqual(2)
    await page.keyboard.press('j')
    await expect.poll(async () => (await readAssembly(page)).mateEnds).toBe(0)
    await page.keyboard.press('j')
    await expect.poll(async () => (await readAssembly(page)).mateEnds).toBe(was.mateEnds)
    await page.keyboard.press('k')
    await expect.poll(async () => (await readAssembly(page)).connectors).toBe(0)
    await page.keyboard.press('k')
    await expect.poll(async () => (await readAssembly(page)).connectors).toBe(was.connectors)
    // a pick names the instance and the connector id
    const at = await page.evaluate(() => window.__freeshapeAssembly!.connectorScreens().find((c) => c.id === 'F90')!)
    const hit = await page.evaluate(([x, y]) => window.__freeshapeAssembly!.pickAt(x!, y!, 'mouse', ['face', 'edge', 'vertex', 'connector']), [at.x, at.y])
    expect(hit).toMatchObject({ kind: 'connector', key: 'F90', instance: at.instance })
  })
})
