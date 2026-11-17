import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FakeXhr, installFakeXhr } from '../test/fakeXhr'
import { ApiError } from './api'
import { UploadTooLarge, uploadFile, uploadUrl } from './upload'

beforeEach(() => void installFakeXhr())
afterEach(() => vi.unstubAllGlobals())

const file = (name = 'box.step', bytes = 'ISO-10303-21;') => new File([bytes], name)

describe('uploadFile', () => {
  it('posts the file itself as application/octet-stream with its name in the query', async () => {
    const f = file('my box.step')
    const done = uploadFile(f, { url: '/documents/d/tabs/T1/import', query: { name: f.name, new_tab: '1' } })
    const x = FakeXhr.all[0]!
    expect(x.method).toBe('POST')
    expect(x.url).toBe('/documents/d/tabs/T1/import?name=my+box.step&new_tab=1')
    expect(x.headers['Content-Type']).toBe('application/octet-stream')
    expect(x.body).toBe(f)
    x.reply(200, { tab: { id: 'T1' } })
    await expect(done).resolves.toEqual({ tab: { id: 'T1' } })
  })

  it('reports progress as the body goes out, only when the browser knows the total', async () => {
    const seen: Array<[number, number]> = []
    const done = uploadFile(file(), { url: '/u', onProgress: (a, b) => seen.push([a, b]) })
    const x = FakeXhr.all[0]!
    x.progress(10, 100)
    x.upload.onprogress?.({ lengthComputable: false, loaded: 50, total: 0 })
    x.progress(100, 100)
    expect(seen).toEqual([
      [10, 100],
      [100, 100],
    ])
    x.reply(200, {})
    await done
  })

  it('stops on Cancel: the request is aborted and the promise rejects with an AbortError', async () => {
    const ctl = new AbortController()
    const done = uploadFile(file(), { url: '/u', signal: ctl.signal })
    ctl.abort()
    expect(FakeXhr.all[0]!.aborted).toBe(true)
    await expect(done).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('makes no request when already cancelled', async () => {
    const ctl = new AbortController()
    ctl.abort()
    await expect(uploadFile(file(), { url: '/u', signal: ctl.signal })).rejects.toMatchObject({ name: 'AbortError' })
    expect(FakeXhr.all.length).toBe(0)
  })

  it('refuses a file over the limit without a request, and takes one exactly at it', async () => {
    const big = file('big.step', 'x'.repeat(11))
    await expect(uploadFile(big, { url: '/u', limit: 10 })).rejects.toBeInstanceOf(UploadTooLarge)
    expect(FakeXhr.all.length).toBe(0)
    const done = uploadFile(file('ok.step', 'x'.repeat(10)), { url: '/u', limit: 10 })
    expect(FakeXhr.all.length).toBe(1)
    FakeXhr.all[0]!.reply(200, {})
    await done
  })

  it("rejects with the server's own text on a refusal, and says so when the server did not answer", async () => {
    const a = uploadFile(file(), { url: '/u' })
    FakeXhr.all[0]!.reply(413, { error: 'body over 67108864 bytes' })
    await expect(a).rejects.toMatchObject({ status: 413, message: 'body over 67108864 bytes' })
    await a.catch((e: unknown) => expect(e).toBeInstanceOf(ApiError))
    const b = uploadFile(file(), { url: '/u' })
    FakeXhr.all[1]!.onerror?.()
    await expect(b).rejects.toMatchObject({ status: 0 })
  })

  it('adds the query to a route that has one', () => {
    expect(uploadUrl('/a', {})).toBe('/a')
    expect(uploadUrl('/a?x=1', { name: 'n' })).toBe('/a?x=1&name=n')
  })
})
