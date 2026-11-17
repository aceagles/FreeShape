import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { removeDocument } from './docs.ts'

/** The 64 x 32 picture of the canvas specs and shots: red, with a blue square at its bottom left (web/fixtures/make_canvas_fixture.py). */
export const CANVAS_PNG = resolve(import.meta.dirname, '../fixtures/canvas.png')
const CLIENT = 'client-canvas-setup'

/**
 * A Canvas on Top of Part Studio T1 of a Document, made through the routes the client uses (upload, dialog, update, accept) as a client
 * that is not the open page, so the open page follows by the revision it makes. `values` are dialog values (Width, Opacity, ...).
 */
export async function canvasOnTop(page: Page, doc: string, values: Record<string, unknown> = {}): Promise<void> {
  const tab = `/documents/${doc}/tabs/T1`
  const headers = { 'X-Client-Id': CLIENT }
  const up = await page.request.post(`${tab}/images?name=canvas.png`, { data: readFileSync(CANVAS_PNG), headers: { ...headers, 'Content-Type': 'application/octet-stream' } })
  expect(up.ok(), await up.text()).toBe(true)
  const { blob, filename } = (await up.json()) as { blob: string; filename: string }
  const open = await page.request.post(`${tab}/features/dialog`, { data: { type: 'canvas', params: { blob, filename } }, headers })
  expect(open.ok(), await open.text()).toBe(true)
  const { dialog } = (await open.json()) as { dialog: string }
  if (Object.keys(values).length > 0) expect((await page.request.post(`${tab}/dialog/${dialog}/update`, { data: { values }, headers })).ok()).toBe(true)
  const done = await page.request.post(`${tab}/dialog/${dialog}/accept`, { data: {}, headers })
  expect(done.ok(), await done.text()).toBe(true)
}

/** A new Document with an empty Part Studio, saved and closed: nothing in front of a canvas. Remove it with `removeDocument(name)`. */
export async function emptyDocument(page: Page, name: string): Promise<string> {
  removeDocument(name)
  const made = (await (await page.request.post('/documents', { data: { name } })).json()) as { id: string }
  await page.request.post(`/documents/${made.id}/open`, { data: {} })
  await page.request.post(`/documents/${made.id}/save`, { data: {} })
  await page.request.post(`/documents/${made.id}/close`, { data: {} })
  return made.id
}

/** Waits until every canvas the view draws has its picture on it. */
export async function waitPictured(page: Page, n = 1): Promise<void> {
  await page.waitForFunction((k) => {
    const c = window.__freeshapeViewport?.canvasScreens() ?? []
    return c.length >= k && c.every((x) => x.pictured)
  }, n)
}
