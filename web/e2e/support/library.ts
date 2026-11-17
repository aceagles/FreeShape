import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import type { Page } from '@playwright/test'

// A file root for the explorer (card I11.7) without a server file root. The e2e server (scripts/web/common.sh) is started with
// no --file-root, so the page's file routes are answered here from web/e2e/fixtures/library/ (two real STEP files and a
// manifest.csv), in the shapes freeshape/serve.py documents. An import by {root, entry} is turned into the upload the server
// does take (the file's bytes), so the Part Studio really gets the part. With `--file-root lib=web/e2e/fixtures/library` on the
// server the same spec would run against the real routes: drop this call.

const dir = resolve(import.meta.dirname, '../fixtures/library')

interface Entry {
  id: string
  name: string
  file: string
  path: string
  size: number
  description: string
  tags: string[]
  licence: string | null
  authors: string | null
  url: string | null
}

/** The CSV of a manifest, a row to an object (the fixture's fields hold no line break; a quoted comma is kept). */
function rows(text: string): Array<Record<string, string>> {
  const split = (line: string): string[] => [...line.matchAll(/("([^"]*)"|[^,]*)(,|$)/g)].slice(0, -1).map((m) => m[2] ?? m[1]!)
  const [head, ...body] = text.trim().split(/\r?\n/)
  const names = split(head!)
  return body.map((l) => Object.fromEntries(split(l).map((v, i) => [names[i]!, v])))
}

function entries(): Entry[] {
  const meta = new Map(rows(readFileSync(join(dir, 'manifest.csv'), 'utf8')).map((r) => [`${r.source}/${r.path}`, r]))
  const out: Entry[] = []
  for (const folder of readdirSync(dir).filter((n) => statSync(join(dir, n)).isDirectory())) {
    for (const file of readdirSync(join(dir, folder)).filter((n) => /\.(step|stp)$/i.test(n))) {
      const path = `${folder}/${file}`
      const m = meta.get(path)
      out.push({ id: Buffer.from(path).toString('hex').slice(0, 16).padEnd(16, '0'), name: file.replace(/\.[^.]+$/, ''), file, path, size: statSync(join(dir, path)).size, description: '', tags: [], licence: m?.licence ?? null, authors: m?.authors ?? null, url: m?.url ?? null })
    }
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

/** The calls the explorer made that reached the import route, as `{ url, body }`, in order. */
export interface Library {
  imports: Array<{ url: string; body: { root: string; entry: string } }>
}

export async function mockLibrary(page: Page): Promise<Library> {
  const all = entries()
  const log: Library = { imports: [] }
  await page.route('**/files', (route) => route.fulfill({ json: { roots: [{ name: 'lib', files: all.length, max_upload: 67108864 }], export_roots: [] } }))
  await page.route(/\/files\/lib(\?|$)/, (route) => {
    const q = new URL(route.request().url()).searchParams
    const folder = q.get('dir') ?? ''
    const words = (q.get('q') ?? '').toLowerCase()
    const inside = all.filter((e) => folder === '' || e.path.startsWith(`${folder}/`))
    const hits = words === '' ? inside.filter((e) => e.path.split('/').slice(0, -1).join('/') === folder) : inside.filter((e) => `${e.name} ${e.path} ${e.tags.join(' ')}`.toLowerCase().includes(words))
    const children = words !== '' ? [] : [...new Set(inside.map((e) => e.path.slice(folder === '' ? 0 : folder.length + 1).split('/')).filter((p) => p.length > 1).map((p) => p[0]!))].sort()
    const offset = Number(q.get('offset') ?? 0)
    const limit = Math.min(Number(q.get('limit') ?? 200), 500)
    return route.fulfill({
      json: {
        root: 'lib',
        dir: folder,
        q: q.get('q') ?? '',
        dirs: children.map((name) => ({ name, path: folder === '' ? name : `${folder}/${name}`, files: inside.filter((e) => e.path.startsWith(`${folder === '' ? '' : `${folder}/`}${name}/`)).length })),
        entries: hits.slice(offset, offset + limit),
        total: hits.length,
        offset,
        limit,
      },
    })
  })
  await page.route(/\/tabs\/[^/]+\/import(\?|$)/, (route) => {
    const req = route.request()
    const body = req.postDataJSON() as { root: string; entry: string } | null
    if (req.method() !== 'POST' || body === null || typeof body.entry !== 'string') return route.fallback()
    log.imports.push({ url: new URL(req.url()).pathname + new URL(req.url()).search, body })
    const e = all.find((x) => x.id === body.entry)
    if (e === undefined) return route.fulfill({ status: 404, json: { error: 'no such entry' } })
    const url = new URL(req.url())
    url.searchParams.set('name', e.file)
    return route.continue({ url: url.toString(), headers: { ...Object.fromEntries(Object.entries(req.headers()).filter(([k]) => k !== 'content-length')), 'content-type': 'application/octet-stream' }, postData: readFileSync(join(dir, e.path)) })
  })
  return log
}
