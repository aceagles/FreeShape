import type { APIRequestContext } from '@playwright/test'
import { closeAll, removeDocument } from './docs.ts'

/**
 * The fixture of the create-features specs, built from scratch through the server (nothing under examples/ is touched):
 * a new Document with its Part Studio and one sketch of a 40 x 25 mm rectangle on the Top plane. `solid` also extrudes
 * the sketch region 10 mm, by the dialog routes the client uses, so the view has a model to pick faces and edges on
 * (the view cannot pick a sketch region yet; the server's create route takes one). The Document is left closed and
 * saved: the spec opens it from the home screen, like any other.
 */
export const FIXTURE = 'Create fixture'
const CLIENT = 'client-s6-create-setup'

const RECTANGLE = {
  points: { origin: [0, 0], p1: [0, 0], p2: [40, 0], p3: [40, 25], p4: [0, 25] },
  entities: {
    l5: { type: 'line', p1: 'p1', p2: 'p2', construction: false },
    l6: { type: 'line', p1: 'p2', p2: 'p3', construction: false },
    l7: { type: 'line', p1: 'p3', p2: 'p4', construction: false },
    l8: { type: 'line', p1: 'p4', p2: 'p1', construction: false },
  },
  constraints: [],
}

async function ok(res: Awaited<ReturnType<APIRequestContext['post']>>, what: string): Promise<unknown> {
  if (!res.ok()) throw new Error(`${what} failed: ${res.status()} ${await res.text()}`)
  return res.json()
}

export async function makeFixture(request: APIRequestContext, opts: { solid: boolean }): Promise<string> {
  await dropFixture(request)
  const made = (await ok(await request.post('/documents', { data: { name: FIXTURE } }), 'create the Document')) as { id: string }
  const id = made.id
  await ok(await request.post(`/documents/${id}/open`, { data: {} }), 'open')
  const tab = `/documents/${id}/tabs/T1`
  const sketch = { id: 'F1', type: 'sketch', name: 'Sketch 1', suppressed: false, params: { plane: { kind: 'std', name: 'Top' }, sketch: RECTANGLE } }
  await ok(await request.post(`${tab}/edit`, { data: { op: 'add_feature', feature: sketch } }), 'add the sketch')
  if (opts.solid) {
    const headers = { 'X-Client-Id': CLIENT }
    const dialog = (await ok(await request.post(`${tab}/features/dialog`, { data: { type: 'extrude', picks: [{ part: 'F1', kind: 'region', index: 1 }] }, headers }), 'open Extrude')) as { dialog: string }
    await ok(await request.post(`${tab}/dialog/${dialog.dialog}/update`, { data: { values: { depth: 10 } }, headers }), 'set the depth')
    await ok(await request.post(`${tab}/dialog/${dialog.dialog}/accept`, { data: {}, headers }), 'accept')
  }
  await ok(await request.post(`/documents/${id}/save`, { data: {} }), 'save')
  await ok(await request.post(`/documents/${id}/close`, { data: {} }), 'close')
  return id
}

/** Close every Document and delete the fixture's folder, if there is one. */
export async function dropFixture(request: APIRequestContext): Promise<void> {
  await closeAll(request)
  removeDocument(FIXTURE)
}
