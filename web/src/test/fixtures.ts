import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { decodeOutputs, isStudio } from '../wire'
import type { StudioOutputs } from '../wire'

// vitest runs from web/ (the jsdom environment's own URL class cannot make file URLs)
const dir = resolve(process.cwd(), 'fixtures')

/** The recorded outputs of an example Part Studio: its bytes (what the server sends) and the decoded form. */
export function fixture(name: 'bracket' | 'tree' | 'bracket.canvas'): { bytes: ArrayBuffer; outputs: StudioOutputs } {
  const b = readFileSync(resolve(dir, `${name}.outputs.fsw`))
  const bytes = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer
  const outputs = decodeOutputs(bytes)
  if (!isStudio(outputs)) throw new Error(`${name} is not a Part Studio`)
  return { bytes, outputs }
}

/**
 * The bracket's bytes with its one part's name, colour or eye changed, as the outputs of a later revision of the
 * same studio would carry them. The header is JSON, so the part's three fields are rewritten there; the buffers after
 * it keep their place because the header stays padded to a multiple of 8.
 */
export function bracketWithPart(change: { name?: string; color?: [number, number, number]; hidden?: boolean }): ArrayBuffer {
  const { bytes } = fixture('bracket')
  const raw = new Uint8Array(bytes)
  const h = new DataView(bytes).getUint32(4, true)
  const text = new TextDecoder().decode(raw.subarray(8, 8 + h)).trimEnd()
  const old = '"name":"Bracket","color":{"$":"tuple","v":[0.6,0.74,0.85]},"hidden":false,'
  if (!text.includes(old)) throw new Error('the bracket fixture does not look as bracketWithPart expects')
  const next = `"name":${JSON.stringify(change.name ?? 'Bracket')},"color":{"$":"tuple","v":${JSON.stringify(change.color ?? [0.6, 0.74, 0.85])}},"hidden":${change.hidden === true},`
  const patched = new TextEncoder().encode(text.replace(old, next))
  const padded = new Uint8Array(Math.ceil(patched.length / 8) * 8).fill(0x20)
  padded.set(patched)
  const out = new Uint8Array(8 + padded.length + (raw.length - 8 - h))
  out.set(raw.subarray(0, 8))
  new DataView(out.buffer).setUint32(4, padded.length, true)
  out.set(padded, 8)
  out.set(raw.subarray(8 + h), 8 + padded.length)
  return out.buffer
}

/** A mate connector of the bracket Part Studio as the outputs carry it (card I10.3): at `o`, z up and x along the x axis unless given. */
export interface BracketConnector {
  id: string
  name: string
  o: [number, number, number]
  z?: [number, number, number]
  x?: [number, number, number]
}

/**
 * The bracket's bytes with these mate connectors (owned by the bracket's part), as the outputs of a studio with such
 * features would carry them. The header is JSON: the empty connector list is replaced, and the padding kept to a multiple of 8.
 */
export function bracketWithConnectors(connectors: readonly BracketConnector[]): ArrayBuffer {
  const { bytes, outputs } = fixture('bracket')
  const raw = new Uint8Array(bytes)
  const h = new DataView(bytes).getUint32(4, true)
  const text = new TextDecoder().decode(raw.subarray(8, 8 + h)).trimEnd()
  const old = '"connectors":[],'
  if (!text.includes(old)) throw new Error('the bracket fixture does not look as bracketWithConnectors expects')
  const node = (c: BracketConnector) => ({
    $: 'dc',
    t: 'MateConnectorOut',
    f: { id: c.id, name: c.name, part: outputs.parts[0]!.id, frame: { o: c.o, z: c.z ?? [0, 0, 1], x: c.x ?? [1, 0, 0] }, hash: `h-${c.id}`, sketch: '' },
  })
  const patched = new TextEncoder().encode(text.replace(old, `"connectors":${JSON.stringify(connectors.map(node))},`))
  const padded = new Uint8Array(Math.ceil(patched.length / 8) * 8).fill(0x20)
  padded.set(patched)
  const out = new Uint8Array(8 + padded.length + (raw.length - 8 - h))
  out.set(raw.subarray(0, 8))
  new DataView(out.buffer).setUint32(4, padded.length, true)
  out.set(padded, 8)
  out.set(raw.subarray(8 + h), 8 + padded.length)
  return out.buffer
}
