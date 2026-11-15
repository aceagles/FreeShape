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
