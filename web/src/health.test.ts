import { describe, expect, it } from 'vitest'
import { fetchHealth } from './health'

const answer = (body: unknown, status = 200) => (async () => new Response(JSON.stringify(body), { status })) as typeof fetch

describe('fetchHealth', () => {
  it('returns the version', async () => {
    expect(await fetchHealth(answer({ ok: true, version: '1.2.3' }))).toEqual({ ok: true, version: '1.2.3' })
  })
  it('rejects a failed status', async () => {
    await expect(fetchHealth(answer({}, 500))).rejects.toThrow('500')
  })
  it('rejects an answer without a version', async () => {
    await expect(fetchHealth(answer({ ok: true }))).rejects.toThrow('version')
  })
})
