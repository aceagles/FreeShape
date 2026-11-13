"""The wire format for engine outputs: `to_wire(obj) -> bytes`, `from_wire(bytes)`. Standard library plus
numpy only. It carries the dataclasses of contract.py (and the plain data inside them) without pickle, so a
payload can come from another process, machine or language and `from_wire` never runs code from it.

Layout (all integers little-endian)
-----------------------------------
    offset 0   4 bytes   magic b"FSW1"
    offset 4   4 bytes   H, the header length in bytes, uint32
    offset 8   H bytes   the header: UTF-8 JSON, then spaces up to the next multiple of 8
    offset B   N bytes   the buffer region, B = 8 + H rounded up to a multiple of 8
The payload is exactly B + N bytes long. The header is the JSON object
    {"version": 1, "buffers_size": N, "root": <node>}
Every buffer sits at an offset (from the start of the buffer region) that is a multiple of 8, so a
JavaScript reader can build a typed array straight on `payload` at `B + off`. Padding bytes are zero.

Nodes (`root` and everything inside it)
---------------------------------------
    null, true, false, string   themselves
    number                      an int or float (a JSON number with "." or "e" is a float, else an int)
    [node, ...]                 a list
    {"key": node, ...}          a dict whose keys are all strings, none of them "$"
    {"$": tag, ...}             a tagged value, by tag:
      "tuple"      "v": [node, ...]                                  tuple
      "set"        "v": [node, ...]                                  set
      "frozenset"  "v": [node, ...]                                  frozenset
      "dict"       "items": [[key node, value node], ...]            any other dict (int, tuple, mixed or
                                                                      "$" keys); insertion order kept
      "int"        "v": "-123456789012345678901"                     an int beyond +-2**53 (decimal text)
      "float"      "v": "nan" | "inf" | "-inf"                       a float JSON cannot hold
      "bytes"      "off": o, "len": n                                bytes = buffer[o:o+n]
      "ndarray"    "dtype": d, "shape": [..], "off": o, "len": n     numpy array, C order, from buffer[o:o+n]
      "npscalar"   "dtype": d, "v": node                             a numpy scalar of dtype d
      "dc"         "t": ClassName, "f": {field: node, ...}           a dataclass of contract.py
`dtype` is one of the allow-list in DTYPES ("bool", "|i1", "|u1", "<i2", "<u2", "<i4", "<u4", "<i8", "<u8",
"<f2", "<f4", "<f8"). `len` is shape product times item size. A buffer is raw little-endian element data.

Choices
-------
* NaN and infinity: JSON has neither, so floats that are not finite are the "float" tag above (-0.0 and
  every finite float are plain JSON numbers that read back bit-exact). A NaN comes back as a NaN; its payload
  bits are not kept. A non-finite number written as a bare JSON number is rejected.
* Types are exact: a subclass of int / str / float / dict / list / tuple (an enum, an IntEnum, a numpy
  matrix, an OrderedDict) is a TypeError, not silently turned into its base. bytearray and memoryview are a
  TypeError too: the contract holds `bytes` only, and a round trip must give the same type back.
* A big-endian array, or a non-contiguous one, is written as little-endian C order, so it reads back as a
  `<` dtype (equal in value). Arrays read back writable and own their memory (copies of the payload).
* A dataclass tag names a class of the REGISTRY (every dataclass defined in contract.py, by class name),
  never a dotted import path. A tag outside it is a ValueError.
* Nesting deeper than MAX_DEPTH, in either direction, is a ValueError (so a cycle cannot recurse for ever).

Errors: `to_wire` raises TypeError for an object it cannot carry, naming its type; `from_wire` raises
ValueError, with a specific message, for any malformed payload.
"""
from __future__ import annotations

import dataclasses
import json
import math
import struct

import numpy as np

from . import contract

MAGIC = b"FSW1"
VERSION = 1
MAX_DEPTH = 64
MAX_NDIM = 8
_ALIGN = 8
_MAX_SAFE_INT = 2 ** 53

# the numpy dtypes a payload may hold: the wire name -> the dtype. No object, string, structured or complex.
DTYPES = {name: np.dtype(name) for name in
          ("bool", "|i1", "|u1", "<i2", "<u2", "<i4", "<u4", "<i8", "<u8", "<f2", "<f4", "<f8")}
