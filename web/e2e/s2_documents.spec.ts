import { expect, test } from '@playwright/test'
import { openApp } from './support/app.ts'
import { closeAll, discardAutosaveOf, holdLockClosed, idOf, releaseLock, removeDocument } from './support/docs.ts'
import { longPressTab } from './support/tabs.ts'

// Slice S2, "Documents and tabs", against `freeshape serve` on a Documents root (scripts/web/e2e.sh): the home
// screen, tabs, saving, the Document lock and recovery. Under WebKit at 1024 x 768 with touch. Each test starts from
// closed Documents (openApp, closeAll), makes what it needs and cleans up, so none depends on another.

const EDIT = { op: 'set_param', feature: 'F5', key: 'diameter', value: 7.0 }

test('from the home screen: create a Document, add a Part Studio, rename it, save, go home, reopen', async ({ page }) => {
  const name = 'Spec widget'
  const renamed = 'Base plate'
  try {
    await closeAll(page.request)
    await page.goto('/')
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.locator('.doc-name', { hasText: /^Bracket$/ })).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.doc-name', { hasText: /^Hinge$/ })).toBeVisible()
    await page.getByRole('button', { name: 'New Document' }).click()
    await page.getByRole('textbox', { name: 'Name' }).fill(name)
    await page.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByTestId('shell')).toBeVisible() // a new Document opens
    await expect(page.getByTestId('document-name')).toHaveText(name)
    await expect(page.locator('.tab-button')).toHaveCount(1)

    await page.getByRole('button', { name: 'Add tab' }).click()
    await page.getByRole('menuitem', { name: 'Part Studio' }).click()
    await expect(page.locator('.tab-button')).toHaveCount(2)

    await longPressTab(page, 'last')
    await page.getByRole('menuitem', { name: 'Rename' }).click()
    const field = page.getByRole('textbox', { name: 'Name' })
    await field.fill(renamed)
    await field.press('Enter')
    await expect(page.locator('.tab-button').last()).toHaveText(renamed)

    await page.getByTestId('dirty-dot').waitFor()
    await page.getByRole('button', { name: 'Save, unsaved changes' }).click()
    await expect(page.getByTestId('dirty-dot')).toHaveCount(0)
    await expect(page.locator('.tab-button')).toHaveCount(2) // the tab list the save fetches still shows both tabs

    await page.getByRole('button', { name: 'Back to Documents' }).click()
    await expect(page.getByTestId('home')).toBeVisible()
    await page.locator('.doc-row', { has: page.locator('.doc-name', { hasText: name }) }).click()
    await expect(page.getByTestId('shell')).toBeVisible()
    await expect(page.locator('.tab-button')).toHaveCount(2)
    await expect(page.locator('.tab-button').last()).toHaveText(renamed)
  } finally {
    await closeAll(page.request)
    removeDocument(name)
  }
})

test('a Document whose lock another program holds opens read-only, with the banner and the save button off', async ({ page }) => {
  try {
    await holdLockClosed(page.request, 'bracket', 'desktop')
    await openApp(page, { model: false })
    const banner = page.getByTestId('read-only-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('the desktop')
    await expect(banner.getByRole('button', { name: 'Take over' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Add tab' })).toBeDisabled()
  } finally {
    releaseLock('bracket')
  }
})

test('the changes a closed Document left behind are offered, and Recover puts them back unsaved', async ({ page }) => {
  try {
    await closeAll(page.request)
    const id = await idOf(page.request, 'Bracket')
    expect((await page.request.post(`/documents/${id}/open`, { data: {} })).status()).toBe(200)
    const edited = await page.request.post(`/documents/${id}/tabs/T1/edit`, { data: EDIT })
    expect(edited.status(), await edited.text()).toBe(200)
    expect((await page.request.post(`/documents/${id}/close`, { data: {} })).status()).toBe(200) // keeps an autosave

    await page.goto('/')
    await expect(page.getByTestId('recover-mark')).toBeVisible({ timeout: 30_000 })
    await page.locator('.doc-row', { has: page.locator('.doc-name', { hasText: /^Bracket$/ }) }).click()
    const dialog = page.getByRole('dialog', { name: /Recover unsaved changes/ })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Recover' }).click()
    await expect(page.getByTestId('shell')).toBeVisible()
    await expect(page.getByTestId('dirty-dot')).toBeVisible()
  } finally {
    await closeAll(page.request)
    await discardAutosaveOf(page.request, 'Bracket')
  }
})
