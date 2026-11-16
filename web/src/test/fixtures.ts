import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { decodeOutputs, isStudio } from '../wire'
import type { StudioOutputs } from '../wire'

// vitest runs from web/ (the jsdom environment's own URL class cannot make file URLs)
const dir = resolve(process.cwd(), 'fixtures')

/** The recorded outputs of an example Part Studio: its bytes (what the server sends) and the decoded form. */
export function fixture(name: 'bracket' | 'tree'): { bytes: ArrayBuffer; outputs: StudioOutputs } {
  const b = readFileSync(resolve(dir, `${name}.outputs.fsw`))
  const bytes = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
  const outputs = decodeOutputs(bytes)
  if (!isStudio(outputs)) throw new Error(`${name} is not a Part Studio`)
  return { bytes, outputs }
}
