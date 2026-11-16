import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { holder, Scripted, singleDocument, tab } from '../support/scripted.ts'
import type { Autosave, Doc } from '../support/scripted.ts'

// The home screen, the tab bar and saving, against a scripted server (no freeshape serve). The test drives every step.
// The whole-window screenshots go to web/shots/out/ (not committed); scripts read them from there.

const now = Date.now() / 1000
const doc = (id: string, name: string, ago: number, extra: Partial<Doc> = {}): Doc => ({
  id,
  name,
  modified: now - ago,
  holder: null,
  open: false,
  missing: false,
  tabs: [tab('T1', 'studio', 'Part Studio 1'), tab('T2', 'assembly', 'Assembly 1')],
  ...extra,
})
const several = () =>
  new Scripted([
    doc('a', 'Gearbox bracket', 3600),
    doc('b', 'Hinge', 86400 * 3, { holder: holder('desktop') }),
    doc('c', 'Pulley block', 86400 * 40),
    doc('d', 'Moved folder', 86400 * 90, { missing: true }),
  ])

const autosave = (extra: Partial<Autosave> = {}): Autosave => ({
  time: now - 25 * 60,
  count: 1,
  tabs: [{ id: 'T1', name: 'Part Studio 1' }],
  deleted: [],
  moved_on: [],
  list_moved_on: false,
  writable: true,
  ...extra,
})
/** The Documents of `several`, the second one of them (Pulley block) with an autosave to recover. */
const withAutosave = (extra: Partial<Autosave> = {}) => {
  const server = several()
  server.doc('c').autosave = autosave(extra)
  return server
}

const sizes = [
  { width: 1024, height: 768 },
  { width: 1366, height: 1024 },
] as const

/** Anything clipped by the window, a button under 44 px, or text that overflows its box. */
const layoutProblems = (page: Page) =>
  page.evaluate(() => {
    const out: string[] = []
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.width === 0 && r.height === 0) continue
      if (r.left < -0.5 || r.top < -0.5 || r.right > innerWidth + 0.5 || r.bottom > innerHeight + 0.5) out.push(`${el.className} ${JSON.stringify(r.toJSON())}`)
      if (el.tagName === 'BUTTON' && (r.width < 43.5 || r.height < 43.5)) out.push(`small ${el.className} ${r.width}x${r.height}`)
      if (el instanceof HTMLElement && el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible') out.push(`overflow ${el.className}`)
    }
    return out
  })

async function start(page: Page, server: Scripted, width = 1024, height = 768) {
  await page.setViewportSize({ width, height })
  await server.install(page)
  await page.goto('/')
  await expect(page.getByTestId('link-status')).toHaveText('Connected')
}

const shot = (page: Page, name: string) => page.screenshot({ path: `shots/out/static-${name}-${page.viewportSize()!.width}x${page.viewportSize()!.height}.png` })

for (const { width, height } of sizes) {
  test(`home screen at ${width} x ${height}: the list, holders, nothing clipped, targets of 44 px or more`, async ({ page }) => {
    await start(page, several(), width, height)
    await expect(page.getByTestId('home')).toBeVisible()
    const rows = page.locator('.doc-row')
    await expect(rows).toHaveCount(4)
    await expect(rows.nth(1)).toContainText('Hinge')
    await expect(rows.nth(1)).toContainText('Open in the desktop')
    await expect(rows.nth(3)).toBeDisabled()
    expect(await layoutProblems(page)).toEqual([])
    await shot(page, 'home')
  })

  test(`new Document dialog at ${width} x ${height} sits in the top half, and Enter creates and opens it`, async ({ page }) => {
    const server = several()
    await start(page, server, width, height)
    await page.getByRole('button', { name: 'New Document' }).click()
    const field = page.getByRole('textbox', { name: 'Name' })
    await expect(field).toBeFocused()
    await field.fill('Flywheel')
    const box = await page.locator('.modal').boundingBox()
    expect(box!.y + box!.height).toBeLessThan(height / 2)
    await shot(page, 'new-document')
    await field.press('Enter')
    await expect(page.getByTestId('shell')).toBeVisible()
    await expect(page.getByTestId('document-name')).toHaveText('Flywheel')
    expect(server.log).toEqual(['POST /documents', 'POST /documents/new1/open', 'ws subscribe new1'])
  })
}

