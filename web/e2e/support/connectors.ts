import { expect } from '@playwright/test'
import type { Page } from '@playwright/test'

/**
 * A Mate connector on a flat face of `part` in Part Studio T1 of the open Document, added by an edit (card I10.4 makes them from the
 * browser): the face is named with the refs route, as a dialog's pick would, so the connector sits on the face and follows it.
 */
export async function connectorOnFace(page: Page, doc: string, part: string, face: number, fid: string, name: string): Promise<void> {
  const refs = await page.request.post(`/documents/${doc}/tabs/T1/refs`, { data: { picks: [{ part, kind: 'face', index: face, want: ['element'] }] } })
  const item = ((await refs.json()) as { items: Array<{ ok: boolean; ref: unknown }> }).items[0]!
  expect(item.ok).toBe(true)
  const params = { name, ref: item.ref, kind: 'face', click: null, snap: null, x_ref: null, flip: false, owner: part }
  const res = await page.request.post(`/documents/${doc}/tabs/T1/edit`, { data: { op: 'add_feature', feature: { id: fid, type: 'mate_connector', name, suppressed: false, params } } })
  expect(res.ok()).toBe(true)
}
