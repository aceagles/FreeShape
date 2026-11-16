// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { decodeOutputs, decodeWire, elementName, faceOfTriangle, isStudio, WireError } from './index'
import type { ElementKind, StudioOutputs } from './index'

const dir = new URL('../../fixtures/', import.meta.url)
const bytes = readFileSync(new URL('bracket.outputs.fsw', dir))
const expected = JSON.parse(readFileSync(new URL('bracket.expected.json', dir), 'utf8')) as {
  tab: string
  kind: string
  parts: number
  triangles: number
  edges: number
  faces: number
  vertices: number
  sketches: number
  features: [id: string, type: string, label: string, name: string][]
  rollback: number
  can_undo: boolean
  can_redo: boolean
  names: { part: number; kind: ElementKind; engine_index: number; index: number; name: string }[]
}

/** An ArrayBuffer holding exactly the fixture, as fetch().arrayBuffer() would give it. */
function payload(): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

function studio(): StudioOutputs {
  const out = decodeOutputs(payload())
  if (!isStudio(out)) throw new Error('fixture is not a studio')
  return out
}

function codeOf(fn: () => unknown): string {
  try {
    fn()
  } catch (e) {
    expect(e).toBeInstanceOf(WireError)
    return (e as WireError).code
  }
  throw new Error('did not throw')
}

describe('decoding the bracket fixture', () => {
  it('matches the counts the generator stored', () => {
    const out = studio()
    expect(out.tab).toBe(expected.tab)
    expect(out.kind).toBe(expected.kind)
    expect(out.parts).toHaveLength(expected.parts)
    expect(out.sketches).toHaveLength(expected.sketches)
    const meshes = out.parts.map((p) => p.mesh!)
    expect(meshes.reduce((n, m) => n + m.indices.length / 3, 0)).toBe(expected.triangles)
    expect(meshes.reduce((n, m) => n + m.edges.length, 0)).toBe(expected.edges)
    expect(meshes.reduce((n, m) => n + m.faceRanges.length, 0)).toBe(expected.faces)
    expect(meshes.reduce((n, m) => n + m.vertices.length / 3, 0)).toBe(expected.vertices)
  })

  it('views the mesh arrays on the response buffer without copying', () => {
    const buf = payload()
    const out = decodeOutputs(buf) as StudioOutputs
    const m = out.parts[0]!.mesh!
    for (const a of [m.positions, m.normals, m.indices, m.vertices, m.edges[0]!]) expect(a.buffer).toBe(buf)
    expect(m.positions.length).toBe(m.normals.length)
  })

  it('also decodes a payload that is not aligned inside its buffer', () => {
    const shifted = new Uint8Array(bytes.length + 3)
    shifted.set(bytes, 3)
    const out = decodeOutputs(shifted.subarray(3)) as StudioOutputs
    expect(out.parts[0]!.mesh!.positions).toEqual(studio().parts[0]!.mesh!.positions)
  })

  it('keeps face ranges, triangle faces and indices consistent (0-based)', () => {
    for (const p of studio().parts) {
      const m = p.mesh!
      let next = 0
      m.faceRanges.forEach(([start, end], face) => {
        expect(start).toBe(next)
        next = end
        for (let t = start; t < end; t++) expect(faceOfTriangle(m, t)).toBe(face)
      })
      expect(next).toBe(m.indices.length / 3)
      const vertexCount = m.positions.length / 3
      for (const i of m.indices) expect(i).toBeLessThan(vertexCount)
      for (const e of m.edges) expect(e.length % 3).toBe(0)
    }
  })

  it('maps every face index to an Element name', () => {
    for (const p of studio().parts) {
      const m = p.mesh!
      expect(p.names.face.size).toBeGreaterThan(0)
      for (let face = 0; face < m.faceRanges.length; face++) {
        expect(typeof elementName(p, 'face', face)).toBe('string')
      }
      for (const key of [...p.names.face.keys(), ...p.names.edge.keys(), ...p.names.vertex.keys()]) {
        expect(key).toBeGreaterThanOrEqual(0)
      }
      expect(Math.max(...p.names.face.keys())).toBeLessThan(m.faceRanges.length)
      expect(Math.max(...p.names.edge.keys())).toBeLessThan(m.edges.length)
      expect(Math.max(...p.names.vertex.keys())).toBeLessThan(m.vertices.length / 3)
    }
  })

  it('looks up three stored names by (part, kind, index)', () => {
    const out = studio()
    expect(expected.names).toHaveLength(3)
    for (const n of expected.names) {
      expect(n.index).toBe(n.engine_index - 1)
      expect(elementName(out.parts[n.part]!, n.kind, n.index)).toBe(n.name)
    }
  })

  it('decodes sketches', () => {
    const s = studio().sketches[0]!
    expect(s.plane.normal).toHaveLength(3)
    expect(s.regions).toHaveLength(s.regionNames.length)
    expect(s.names.face.get(0)).toBe(s.regionNames[0])
  })
})

