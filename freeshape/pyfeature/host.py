"""The runner registered in features.RUNNERS for every "py:" feature type.

`run(state, f)` looks the type up in the registry (never importing anything named by `f`), validates the
stored parameters against the class's declarations -- a received Document is hostile data -- calls `build`
with `inputs.make` set to a naming builder (`namedbuild.Builder`), and adds each solid of the result as a
new part carrying its element map in `state.names`. In this slice a Python feature only creates parts.

Stored last build (I4.7). `build_record` turns a feature's parts into the `python_builds` entry a Document
saves (BREP and names as blob-store keys); `restore` adds the parts of such an entry back, which is how a
feature whose plugin is missing is locked on its last build -- as FreeCAD keeps a FeaturePython's `Shape`
in the FCStd, so a file whose addon is missing still shows its geometry but cannot recompute. The entry
and the blobs come from the file: they are hostile data, read only through `BlobStore.get` (key checked,
hash verified, size capped) and never used to build a path or an import.
"""
from __future__ import annotations

import hashlib

from .. import blobstore as B
from .. import kernel as K
from .. import naming as N
from . import registry
from .api import Inputs

OP_PYFEATURE = "PYF"                    # namedbuild.OP_PYFEATURE: the op code of seeded Python-feature names
MAX_BLOB_BYTES = B.DEFAULT_MAX_BYTES    # per blob, when restoring a stored build
MAX_STORED_PARTS = 10000

_KIND_OF_LETTER = {N.FACE: "face", N.EDGE: "edge", N.VERTEX: "vertex"}   # engine output format (I3.1)
_LETTER_OF_KIND = {v: k for k, v in _KIND_OF_LETTER.items()}


def validate(cls, params, variables) -> dict:
    """The parameters `build` receives: one value per declaration, checked by type and range. Unknown keys
    are ignored and missing keys take their default. Raises K.KernelError naming the parameter's label."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise K.KernelError(f"{cls.label}: parameters must be a mapping")
    out = {}
    for p in cls.params:
        out[p.key] = p.check(params.get(p.key, p.default), variables)
    return out


def _seeded(shape, fid):
    """A plain shape's index names (`f3`, `e7`, `v2`), the owner's rule for untagged elements. A naming
    failure leaves the shape unnamed rather than failing the feature (as name_feature does)."""
    try:
        return N.seed(shape, fid, OP_PYFEATURE)
    except Exception:
        return None


def _named_solids(result, label, fid):
    """[(solid, NamedShape or None)] of a build result: a shape, a NamedShape, or a (nested) list or tuple
    of either. Each solid takes the names of its own sub-shapes (`NamedShape.restrict`)."""
    from OCP.TopoDS import TopoDS_Shape
    if isinstance(result, N.NamedShape):
        named, shape = result, result.shape
    elif isinstance(result, TopoDS_Shape):
        if result.IsNull():
            return []
        named, shape = None, result
    elif isinstance(result, (list, tuple)):
        out = []
        for r in result:
            out += _named_solids(r, label, fid)
        return out
    else:
        raise K.KernelError(f"{label}: build returned {type(result).__name__}, not a shape")
    if shape is None or shape.IsNull():
        return []
    solids = K.solids(shape)
    if named is None and solids:
        named = _seeded(shape, fid)
    out = []
    for s in solids:
        try:
            out.append((s, named.restrict(s) if named is not None else None))
        except Exception:
            out.append((s, None))
    return out


def _builder(fid, label):
    from ..namedbuild import Builder
    try:
        return Builder(fid)
    except N.NamingError as e:
        raise K.KernelError(f"{label}: {e}") from e


def run(state, f):
    cls = registry.get(f.get("type"))
    if cls is None:
        registry.load_user_plugins()
        cls = registry.get(f.get("type"))
    if cls is None:
        raise K.KernelError(registry.missing_message(f))
    params = validate(cls, f.get("params"), state.vars)
    inputs = Inputs(feature_id=f["id"], vars=dict(state.vars), make=_builder(f["id"], cls.label))
    try:
        result = cls().build(params, inputs)
        solids = _named_solids(result, cls.label, f["id"])
    except K.KernelError:
        raise
    except (Exception, SystemExit) as e:   # a plugin calling sys.exit must not end the app
        raise K.KernelError(f"{cls.label}: {type(e).__name__}: {e}") from e
    if not solids:
        raise K.KernelError(f"{cls.label}: the build made no solid")
    for s, named in solids:
        part = state.new_part(s, f["id"])
        state.set_named(part.id, named)


# --------------------------------------------------------------------------- stored last build

def names_json(named) -> dict:
    """A part's element map in the engine output format: {"face": {index: name}, "edge": ..., "vertex": ...},
    index 1-based in TopExp.MapShapes order of the part's shape. Empty kinds when the part is unnamed."""
    out = {"face": {}, "edge": {}, "vertex": {}}
    for (letter, index), name in (named.names.items() if named is not None else ()):
        kind = _KIND_OF_LETTER.get(letter)
        if kind is not None:
            out[kind][int(index)] = name
    return out