_WIRE_NAME = {dt: name for name, dt in DTYPES.items()}

# tag -> class, every dataclass defined in contract.py. Built once, from the module, never from a payload.
REGISTRY = {name: obj for name, obj in vars(contract).items()
            if isinstance(obj, type) and dataclasses.is_dataclass(obj) and obj.__module__ == contract.__name__}
_TAG_OF = {cls: name for name, cls in REGISTRY.items()}


def _name(x):
    t = type(x)
    return f"{t.__module__}.{t.__qualname__}"


def _pad(n):
    return -n % _ALIGN


# =========================================================================== encode

class _Writer:
    def __init__(self):
        self.parts = []
        self.size = 0

    def buffer(self, data):
        """Append `data` (bytes) at an 8-aligned offset; returns (offset, length)."""
        off = self.size
        self.parts.append(data)
        pad = _pad(len(data))
        if pad:
            self.parts.append(b"\0" * pad)
        self.size += len(data) + pad
        return off, len(data)

    def node(self, x, depth):
        if depth > MAX_DEPTH:
            raise ValueError(f"nesting deeper than {MAX_DEPTH} levels (a cycle?)")
        t = type(x)
        if x is None or t is bool or t is str:
            return x
        if t is int:
            return x if -_MAX_SAFE_INT <= x <= _MAX_SAFE_INT else {"$": "int", "v": str(x)}
        if t is float:
            if math.isfinite(x):
                return x
            return {"$": "float", "v": "nan" if x != x else ("inf" if x > 0 else "-inf")}
        if t is list:
            return [self.node(v, depth + 1) for v in x]
        if t is tuple:
            return {"$": "tuple", "v": [self.node(v, depth + 1) for v in x]}
        if t is set or t is frozenset:
            return {"$": "set" if t is set else "frozenset", "v": [self.node(v, depth + 1) for v in x]}
        if t is dict:
            if all(type(k) is str and k != "$" for k in x):
                return {k: self.node(v, depth + 1) for k, v in x.items()}
            return {"$": "dict", "items": [[self.node(k, depth + 1), self.node(v, depth + 1)]
                                           for k, v in x.items()]}
        if t is bytes:
            off, n = self.buffer(x)
            return {"$": "bytes", "off": off, "len": n}
        if t is np.ndarray:
            return self.array(x)
        if isinstance(x, np.generic):
            dt = x.dtype.newbyteorder("<") if x.dtype.byteorder == ">" else x.dtype
            if dt not in _WIRE_NAME:
                raise TypeError(f"cannot send numpy scalar of dtype {x.dtype} ({_name(x)})")
            return {"$": "npscalar", "dtype": _WIRE_NAME[dt], "v": self.node(x.item(), depth + 1)}
        if t in _TAG_OF:
            return {"$": "dc", "t": _TAG_OF[t],
                    "f": {f.name: self.node(getattr(x, f.name), depth + 1) for f in dataclasses.fields(x)}}
        raise TypeError(f"cannot send an object of type {_name(x)}")

    def array(self, a):
        dt = a.dtype.newbyteorder("<") if a.dtype.byteorder == ">" else a.dtype
        if dt not in _WIRE_NAME:
            raise TypeError(f"cannot send a numpy array of dtype {a.dtype}")
        data = np.ascontiguousarray(a, dtype=dt).tobytes(order="C")
        off, n = self.buffer(data)
        return {"$": "ndarray", "dtype": _WIRE_NAME[dt], "shape": list(a.shape), "off": off, "len": n}


