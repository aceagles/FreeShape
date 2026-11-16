import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import type { APIRequestContext } from '@playwright/test'

// The Documents the served client runs against, seen from a spec: the root and data directory scripts/web/common.sh
// made (FS_DOCS_ROOT, FS_DATA_DIR), and the app's own lock and registry code (scripts/web/doc_tools.py) to write a
// lock for another program or to delete a Document a spec made.

const repo = resolve(import.meta.dirname, '../../..')

function tools(...args: string[]): void {
  const py = process.env.FS_PY
  if (!py || !process.env.FS_DOCS_ROOT || !process.env.FS_DATA_DIR) throw new Error('FS_PY, FS_DOCS_ROOT and FS_DATA_DIR are not set: run this through scripts/web/e2e.sh or shots.sh')
  try {
    execFileSync(py, ['scripts/web/doc_tools.py', ...args], { cwd: repo, stdio: 'pipe', timeout: 120_000, env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
  } catch (e) {
    const err = e as { stderr?: Buffer; message: string }
    throw new Error(`doc_tools.py ${args[0]} failed: ${err.stderr?.toString().trim().split('\n').pop() ?? err.message}`, { cause: e })
  }
}

const docsRoot = () => process.env.FS_DOCS_ROOT ?? ''
const dataDir = () => process.env.FS_DATA_DIR ?? ''

/** Write a live lock on the served copy of a Document (its folder name under the root) for another program. */
export const holdLock = (folder: string, program = 'desktop') => tools('hold', docsRoot(), folder, program)
/** The same, once the server has let go of every Document: a lock the server holds is not another program's to write. */
export async function holdLockClosed(request: APIRequestContext, folder: string, program = 'desktop'): Promise<void> {
  await closeAll(request)
  holdLock(folder, program)
}
export const releaseLock = (folder: string) => tools('release', docsRoot(), folder)
/** Delete a Document folder a spec made, and its registry entry. */
export const removeDocument = (folder: string) => tools('remove', docsRoot(), dataDir(), folder)

interface Row {
  id: string
  name: string
  open: boolean
}
export async function listRows(request: APIRequestContext): Promise<Row[]> {
  return (await (await request.get('/documents')).json()).documents as Row[]
}
export async function idOf(request: APIRequestContext, name: string): Promise<string> {
  const row = (await listRows(request)).find((r) => r.name === name)
  if (!row) throw new Error(`the server lists no Document called ${name}`)
  return row.id
}
/** Close every open Document, throwing away what was not saved (and the autosave of it), so the next state starts from the files. */
export async function closeAll(request: APIRequestContext): Promise<void> {
  for (const row of await listRows(request)) {
    if (row.open) await request.post(`/documents/${row.id}/close`, { data: { discard: true } })
  }
}
/** Delete the recoverable autosave of a Document, if it has one (it needs the Document open for a moment). */
export async function discardAutosaveOf(request: APIRequestContext, name: string): Promise<void> {
  const id = await idOf(request, name)
  await request.post(`/documents/${id}/open`, { data: {} })
  await request.post(`/documents/${id}/discard-autosave`, { data: {} })
  await request.post(`/documents/${id}/close`, { data: { discard: true } })
}