def build_record(state, fid, plugin):
    """(entry, blobs) for the parts feature `fid` made in `state` (ids `<fid>/<n>`, in creation order).
    `entry` is the `python_builds` value {"plugin", "parts": [{"id", "brep", "names"}]}; `blobs` maps each
    key to its bytes, to put in the blob store before the JSON naming them is written."""
    prefix = f"{fid}/"
    pids = [pid for pid in state.parts if isinstance(pid, str) and pid.startswith(prefix)]
    pids.sort(key=lambda p: int(p[len(prefix):]) if p[len(prefix):].isdigit() else 0)
    parts, blobs = [], {}
    for pid in pids:
        brep = B.brep_bytes(state.parts[pid].shape)
        names = B.dump_json(names_json(state.named(pid)))
        rec = {"id": pid}
        for field, data in (("brep", brep), ("names", names)):
            key = hashlib.sha256(data).hexdigest()
            blobs[key] = data
            rec[field] = key
        parts.append(rec)
    return {"plugin": dict(plugin), "parts": parts}, blobs


def entry_plugin(entry, f) -> tuple[str, str]:
    """(plugin id, version) to show for a stored build: the entry's own record, else the feature's."""
    rec = entry.get("plugin") if isinstance(entry, dict) else None
    if isinstance(rec, dict) and isinstance(rec.get("id"), str):
        return registry.recorded_plugin({"plugin": rec})
    return registry.recorded_plugin(f)


def locked_message(entry, f) -> str:
    pid, ver = entry_plugin(entry, f)
    return f'locked: plugin "{pid}" {ver} is not installed; showing its last build'.replace("  ", " ")


def damaged_message(f) -> str:
    name = registry._clip(f.get("name", "") if isinstance(f, dict) else "")
    return f'stored build of "{name}" is damaged'


def _load_names(data, named_shape):
    """Fill `named_shape.names` from a names blob, refusing anything that is not exactly the format."""
    obj = B.load_json(data)
    if not isinstance(obj, dict) or not set(obj) <= set(_LETTER_OF_KIND):
        raise ValueError("names blob is not {face, edge, vertex}")
    for kind, table in obj.items():
        letter = _LETTER_OF_KIND[kind]
        if not isinstance(table, dict):
            raise ValueError("names table is not a mapping")
        count = len(named_shape.keys(letter))
        for index, name in table.items():
            if not (isinstance(index, str) and index.isascii() and index.isdigit()) or not isinstance(name, str):
                raise ValueError("bad names entry")
            i = int(index)
            if not 1 <= i <= count:
                raise ValueError("names index out of range")
            named_shape.names[(letter, i)] = name


def _load_part(store, rec, expected_id, blobs):
    """(solid, NamedShape) of one stored part; adds the bytes it read to `blobs`."""
    if not isinstance(rec, dict) or rec.get("id") != expected_id:
        raise ValueError("stored part id is not the feature's next part id")
    brep = store.get(rec.get("brep"), max_bytes=MAX_BLOB_BYTES)
    names = store.get(rec.get("names"), max_bytes=MAX_BLOB_BYTES)
    shape = B.shape_from_brep(brep)        # only bytes `get` has hash-checked reach a parser
    solids = K.solids(shape)
    if len(solids) != 1:
        raise ValueError("stored part is not one solid")
    named = N.NamedShape(solids[0], {})
    _load_names(names, named)
    blobs[rec["brep"]], blobs[rec["names"]] = brep, names
    return solids[0], named


def restore(state, f, entry, store):
    """Add the parts of a stored build (`entry`, from a Document's `python_builds`) to `state` under their
    stored ids with their stored names. Everything is read and checked before any part is added; a missing
    store, a malformed entry or any refused or unreadable blob raises K.KernelError ("damaged").
    Returns {key: bytes} of the blobs read, so a later save can write them to another folder."""
    fid = f["id"]
    try:
        if store is None:
            raise B.BlobError("the Part Studio has no folder")
        parts = entry.get("parts") if isinstance(entry, dict) else None
        if not isinstance(parts, list) or not parts or len(parts) > MAX_STORED_PARTS:
            raise ValueError("no stored parts")
        start = state.feature_part_counts.get(fid, 0)
        blobs = {}
        loaded = [_load_part(store, rec, f"{fid}/{start + i + 1}", blobs) for i, rec in enumerate(parts)]
    except Exception as e:     # BlobError, a malformed entry, or a parser failure: all mean damaged
        raise K.KernelError(damaged_message(f)) from e
    for (solid, named), rec in zip(loaded, parts):
        part = state.new_part(solid, fid)
        if part.id != rec["id"]:          # cannot happen: ids were checked against the same counter
            raise K.KernelError(damaged_message(f))
        state.set_named(part.id, named)
    return blobs