def to_wire(obj) -> bytes:
    """`obj` (a contract dataclass, or plain data holding them) as wire bytes. TypeError for anything else."""
    w = _Writer()
    root = w.node(obj, 0)
    header = json.dumps({"version": VERSION, "buffers_size": w.size, "root": root},
                        allow_nan=False, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header += b" " * _pad(8 + len(header))
    return b"".join([MAGIC, struct.pack("<I", len(header)), header, *w.parts])


# =========================================================================== decode

def _reject_constant(name):
    raise ValueError(f"header holds the bare JSON constant {name}: non-finite floats must use the float tag")


def _is_int(v):
    return type(v) is int


class _Reader:
    def __init__(self, region):
        self.region = region

    def span(self, node):
        off, n = node.get("off"), node.get("len")
        if not (_is_int(off) and _is_int(n)) or off < 0 or n < 0:
            raise ValueError(f"buffer slice has a bad offset or length: {off!r}, {n!r}")
        if off + n > len(self.region):
            raise ValueError(f"buffer slice [{off}:{off + n}] lies outside the {len(self.region)}-byte buffer region")
        return off, n

    def dtype(self, node):
        d = node.get("dtype")
        if type(d) is not str or d not in DTYPES:
            raise ValueError(f"dtype {d!r} is not allowed")
        return DTYPES[d]

    def node(self, x, depth):
        if depth > MAX_DEPTH:
            raise ValueError(f"header nests deeper than {MAX_DEPTH} levels")
        if x is None or type(x) is bool or type(x) is str or type(x) is int:
            return x
        if type(x) is float:
            if not math.isfinite(x):
                raise ValueError("header holds a non-finite number outside the float tag")
            return x
        if type(x) is list:
            return [self.node(v, depth + 1) for v in x]
        if type(x) is not dict:
            raise ValueError(f"unexpected {type(x).__name__} in the header")
        if "$" not in x:
            return {k: self.node(v, depth + 1) for k, v in x.items()}
        return self.tagged(x, depth)

    def items(self, x, depth):
        v = x.get("v")
        if type(v) is not list:
            raise ValueError(f"tag {x['$']!r} needs a list under 'v'")
        return [self.node(e, depth + 1) for e in v]

    def tagged(self, x, depth):
        tag = x["$"]
        try:
            if tag == "tuple":
                return tuple(self.items(x, depth))
            if tag == "set":
                return set(self.items(x, depth))
            if tag == "frozenset":
                return frozenset(self.items(x, depth))
            if tag == "dict":
                pairs = x.get("items")
                if type(pairs) is not list or any(type(p) is not list or len(p) != 2 for p in pairs):
                    raise ValueError("tag 'dict' needs 'items': a list of [key, value] pairs")
                return {self.node(k, depth + 1): self.node(v, depth + 1) for k, v in pairs}
            if tag == "int":
                v = x.get("v")
                if type(v) is not str:
                    raise ValueError("tag 'int' needs a decimal string under 'v'")
                return int(v)
            if tag == "float":
                v = x.get("v")
                if v not in ("nan", "inf", "-inf"):
                    raise ValueError(f"tag 'float' holds {v!r}, not nan, inf or -inf")
                return float(v)
            if tag == "bytes":
                off, n = self.span(x)
                return bytes(self.region[off:off + n])
            if tag == "ndarray":
                return self.array(x)
            if tag == "npscalar":
                dt = self.dtype(x)
                return dt.type(self.node(x.get("v"), depth + 1))
            if tag == "dc":
                return self.dataclass(x, depth)
        except TypeError as e:               # an unhashable set member or dict key, a scalar that does not fit
            raise ValueError(f"bad value under tag {tag!r}: {e}") from None
        except OverflowError as e:
            raise ValueError(f"bad value under tag {tag!r}: {e}") from None
        raise ValueError(f"unknown tag {tag!r}" if type(tag) is str else "tag is not a string")

    def array(self, x):
        dt = self.dtype(x)
        shape = x.get("shape")
        if type(shape) is not list or len(shape) > MAX_NDIM or any(not _is_int(d) or d < 0 for d in shape):
            raise ValueError(f"bad array shape {shape!r}")
        off, n = self.span(x)
        count = math.prod(shape)
        if count * dt.itemsize != n:
            raise ValueError(f"array shape {shape} of {dt} needs {count * dt.itemsize} bytes, the slice has {n}")
        return np.frombuffer(self.region, dtype=dt, count=count, offset=off).reshape(shape).copy()

    def dataclass(self, x, depth):
        cls = REGISTRY.get(x.get("t")) if type(x.get("t")) is str else None
        if cls is None:
            raise ValueError(f"unknown tag: dataclass {x.get('t')!r} is not in the registry")
        fields = x.get("f")
        if type(fields) is not dict:
            raise ValueError(f"dataclass {cls.__name__} needs its fields under 'f'")
        known = {f.name for f in dataclasses.fields(cls)}
        extra = set(fields) - known
        if extra:
            raise ValueError(f"dataclass {cls.__name__} has no field {sorted(extra)[0]!r}")
        missing = [f.name for f in dataclasses.fields(cls)
                   if f.name not in fields and f.default is dataclasses.MISSING
                   and f.default_factory is dataclasses.MISSING]
        if missing:
            raise ValueError(f"dataclass {cls.__name__} lacks field {missing[0]!r}")
        return cls(**{k: self.node(v, depth + 1) for k, v in fields.items()})


def from_wire(data):
    """The object `to_wire` was given. ValueError, with a specific message, for any malformed payload. Never
    unpickles and never imports a name from the payload."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError(f"payload must be bytes, not {type(data).__name__}")
    data = memoryview(data).cast("B")
    if len(data) < 8:
        raise ValueError(f"payload is {len(data)} bytes, shorter than the 8-byte prefix")
    if bytes(data[:4]) != MAGIC:
        raise ValueError("payload does not start with the FSW1 magic")
    (hlen,) = struct.unpack_from("<I", data, 4)
    if 8 + hlen > len(data):
        raise ValueError(f"header length {hlen} runs past the end of the {len(data)}-byte payload")
    try:
        header = json.loads(bytes(data[8:8 + hlen]).decode("utf-8"), parse_constant=_reject_constant)
    except UnicodeDecodeError as e:
        raise ValueError(f"header is not valid UTF-8: {e}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"header is not valid JSON: {e}") from None
    except RecursionError:
        raise ValueError("header nests too deeply") from None
    if type(header) is not dict or header.get("version") != VERSION or "root" not in header:
        raise ValueError("header is not {'version': 1, 'buffers_size': N, 'root': ...}")
    size = header.get("buffers_size")
    if not _is_int(size) or size < 0:
        raise ValueError(f"header buffers_size {size!r} is not a non-negative integer")
    start = 8 + hlen + _pad(8 + hlen)
    if len(data) < start + size:
        raise ValueError(f"payload is truncated: the header declares {size} buffer bytes from offset {start}, "
                         f"the payload ends at {len(data)}")
    if len(data) > start + size:
        raise ValueError(f"{len(data) - start - size} unexpected bytes after the buffer region")
    try:
        return _Reader(data[start:start + size]).node(header["root"], 0)
    except RecursionError:
        raise ValueError("header nests too deeply") from None


# =========================================================================== for a browser

def mesh_json(mesh_out) -> dict:
    """A `MeshOut` as plain lists a browser can hand to WebGL / three.js. Float lists are flat, x y z per
    point; every index is 0-based.
      positions   [x0, y0, z0, x1, ...]   vertex positions, 3 per vertex          (len = 3 * vertex count)
      normals     [nx0, ny0, nz0, ...]    per-vertex normals, same order           (len = 3 * vertex count)
      indices     [a0, b0, c0, a1, ...]   triangle corners, each an index into the VERTEX list, i.e.
                                          position of corner k is positions[3k : 3k + 3]  (len = 3 * triangles)
      face_ids    [f0, f1, ...]           per triangle, the 1-based face index it belongs to (len = triangles)
      face_ranges [[start, end], ...]     entry i - 1 is face i: its triangles are triangle numbers
                                          start .. end - 1, so indices[3 * start : 3 * end]
      edges       [[x, y, z, ...], ...]   entry i - 1 is edge i: a polyline, 3 floats per point, in order
      vertices    [x, y, z, ...]          the shape's topological vertices, 3 per vertex, vertex-index order
    Coordinates are in the shape's own units (millimetres)."""
    def flat(a):
        return np.asarray(a).reshape(-1).tolist()
    return {
        "positions": flat(mesh_out.verts),
        "normals": flat(mesh_out.normals),
        "indices": flat(mesh_out.tris),
        "face_ids": flat(mesh_out.tri_face),
        "face_ranges": [[int(a), int(b)] for a, b in mesh_out.face_ranges],
        "edges": [flat(p) for p in mesh_out.edge_polys],
        "vertices": flat(mesh_out.vertices),
    }
