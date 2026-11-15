import { WireError } from './errors'

/**
 * The generic reader for the engine wire format (freeshape/engine/wire.py).
 *
 *   offset 0  "FSW1"   offset 4  H (uint32 LE)   offset 8  H bytes of JSON header, space padded to 8
 *   then the buffer region (8-aligned buffers). The header is {version: 1, buffers_size, root}.
 *
 * Node mapping: null, boolean, number, string, array and plain object map to themselves; tagged values map to
 *   tuple -> array, set / frozenset -> Set, dict -> Map (any key type), int (beyond 2**53) -> bigint,
 *   float (nan, inf) -> number, bytes -> Uint8Array view, npscalar -> number or bigint,
 *   ndarray -> {@link NdArray} (a typed array over the payload: no copy when the offset is aligned),
 *   dc -> {@link DataclassNode}.
 * A Python dict with only string keys is a plain object; an int-keyed dict is a Map, except that an EMPTY one
 * is also a plain `{}`. Use {@link intKeyMap} to read either.
 */

export type TypedArray =
  | Int8Array
  | Uint8Array
  | Int16Array
  | Uint16Array
  | Int32Array
  | Uint32Array
  | Float32Array
  | Float64Array
  | BigInt64Array
  | BigUint64Array

export interface NdArray {
  readonly dtype: string
  readonly shape: readonly number[]
  readonly data: TypedArray
}

/** A contract dataclass: its class name and its fields, decoded. */
export interface DataclassNode {
  readonly cls: string
  readonly fields: Readonly<Record<string, unknown>>
}

export function isNdArray(x: unknown): x is NdArray {
  return typeof x === 'object' && x !== null && 'dtype' in x && 'shape' in x && 'data' in x
}

export function isDataclass(x: unknown): x is DataclassNode {
  return typeof x === 'object' && x !== null && 'cls' in x && 'fields' in x
}

const MAX_DEPTH = 64
const LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1

type Ctor = { new (buffer: ArrayBuffer, byteOffset: number, length: number): TypedArray; BYTES_PER_ELEMENT: number }

const DTYPES: Record<string, Ctor> = {
  '|i1': Int8Array,
  '|u1': Uint8Array,
  '<i2': Int16Array,
  '<u2': Uint16Array,
  '<i4': Int32Array,
  '<u4': Uint32Array,
  '<f4': Float32Array,
  '<f8': Float64Array,
  '<i8': BigInt64Array,
  '<u8': BigUint64Array,
}

export type WireInput = ArrayBuffer | ArrayBufferView

/** Decode a whole payload to its root value. Throws {@link WireError}. */
export function decodeWire(input: WireInput): unknown {
  if (!LITTLE_ENDIAN) throw new WireError('unsupported', 'big-endian hosts are not supported')
  const bytes =
    input instanceof ArrayBuffer
      ? new Uint8Array(input)
      : new Uint8Array(input.buffer as ArrayBuffer, input.byteOffset, input.byteLength)
  if (bytes.length < 8) {
    throw new WireError('too-short', `payload is ${bytes.length} bytes, shorter than the 8-byte prefix`)
  }
  if (bytes[0] !== 0x46 || bytes[1] !== 0x53 || bytes[2] !== 0x57 || bytes[3] !== 0x31) {
    throw new WireError('bad-magic', 'payload does not start with the FSW1 magic')
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const hlen = view.getUint32(4, true)
  if (8 + hlen > bytes.length) {
    throw new WireError('truncated-header', `header length ${hlen} runs past the end of the ${bytes.length}-byte payload`)
  }
  let header: unknown
  try {
    header = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(8, 8 + hlen)))
  } catch (e) {
    throw new WireError('bad-header', `header is not valid UTF-8 JSON: ${(e as Error).message}`)
  }
  if (!isRecord(header) || header['version'] !== 1 || !('root' in header)) {
    throw new WireError('bad-header', "header is not {'version': 1, 'buffers_size': N, 'root': ...}")
  }
  const size = header['buffers_size']
  if (typeof size !== 'number' || !Number.isInteger(size) || size < 0) {
    throw new WireError('bad-header', `header buffers_size ${String(size)} is not a non-negative integer`)
  }
  const start = Math.ceil((8 + hlen) / 8) * 8
  if (bytes.length < start + size) {
    throw new WireError(
      'truncated-buffers',
      `payload is truncated: the header declares ${size} buffer bytes from offset ${start}, the payload ends at ${bytes.length}`,
    )
  }
  if (bytes.length > start + size) {
    throw new WireError('trailing-bytes', `${bytes.length - start - size} unexpected bytes after the buffer region`)
  }
  return new Reader(bytes, start, size).node(header['root'], 0)
}

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x)
}

class Reader {
  constructor(
    private readonly bytes: Uint8Array,
    private readonly start: number,
    private readonly size: number,
  ) {}

  private span(node: Record<string, unknown>): [number, number] {
    const off = node['off']
    const len = node['len']
    if (!isIndex(off) || !isIndex(len)) throw bad(`buffer slice has a bad offset or length: ${String(off)}, ${String(len)}`)
    if (off + len > this.size) throw bad(`buffer slice [${off}:${off + len}] lies outside the ${this.size}-byte buffer region`)
    return [this.start + off, len]
  }