interface TreeExpected {
  features: { id: string; type: string; label: string; name: string; suppressed: boolean; error: string | null; folder: string | null; hidden: boolean }[]
  folders: { id: string; name: string; open: boolean; suppressed: boolean; failed: number }[]
  rollback: number
  can_undo: boolean
  can_redo: boolean
  errors: Record<string, string>
}

function treeFixture(): { out: StudioOutputs; want: TreeExpected } {
  const b = readFileSync(new URL('tree.outputs.fsw', dir))
  const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
  if (!isStudio(out)) throw new Error('fixture is not a studio')
  const want = JSON.parse(readFileSync(new URL('tree.expected.json', dir), 'utf8')) as TreeExpected
  return { out, want }
}

describe('the manipulators of a preview', () => {
  it('decodes the depth arrow of an extrude preview', () => {
    const b = readFileSync(new URL('preview.outputs.fsw', dir))
    const out = decodeOutputs(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) as ArrayBuffer)
    if (!isStudio(out)) throw new Error('fixture is not a studio')
    const want = JSON.parse(readFileSync(new URL('preview.expected.json', dir), 'utf8')) as {
      manipulators: { field: string; flip: string; origin: number[]; dir: number[]; value: number; symmetric: boolean; snap: string; min: number | null; label: string }[]
    }
    expect(out.manipulators).toEqual(want.manipulators)
    expect(out.manipulators).toHaveLength(1)
    expect(out.manipulators[0]).toMatchObject({ field: 'depth', flip: 'flip', value: 12, symmetric: false, min: null, snap: 'view', label: '12 mm' })
  })

  it('gives none for committed outputs and for a payload that predates the field', () => {
    expect(studio().manipulators).toEqual([])
  })
})

