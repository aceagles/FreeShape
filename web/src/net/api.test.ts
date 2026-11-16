import { afterEach, expect, it, vi } from 'vitest'
import { stubFetch } from '../test/fakes'
import { ApiError, addTab, deleteTab, fetchOutputsBytes, openDocument, postEdit, renameTab, saveDocument } from './api'
import { fetchStudio } from '../viewport/StudioViewport'

afterEach(() => vi.unstubAllGlobals())

it('asks for outputs under the Document and the tab', async () => {
  const calls = stubFetch(() => ({ bytes: new ArrayBuffer(4) }))
  await fetchOutputsBytes('doc-1', 'T1')
  expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(['GET /documents/doc-1/tabs/T1/outputs'])
})

it('fetchStudio uses the Document route, not the old /tabs one', async () => {
  const calls = stubFetch(() => ({ status: 500 }))
  await expect(fetchStudio('doc-1', 'T2')).rejects.toBeInstanceOf(ApiError)
  expect(calls[0]!.url).toBe('/documents/doc-1/tabs/T2/outputs')
})

it('sends an edit to the Document and tab with the client id and the base revision', async () => {
  const calls = stubFetch(() => ({ json: { rebuilt: ['T1'] } }))
  expect(await postEdit('doc-1', 'T1', { op: 'set_param', name: 'x' }, 'abcdefgh', 7)).toEqual({ rebuilt: ['T1'] })
  expect(calls[0]).toMatchObject({
    method: 'POST',
    url: '/documents/doc-1/tabs/T1/edit',
    body: { op: 'set_param', name: 'x' },
    headers: { 'X-Client-Id': 'abcdefgh', 'X-Base-Revision': '7' },
  })
  await postEdit('doc-1', 'T1', { op: 'a' }, 'abcdefgh', null)
  expect(calls[1]!.headers['X-Base-Revision']).toBeUndefined()
})

it('uses the Document routes for open, save, add, rename and delete', async () => {
  const calls = stubFetch(() => ({ json: {} }))
  await openDocument('d')
  await saveDocument('d', 'abcdefgh')
  await addTab('d', 'assembly')
  await renameTab('d', 'T1', 'New')
  await deleteTab('d', 'T1')
  expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
    'POST /documents/d/open',
    'POST /documents/d/save',
    'POST /documents/d/tabs',
    'POST /documents/d/tabs/T1/rename',
    'DELETE /documents/d/tabs/T1',
  ])
  expect(calls[2]!.body).toEqual({ kind: 'assembly' })
  expect(calls[3]!.body).toEqual({ name: 'New' })
})

it('turns a failed answer into an ApiError with the status, the text and the body', async () => {
  stubFetch(() => ({ status: 409, json: { error: 'in use', linked_by: [{ id: 'T2', name: 'Assembly 1' }] } }))
  const e = await deleteTab('d', 'T1').catch((x: unknown) => x)
  expect(e).toBeInstanceOf(ApiError)
  expect((e as ApiError).status).toBe(409)
  expect((e as ApiError).message).toBe('in use')
  expect((e as ApiError).linkedBy).toEqual([{ id: 'T2', name: 'Assembly 1' }])
})

it('says the server did not answer when the request fails outright', async () => {
  vi.stubGlobal('fetch', async () => {
    throw new TypeError('Load failed')
  })
  const e = await openDocument('d').catch((x: unknown) => x)
  expect((e as ApiError).status).toBe(0)
  expect((e as ApiError).message).toMatch(/did not answer/)
})
