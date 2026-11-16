import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll } from './support/docs.ts'
import { openAssemblyTab, readAssembly } from './support/assembly.ts'

// Slice S8, drawing an Assembly (card I8.1) against `freeshape serve`: the Hinge's Assembly shows its instances in the
// view and in the list, the eye hides one and shows it again, and a pick on an instance names the instance. Edits stay
// in the server's memory (nothing is saved); the next openApp closes the Document and throws them away.

test.afterEach(async ({ page }) => closeAll(page.request))

test('the hinge Assembly draws its instances, and the eye hides one and shows it again', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  const first = await openAssemblyTab(page, 3)
  console.log(`assembly: ${JSON.stringify({ instances: first.instances, drawn: first.drawn, hidden: first.hidden, geometries: first.geometries, triangles: first.triangles })}`)
  expect([first.instances, first.drawn, first.hidden, first.geometries]).toEqual([3, 3, 0, 3])
  expect(first.drawnIds).toEqual(['I1', 'I2', 'I3'])
  await expect(page.getByTestId('instance-list').locator('li')).toHaveCount(3)
  await expect(page.getByTestId('mate-list').locator('li')).toHaveCount(2)

  const arm = (await page.locator('[data-instance-row="I2"] .row-name').innerText()).trim()
  await page.getByRole('button', { name: `Hide ${arm}`, exact: true }).click()
  await expect.poll(async () => (await readAssembly(page)).drawn).toBe(2)
  const hidden = await readAssembly(page)
  expect([hidden.hidden, hidden.drawnIds]).toEqual([1, ['I1', 'I3']])
  expect(hidden.revision).toBeGreaterThan(first.revision ?? 0)
  await expect(page.locator('[data-instance-row="I2"]')).toHaveAttribute('data-hidden', '')

  await page.getByRole('button', { name: `Show ${arm}`, exact: true }).click()
  await expect.poll(async () => (await readAssembly(page)).drawn).toBe(3)
  expect((await readAssembly(page)).drawnIds).toEqual(['I1', 'I2', 'I3'])
})

test('a pick on a drawn instance returns its id with the part and an Element name', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 3)
  // the middle of each instance's box is usually on one of its faces; try a few points around it
  const picked = await page.evaluate(() => {
    const v = window.__freeshapeAssembly!
    const found: Record<string, { instance: string | undefined; kind: string; name: string | null }> = {}
    for (const id of v.drawnIds) {
      const c = v.screenOf(id)
      if (!c) continue
      for (const [dx, dy] of [[0, 0], [6, 0], [-6, 0], [0, 6], [0, -6], [14, 10], [-14, -10]] as const) {
        const hit = v.pickAt(c.x + dx, c.y + dy, 'mouse', ['face'])
        if (hit && hit.instance === id) {
          found[id] = { instance: hit.instance, kind: hit.kind, name: hit.name }
          break
        }
      }
    }
    return found
  })
  console.log(`picks: ${JSON.stringify(picked)}`)
  expect(Object.keys(picked).length).toBeGreaterThanOrEqual(2)
  for (const hit of Object.values(picked)) {
    expect(hit.kind).toBe('face')
    expect(hit.name).toMatch(/\S/)
  }
})

test('the fixed toggle and Delete change the Assembly the server holds', async ({ page }) => {
  await openApp(page, { document: 'Hinge', model: false })
  await openAssemblyTab(page, 3)
  const free = page.locator('.instance-row:not([data-fixed]) .instance-fixed').first()
  const id = await free.getAttribute('data-instance-fixed')
  await free.click()
  await expect(page.locator(`[data-instance-row="${id}"]`)).toHaveAttribute('data-fixed', '')
  await page.locator(`[data-instance="${id}"]`).click()
  await page.getByRole('button', { name: /^Delete / }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click()
  await expect.poll(async () => (await readAssembly(page)).instances).toBe(2)
  await expect(page.getByTestId('instance-list').locator('li')).toHaveCount(2)
})