test('opening a Document, then Back, closes it and returns to the list', async ({ page }) => {
  const server = several()
  await start(page, server)
  await page.getByRole('button', { name: /Gearbox bracket/ }).click()
  await expect(page.getByTestId('document-name')).toHaveText('Gearbox bracket')
  await expect(page.getByRole('button', { name: 'Part Studio 1' })).toBeVisible()
  await page.getByRole('button', { name: 'Back to Documents' }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  expect(server.log).toEqual(['POST /documents/a/open', 'ws subscribe a', 'POST /documents/a/close', 'ws unsubscribe a'])
  expect(server.doc('a').open).toBe(false)
})

test('opening a Document while another is open offers to close the other', async ({ page }) => {
  const server = several()
  await start(page, server)
  await expect(page.getByTestId('home')).toBeVisible()
  server.doc('c').open = true // another client opened it since the list was read
  await page.getByRole('button', { name: /Gearbox bracket/ }).click()
  const dialog = page.getByRole('dialog', { name: 'Another Document is open' })
  await expect(dialog).toContainText('"Pulley block" is still open')
  await shot(page, 'another-open')
  await dialog.getByRole('button', { name: 'Close "Pulley block"' }).click()
  await expect(page.getByTestId('document-name')).toHaveText('Gearbox bracket')
  expect(server.doc('c').open).toBe(false)
})

test('a reload with one Document open resumes it', async ({ page }) => {
  const server = several()
  server.doc('c').open = true
  await start(page, server)
  await expect(page.getByTestId('document-name')).toHaveText('Pulley block')
})

for (const { width, height } of sizes) {
  test(`a Document held by the desktop opens read-only with the banner at ${width} x ${height}; Take over asks first`, async ({ page }) => {
    const server = several()
    await start(page, server, width, height)
    await page.getByRole('button', { name: /Hinge/ }).click()
    const banner = page.getByTestId('read-only-banner')
    await expect(banner).toContainText('"Hinge" is being edited in the desktop, so it is open read-only here.')
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Add tab' })).toBeDisabled()
    await shot(page, 'read-only')
    await page.getByRole('button', { name: 'Take over' }).click()
    const dialog = page.getByRole('dialog', { name: 'Take over "Hinge"?' })
    await expect(dialog).toBeVisible()
    await shot(page, 'take-over')
    expect(server.doc('b').holder).not.toBeNull()
    await dialog.getByRole('button', { name: 'Take over' }).click()
    await expect(banner).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Add tab' })).toBeEnabled()
  })
}

test('the lock being taken away while a Document is open shows the banner at once', async ({ page }) => {
  const server = singleDocument()
  await start(page, server)
  await expect(page.getByTestId('read-only-banner')).toHaveCount(0)
  server.push({ type: 'lock', document: 'd', read_only: true, holder: holder('desktop') })
  await expect(page.getByTestId('read-only-banner')).toBeVisible()
  server.push({ type: 'lock', document: 'd', read_only: false, holder: null })
  await expect(page.getByTestId('read-only-banner')).toHaveCount(0)
})

test('a long press on a tab opens its menu and does not switch; a short press switches; the tab has no callout or selection', async ({ page }) => {
  await start(page, singleDocument())
  const second = page.getByRole('button', { name: 'Assembly 1' })
  const first = page.getByRole('button', { name: 'Part Studio 1' })
  await expect(first).toHaveAttribute('aria-current', 'page')
  expect(await second.evaluate((el) => getComputedStyle(el).userSelect)).toBe('none')
  // -webkit-touch-callout exists on iOS only, so this engine does not report it: read the rule from the source
  expect(readFileSync('src/shell/shell.css', 'utf8')).toMatch(/\.tab-button \{[^}]*-webkit-touch-callout: none;/)
  const box = (await second.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await expect(page.getByRole('menu', { name: 'Assembly 1 menu' })).toBeVisible() // appears by itself after 500 ms of holding
  await page.mouse.up()
  await expect(page.getByRole('menu')).toBeVisible()
  await expect(first).toHaveAttribute('aria-current', 'page')
  expect(await page.evaluate(() => String(getSelection()))).toBe('')
  await page.mouse.click(5, 700) // a press elsewhere closes the menu
  await expect(page.getByRole('menu')).toHaveCount(0)
  await second.click()
  await expect(second).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('menu')).toHaveCount(0)
})

async function longPress(page: Page, name: string) {
  const box = (await page.getByRole('button', { name }).boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.getByRole('menu').waitFor()
  await page.mouse.up()
}

for (const { width, height } of sizes) {
  test(`tab menu, add-tab choice, rename and delete dialogs at ${width} x ${height}`, async ({ page }) => {
    const server = singleDocument()
    await start(page, server, width, height)
    await longPress(page, 'Part Studio 1')
    await shot(page, 'tab-menu')
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    const field = page.getByRole('textbox', { name: 'Name' })
    await expect(field).toBeFocused()
    const box = await page.locator('.modal').boundingBox()
    expect(box!.y + box!.height).toBeLessThan(height / 2)
    await shot(page, 'rename')
    await field.fill('Base plate')
    await field.press('Enter')
    await expect(page.getByRole('button', { name: 'Base plate' })).toBeVisible()
    await expect(page.getByTestId('dirty-dot')).toBeVisible()
    await shot(page, 'dirty')
    await page.getByRole('button', { name: 'Save, unsaved changes' }).click()
    await expect(page.getByTestId('dirty-dot')).toHaveCount(0)
    await page.getByRole('button', { name: 'Add tab' }).click()
    await shot(page, 'add-tab')
    await page.getByRole('menuitem', { name: 'Part Studio' }).click()
    await expect(page.getByRole('button', { name: 'Part Studio 2' })).toHaveAttribute('aria-current', 'page')
    await longPress(page, 'Part Studio 2')
    await page.getByRole('menuitem', { name: 'Delete' }).click()
    await expect(page.getByRole('dialog', { name: 'Delete "Part Studio 2"?' })).toBeVisible()
    await shot(page, 'delete-tab')
    await page.getByRole('button', { name: 'Delete' }).click()
    await expect(page.getByRole('button', { name: 'Part Studio 2' })).toHaveCount(0)
    // a Part Studio that an Assembly uses cannot be deleted: the message names the Assembly
    await longPress(page, 'Base plate')
    await page.getByRole('menuitem', { name: 'Delete' }).click()
    await page.getByRole('button', { name: 'Delete' }).click()
    const message = page.getByRole('dialog', { name: 'Could not delete "Base plate"' })
    await expect(message).toContainText('"Assembly 1" uses it')
    await shot(page, 'delete-refused')
    await message.getByRole('button', { name: 'OK' }).click()
    expect(server.log).toContain('DELETE /documents/d/tabs/T3')
  })
}

test('back with unsaved changes asks to save, discard or stay', async ({ page }) => {
  const server = singleDocument()
  server.doc('d').tabs[0]!.dirty = true
  await start(page, server)
  await page.getByRole('button', { name: 'Back to Documents' }).click()
  const dialog = page.getByRole('dialog', { name: 'Save changes to "Bracket"?' })
  await expect(dialog.getByRole('button')).toHaveText(['Stay', 'Discard', 'Save'])
  await shot(page, 'leave-unsaved')
  await dialog.getByRole('button', { name: 'Stay' }).click()
  await expect(page.getByTestId('shell')).toBeVisible()
})

for (const { width, height } of sizes) {
  test(`a Document with an autosave to recover is marked on the home screen at ${width} x ${height}`, async ({ page }) => {
    await start(page, withAutosave(), width, height)
    const rows = page.locator('.doc-row')
    await expect(rows).toHaveCount(4)
    await expect(rows.nth(2)).toContainText('Pulley block')
    await expect(rows.nth(2).getByTestId('recover-mark')).toHaveText('Unsaved edits can be recovered')
    await expect(page.getByTestId('recover-mark')).toHaveCount(1)
    expect(await layoutProblems(page)).toEqual([])
    await shot(page, 'home-recoverable')
  })

  test(`opening it asks Recover or Discard, in the top half, with what each does at ${width} x ${height}`, async ({ page }) => {
    const server = withAutosave()
    await start(page, server, width, height)
    await page.getByRole('button', { name: /Pulley block/ }).click()
    const dialog = page.getByRole('dialog', { name: 'Recover unsaved changes to "Pulley block"?' })
    await expect(dialog).toContainText('written 25 minutes ago')
    await expect(dialog.getByRole('button')).toHaveText(['Decide later', 'Discard', 'Recover'])
    await expect(page.getByTestId('shell')).toHaveCount(0) // the question comes before the Document
    const box = await page.locator('.modal').boundingBox()
    expect(box!.y + box!.height).toBeLessThan(height / 2)
    expect(await layoutProblems(page)).toEqual([])
    await shot(page, 'recover-dialog')
    await dialog.getByRole('button', { name: 'Recover' }).click()
    await expect(page.getByTestId('document-name')).toHaveText('Pulley block')
    await expect(page.getByTestId('dirty-dot')).toBeVisible() // recovered edits are unsaved
    expect(server.log).toEqual(['POST /documents/c/open', 'POST /documents/c/recover', 'ws subscribe c'])
    expect(server.doc('c').autosave).toBeNull()
  })

  test(`when the saved Document has moved on the dialog says what Recover replaces at ${width} x ${height}`, async ({ page }) => {
    await start(page, withAutosave({ moved_on: ['T1'] }), width, height)
    await page.getByRole('button', { name: /Pulley block/ }).click()
    const warning = page.getByTestId('autosave-moved')
    await expect(warning).toContainText('"Part Studio 1" was saved by another program after these changes were written')
    const box = await page.locator('.modal').boundingBox()
    expect(box!.y + box!.height).toBeLessThan(height * 0.75)
    expect(await layoutProblems(page)).toEqual([])
    await shot(page, 'recover-dialog-moved-on')
  })

  test(`a Document the server closed returns the client to the home screen with a message at ${width} x ${height}`, async ({ page }) => {
    const server = several()
    await start(page, server, width, height)
    await page.getByRole('button', { name: /Gearbox bracket/ }).click()
    await expect(page.getByTestId('document-name')).toHaveText('Gearbox bracket')
    server.doc('a').open = false
    server.doc('a').autosave = autosave({ time: now })
    server.push({ type: 'closed', document: 'a', reason: 'idle' }) // the server closed it for being idle
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.getByTestId('home-notice')).toContainText('"Gearbox bracket" was closed because nobody was connected to it for a while')
    await expect(page.getByTestId('home-notice')).toContainText('Changes that were not saved are kept')
    await expect(page.locator('.doc-row').first().getByTestId('recover-mark')).toBeVisible()
    expect(await layoutProblems(page)).toEqual([])
    await shot(page, 'closed-notice')
  })
}

test('Discard deletes the autosave and opens the Document as saved; Decide later opens it and deletes nothing', async ({ page }) => {
  const server = withAutosave()
  await start(page, server)
  await page.getByRole('button', { name: /Pulley block/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Decide later' }).click()
  await expect(page.getByTestId('document-name')).toHaveText('Pulley block')
  await expect(page.getByTestId('dirty-dot')).toHaveCount(0)
  expect(server.doc('c').autosave).not.toBeNull()
  await page.getByRole('button', { name: 'Back to Documents' }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  await page.getByRole('button', { name: /Pulley block/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Discard' }).click()
  await expect(page.getByTestId('document-name')).toHaveText('Pulley block')
  expect(server.doc('c').autosave).toBeNull()
  expect(server.log).toContain('POST /documents/c/discard-autosave')
  expect(server.log).not.toContain('POST /documents/c/recover')
})