  node(x: unknown, depth: number): unknown {
    if (depth > MAX_DEPTH) throw bad(`header nests deeper than ${MAX_DEPTH} levels`)
    if (x === null || typeof x === 'boolean' || typeof x === 'string' || typeof x === 'number') return x
    if (Array.isArray(x)) return x.map((v) => this.node(v, depth + 1))
    if (!isRecord(x)) throw bad(`unexpected ${typeof x} in the header`)
    if (!('$' in x)) {
      const out: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(x)) out[k] = this.node(v, depth + 1)
      return out
    }
    return this.tagged(x, depth)
  }

  private list(x: Record<string, unknown>, depth: number): unknown[] {
    const v = x['v']
    if (!Array.isArray(v)) throw bad(`tag ${String(x['$'])} needs a list under 'v'`)
    return v.map((e) => this.node(e, depth + 1))
  }

  private tagged(x: Record<string, unknown>, depth: number): unknown {
    const tag = x['$']
    switch (tag) {
      case 'tuple':
        return this.list(x, depth)
      case 'set':
      case 'frozenset':
        return new Set(this.list(x, depth))
      case 'dict': {
        const pairs = x['items']
        if (!Array.isArray(pairs) || pairs.some((p) => !Array.isArray(p) || p.length !== 2)) {
          throw bad("tag 'dict' needs 'items': a list of [key, value] pairs")
        }
        return new Map(
          (pairs as unknown[][]).map((p) => [this.node(p[0], depth + 1), this.node(p[1], depth + 1)] as const),
        )
      }
      case 'int': {
        const v = x['v']
        if (typeof v !== 'string' || !/^-?\d+$/.test(v)) throw bad("tag 'int' needs a decimal string under 'v'")
        return BigInt(v)
      }
      case 'float': {
        const v = x['v']
        if (v === 'nan') return NaN
        if (v === 'inf') return Infinity
        if (v === '-inf') return -Infinity
        throw bad(`tag 'float' holds ${String(v)}, not nan, inf or -inf`)
      }
      case 'bytes': {
        const [at, len] = this.span(x)
        return this.bytes.subarray(at, at + len)
      }
      case 'ndarray':
        return this.array(x)
      case 'npscalar': {
        dtypeOf(x)
        return this.node(x['v'], depth + 1)
      }
      case 'dc': {
        const cls = x['t']
        const f = x['f']
        if (typeof cls !== 'string' || !isRecord(f)) throw bad("tag 'dc' needs a class name 't' and fields 'f'")
        const fields: Record<string, unknown> = {}
        for (const [k, v] of Object.entries(f)) fields[k] = this.node(v, depth + 1)
        return { cls, fields } satisfies DataclassNode
      }
      default:
        throw bad(typeof tag === 'string' ? `unknown tag ${tag}` : 'tag is not a string')
    }
  }

  private array(x: Record<string, unknown>): NdArray {
    const name = dtypeOf(x)
    const ctor = DTYPES[name]
    if (!ctor) throw new WireError('unsupported', `dtype ${name} is not supported by this reader`)
    const shape = x['shape']
    if (!Array.isArray(shape) || shape.length > 8 || shape.some((d) => !isIndex(d))) throw bad(`bad array shape ${JSON.stringify(shape)}`)
    const dims = shape as number[]
    const count = dims.reduce((a, b) => a * b, 1)
    const [at, len] = this.span(x)
    if (count * ctor.BYTES_PER_ELEMENT !== len) {
      throw bad(`array shape [${dims.join(', ')}] of ${name} needs ${count * ctor.BYTES_PER_ELEMENT} bytes, the slice has ${len}`)
    }
    const abs = this.bytes.byteOffset + at
    // The writer 8-aligns every buffer, so this is a view on the payload. A payload sliced at an odd offset of a
    // larger ArrayBuffer cannot be viewed in place: copy that one buffer.
    const data =
      abs % ctor.BYTES_PER_ELEMENT === 0
        ? new ctor(this.bytes.buffer as ArrayBuffer, abs, count)
        : new ctor(this.bytes.slice(at, at + len).buffer as ArrayBuffer, 0, count)
    return { dtype: name, shape: dims, data }
  }
}

function dtypeOf(x: Record<string, unknown>): string {
  const d = x['dtype']
  if (typeof d !== 'string' || !(d in DTYPES || d === 'bool' || d === '<f2')) throw bad(`dtype ${String(d)} is not allowed`)
  return d
}

function isIndex(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0
}

function bad(message: string): WireError {
  return new WireError('bad-node', message)
}

/** Read an int-keyed Python dict (a Map, or the plain `{}` an empty one arrives as) into a Map. */
export function intKeyMap(x: unknown): Map<number, unknown> {
  if (x instanceof Map) {
    const out = new Map<number, unknown>()
    for (const [k, v] of x) {
      if (typeof k !== 'number') throw new WireError('bad-structure', `expected an integer key, got ${typeof k}`)
      out.set(k, v)
    }
    return out
  }
  if (isRecord(x) && Object.keys(x).length === 0) return new Map()
  throw new WireError('bad-structure', 'expected an integer-keyed dict')
}