describe('the feature tree', () => {
  it('lists the bracket features in file order with their types, labels and names', () => {
    const out = studio()
    expect(out.hasTree).toBe(true)
    expect(out.features.map((f) => [f.id, f.type, f.label, f.name])).toEqual(expected.features)
    expect(out.features.map((f) => f.type)).toEqual(['sketch', 'extrude', 'fillet', 'sketch', 'hole', 'sketch', 'extrude', 'chamfer'])
    expect(out.features.map((f) => f.name).slice(0, 3)).toEqual(['Bracket profile', 'Extrude 1', 'Fillet 1'])
    expect(out.features.every((f) => !f.suppressed && f.error === null && f.folder === null)).toBe(true)
    expect(out.folders).toEqual([])
  })

  it('reports no rollback, no undo and no redo on a freshly opened tab', () => {
    const out = studio()
    expect(out.rollback).toBe(out.features.length)
    expect([out.rollback, out.canUndo, out.canRedo]).toEqual([expected.rollback, expected.can_undo, expected.can_redo])
    expect(out.canUndo).toBe(false)
    expect(out.canRedo).toBe(false)
  })

  it('decodes suppressed, failed, folder, hidden and rolled-back states', () => {
    const { out, want } = treeFixture()
    expect(out.features).toEqual(want.features)
    const by = (id: string) => out.features.find((f) => f.id === id)!
    expect(by('F3').suppressed).toBe(true)
    expect(by('F9').error).toBe('Select edges to fillet')
    expect(by('F9').error).toBe(out.errors['F9'])
    expect(by('F4').folder).toBe('D1')
    expect(by('F4').hidden).toBe(true)
    expect(out.features.map((f) => f.id)).toEqual(['F1', 'F2', 'F3', 'F4', 'F5', 'F9', 'F6', 'F7', 'F8'])
    expect(out.folders).toEqual(want.folders)
    expect(out.folders[0]).toMatchObject({ id: 'D1', name: 'Holes', open: true, suppressed: false, failed: 0 })
    expect(out.rollback).toBe(7)
    expect(out.features.slice(out.rollback).map((f) => f.id)).toEqual(['F7', 'F8'])
    expect(by('F2').name).toBe('Body')
    expect(by('F1').name).toBe('Bracket profile')
    expect([out.canUndo, out.canRedo]).toEqual([true, true])
  })

  it('decodes an older payload without the tree fields as an empty tree', () => {
    const root = decodeWire(payload()) as { fields: Record<string, unknown> }
    for (const k of ['features', 'folders', 'rollback', 'can_undo', 'can_redo']) expect(root.fields[k]).toBeDefined()
    // re-encode the header without the five fields: the same buffers, an older sender
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    const hlen = view.getUint32(4, true)
    const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + hlen)))
    for (const k of ['features', 'folders', 'rollback', 'can_undo', 'can_redo']) delete header.root.f[k]
    const text = new TextEncoder().encode(JSON.stringify(header))
    const headerLen = text.length + ((8 - ((8 + text.length) % 8)) % 8)
    const start = 8 + hlen + ((8 - ((8 + hlen) % 8)) % 8)
    const buffers = bytes.subarray(start)
    const old = new Uint8Array(8 + headerLen + buffers.length)
    old.set(bytes.subarray(0, 4))
    new DataView(old.buffer).setUint32(4, headerLen, true)
    old.set(text, 8)
    old.fill(0x20, 8 + text.length, 8 + headerLen)
    old.set(buffers, 8 + headerLen)
    const out = decodeOutputs(old) as StudioOutputs
    expect(out.hasTree).toBe(false)
    expect(out.features).toEqual([])
    expect(out.folders).toEqual([])
    expect([out.rollback, out.canUndo, out.canRedo]).toEqual([0, false, false])
    expect(out.parts).toHaveLength(expected.parts)
  })
})

describe('refusing bad payloads', () => {
  it('throws bad-magic for a wrong magic', () => {
    const b = new Uint8Array(bytes)
    b[0] = 0x58
    expect(codeOf(() => decodeWire(b))).toBe('bad-magic')
    expect(() => decodeWire(b)).toThrow(/FSW1/)
  })

  it('throws too-short for fewer than 8 bytes', () => {
    expect(codeOf(() => decodeWire(new Uint8Array(bytes.subarray(0, 5))))).toBe('too-short')
  })

  it('throws truncated-header when the header runs past the end', () => {
    expect(codeOf(() => decodeWire(new Uint8Array(bytes.subarray(0, 40))))).toBe('truncated-header')
  })

  it('throws truncated-buffers when the buffer region is cut', () => {
    expect(codeOf(() => decodeWire(new Uint8Array(bytes.subarray(0, bytes.length - 8))))).toBe('truncated-buffers')
  })

  it('throws trailing-bytes for extra bytes', () => {
    const b = new Uint8Array(bytes.length + 8)
    b.set(bytes)
    expect(codeOf(() => decodeWire(b))).toBe('trailing-bytes')
  })

  it('throws bad-header for a header that is not JSON', () => {
    const b = new Uint8Array(bytes)
    b[8] = 0x7b
    b[9] = 0x7b
    expect(codeOf(() => decodeWire(b))).toBe('bad-header')
  })

  it('throws bad-structure for a valid payload that is not outputs', () => {
    const header = new TextEncoder().encode('{"version":1,"buffers_size":0,"root":[1,2]}')
    const padded = new Uint8Array(Math.ceil((8 + header.length) / 8) * 8)
    padded.set([0x46, 0x53, 0x57, 0x31])
    new DataView(padded.buffer).setUint32(4, padded.length - 8, true)
    padded.set(header, 8)
    padded.fill(0x20, 8 + header.length)
    expect(codeOf(() => decodeOutputs(padded))).toBe('bad-structure')
  })
})
