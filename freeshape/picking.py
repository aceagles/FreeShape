"""Turning a pick into a stored reference, without Qt (I4.3).

The desktop's `ui.dialogs.pick_to_item` and the Browser client's refs route (serve.py, through the engine's `refs`
request) share this: a pick is a kind, a key, and the set of kinds the field being filled wants; the answer is the
`(kind, ref)` item a feature's params store, or None when the pick cannot fill that field. Nothing here reads a
camera, a hover or a visibility flag: the only model data it needs is the part shapes, the solved sketches and
(for element names) the parts' name maps.

Keys, as the desktop's `Pick.key` has them (indices are 0-based here, in `kernel.faces` / `kernel.edges` order):
    face / edge / vertex   (part id, index)           region   (sketch feature id, region index)
    sketch_line / sketch_point   (sketch feature id, entity id)       part   (part id,)
    plane  ("std", name) or ("plane", feature id)     axis / connector   (feature id,)
"""
from __future__ import annotations

import math

import numpy as np

from . import features as F
from . import kernel as K

# kind -> the wanted kinds that can ever give an item for a pick of that kind (what a refusal reason is told from)
ACCEPTS = {
    "face": {"element", "face", "plane", "edge", "axis", "part"},
    "edge": {"element", "edge", "axis", "path"},
    "vertex": {"vertex"},
    "part": {"part"},
    "region": {"region"},
    "sketch_line": {"region", "path", "axis", "sketch_line"},
    "sketch_point": {"sketch_point"},
    "plane": {"plane"},
    "axis": {"axis_feature", "axis"},
    "connector": {"connector", "axis", "axis_connector"},
}
# every kind a selection field's `want` may hold (ui.dialogs SCHEMAS), the fixed set the route accepts
WANTED = frozenset({"element", "face", "edge", "vertex", "plane", "axis", "part", "path", "region", "sketch_line",
                    "sketch_point", "axis_feature", "axis_connector", "connector", "feature"})
_LABEL = {"element": "a face or an edge", "face": "a face", "edge": "an edge", "vertex": "a vertex",
          "plane": "a plane", "axis": "an axis", "part": "a part", "path": "a chain of curves or an edge",
          "region": "a sketch region", "sketch_line": "a sketch line", "sketch_point": "a sketch point",
          "axis_feature": "an axis", "axis_connector": "a mate connector", "connector": "a mate connector",
          "feature": "a feature"}
_KIND_LABEL = {"face": "a face", "edge": "an edge", "vertex": "a vertex", "part": "a part",
               "region": "a sketch region", "sketch_line": "a sketch line", "sketch_point": "a sketch point",
               "plane": "a plane", "axis": "an axis", "connector": "a mate connector"}


def region_touching(sr, eid):
    """Index of the sketch region whose outline runs along sketch entity `eid` (the smallest such region, so a
    hole's circle picks the hole rather than the plate around it), or None."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    try:
        pts = sr.sketch.polyline(eid, 16)
    except Exception:
        return None
    if not len(pts):
        return None
    u, v = pts[len(pts) // 2]
    P = sr.plane.to_world(u, v)
    vert = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, P))).Vertex()
    best = None
    for ri, face in enumerate(sr.regions):
        dd = None
        for e in K.edges(face):
            ext = BRepExtrema_DistShapeShape(vert, e)
            if ext.IsDone():
                d = ext.Value()
                dd = d if dd is None else min(dd, d)
        if dd is not None and dd < 1e-5 * max(1.0, float(np.linalg.norm(P))) + 1e-6:
            area = K.face_info(face).get("area", 0.0)
            if best is None or area < best[0]:
                best = (area, ri)
    return None if best is None else best[1]


def make_item(part_shapes, sketches, kind, key, want, named=None, engine_names=None):
    """The `(kind, ref)` item of a pick for a field that wants the kinds in `want`, or None.

    `part_shapes`: {part id: shape}. `sketches`: {sketch feature id: SketchResult}. `named`: part id -> that part's
    NamedShape (or None), i.e. `state.named`; with it a picked face, edge or vertex stores the element's name next
    to its signature (a part without a map gives a signature-only reference). `engine_names`:
    {part id: {"face"|"edge"|"vertex": {1-based index: name}}}, the name maps the engine returns: when given, a
    face / edge / vertex name comes from it by the picked element's 1-based index, not from re-finding the element
    in the shape."""
    k = kind
    names = named if named is not None else (lambda pid: None)

    def engine_named(ref, pid, kind, idx):
        # a Part Studio tab drawn from the engine: the name comes from the part's element-name map by
        # the picked element's 1-based index, not from re-finding the element in a rebuilt shape
        n = ((engine_names or {}).get(pid) or {}).get(kind, {}).get(idx + 1)
        if n:
            ref["name"] = n
        return ref
    if "element" in want and k in ("face", "edge"):      # a Mate connector: one face or one edge, never a face's edges
        pid, i = key
        shape = part_shapes.get(pid)
        if shape is None:
            return None
        sub = (K.faces if k == "face" else K.edges)(shape)[i]
        return (k, engine_named(F.make_ref(pid, sub, k, shape, names(pid)), pid, k, i))
    if k == "region" and "region" in want:
        fid, ri = key
        sr = sketches.get(fid)
        if sr is not None and ri < len(sr.regions):
            return ("region", F.region_ref(sr, fid, sr.regions[ri]))
    if k == "face":
        pid, fi = key
        shape = part_shapes.get(pid)
        if shape is None:
            return None
        face = K.faces(shape)[fi]
        if "face" in want:
            return ("face", engine_named(F.make_ref(pid, face, "face", shape, names(pid)), pid, "face", fi))
        if "plane" in want:
            pl = K.planar_face_plane(face)
            if pl is not None:
                return ("plane", {"kind": "face",
                                  "face": engine_named(F.make_ref(pid, face, "face", shape, names(pid)), pid, "face", fi),
                                  "origin": pl.origin.tolist(), "xdir": pl.xdir.tolist()})
        if "edge" in want:          # selecting a face selects all of its edges (fillet/chamfer)
            return ("edges", [F.make_ref(pid, e, "edge", shape, names(pid)) for e in K.edges(face)])
        if "axis" in want and F.round_face_axis(face) is not None:      # a cylinder, cone, torus, disc or ring
            return ("axis", {"kind": "face",
                             "face": engine_named(F.make_ref(pid, face, "face", shape, names(pid)), pid, "face", fi)})
        if "part" in want:
            return ("part", pid)
    if k == "part" and "part" in want:
        return ("part", key[0])
    if k == "edge":
        pid, ei = key
        shape = part_shapes.get(pid)
        if shape is None:
            return None
        edge = K.edges(shape)[ei]
        if "edge" in want:
            return ("edge", engine_named(F.make_ref(pid, edge, "edge", shape, names(pid)), pid, "edge", ei))
        if "axis" in want:
            return ("axis", {"kind": "edge",
                             "edge": engine_named(F.make_ref(pid, edge, "edge", shape, names(pid)), pid, "edge", ei)})
    if k == "sketch_line" and "region" in want:
        # clicking a sketch's line (or circle) picks the closed area it bounds
        fid, eid = key
        sr = sketches.get(fid)
        if sr is None or not sr.regions:
            return None
        ri = region_touching(sr, eid)
        if ri is not None:
            return ("region", F.region_ref(sr, fid, sr.regions[ri]))
        return None
    if k == "sketch_line" and "path" in want:
        fid, eid = key
        return ("path", {"kind": "sketch_chain", "sketch": fid, "entity": eid})
    if k == "edge" and "path" in want:
        pid, ei = key
        shape = part_shapes.get(pid)
        if shape is not None:
            return ("path", {"kind": "edge", "edge": F.make_ref(pid, K.edges(shape)[ei], "edge", shape, names(pid))})
    if k == "sketch_line" and "axis" in want:
        fid, eid = key
        sr = sketches.get(fid)
        if sr is not None and sr.sketch.entities.get(eid, {}).get("type") in ("circle", "arc"):
            return ("axis", {"kind": "sketch_circle", "sketch": fid, "entity": eid})
        return ("axis", {"kind": "sketch_line", "sketch": fid, "line": eid})
    if k == "vertex" and "vertex" in want:
        pid, vi = key
        shape = part_shapes.get(pid)
        if shape is None:
            return None
        pts = K.vertex_points(shape)
        if vi >= len(pts):
            return None
        return ("vertex", engine_named(F.make_vertex_ref(pid, pts[vi], shape, names(pid)), pid, "vertex", vi))
    if k == "plane" and "plane" in want:
        if key[0] == "std":
            return ("plane", {"kind": "std", "name": key[1]})
        return ("plane", {"kind": "plane", "fid": key[1]})
    # the Axis tool's own picks (its refs_sel field asks for these with "sketch_line", "sketch_point",
    # "axis_feature" and "connector"; revolve and the patterns do not)
    if k == "sketch_line" and "sketch_line" in want:
        fid, eid = key
        ref = {"kind": "sketch_line", "sketch": fid, "line": eid}
        if "axis_connector" in want:        # the Plane tool: plane_role needs the entity type, it has no model state
            sr = sketches.get(fid)
            if sr is not None:
                ref["etype"] = sr.sketch.entities.get(eid, {}).get("type", "line")
        return ("sketch_line", ref)
    if k == "sketch_point" and "sketch_point" in want:
        return _sketch_point_item(sketches, key)
    if k == "axis" and ("axis_feature" in want or "axis" in want):
        return ("axis", {"kind": "axis", "fid": key[0]})
    if k == "connector" and "connector" in want:
        return ("connector", {"fid": key[0]})
    if k == "connector" and ("axis" in want or "axis_connector" in want):
        # a mate connector where an axis is asked for: its Z axis, by reference to the connector (the viewport pick
        # does not say which arrow of the triad was hit)
        return ("axis", {"kind": "connector", "fid": key[0], "axis": "z"})
    return None


def _sketch_point_item(sketches, key):
    """A sketch point as the ADR 0011 sketch-geometry reference ("sketch" item), as the Mate connector tool stores it."""
    from . import sketchref
    fid, eid = key
    sr = sketches.get(fid)
    if sr is not None:
        try:
            d = sketchref.describe(sr.sketch.to_dict(), eid, "point")
        except K.KernelError:
            return None
        return ("sketch", {"sketch": fid, "entity": eid, "role": "point", "etype": d["etype"], "sig": d["sig"]})
    return None


def item_name(item):
    """The Element name inside an item `make_item` returned: a face, edge or vertex reference's, or that of the
    face a plane or axis was taken from; None when it has none (a legacy reference, a part, a region ...)."""
    kind, ref = item
    if not isinstance(ref, dict):
        return None
    if kind in ("face", "edge", "vertex", "region") and ref.get("name"):
        return ref["name"]
    sub = ref.get("face") or ref.get("edge")
    return sub.get("name") if kind in ("plane", "axis") and isinstance(sub, dict) else None


def refusal(kind, want, shape=None):
    """(code, message) saying why a pick of `kind` filled no field that wants `want` (call it when `make_item`
    returned None). `shape` is the picked face's shape when the pick was a face. Codes: "wrong_kind" (nothing in
    `want` takes this kind), "not_flat", "no_axis", "no_region", "nothing_here" (the right kind, but no item came
    out)."""
    want = set(want)
    ok = ACCEPTS.get(kind, set()) & want
    if not ok:
        needs = sorted({_LABEL.get(w, w) for w in want if w in _LABEL})
        what = " or ".join(needs) if needs else "something else"
        return "wrong_kind", f"{_KIND_LABEL.get(kind, 'this')} cannot be used here; {what} is needed".capitalize()
    if kind == "face" and shape is not None:
        if ok <= {"plane"}:
            return "not_flat", "That face is not flat"
        if ok <= {"axis"}:
            return "no_axis", "That face is not round, so it has no axis"
        if ok <= {"plane", "axis"}:
            return "no_axis", "That face is neither flat nor round"
    if kind == "sketch_line" and ok == {"region"}:
        return "no_region", "That sketch line does not bound a closed area"
    if kind == "region":
        return "no_region", "That sketch region is not there"
    return "nothing_here", "That cannot be used here"


# ---------------------------------------------------------------------- picks sent over the wire (the refs request)

MAX_PICKS = 200                 # picks in one request
MAX_ID = 200                    # longest part, sketch or entity id
MAX_INDEX = 10_000_000
MAX_COORD = 1e12
PICK_KINDS = ("face", "edge", "vertex", "part", "region", "sketch_line", "sketch_point", "connector")   # connector: I10.3
_INDEXED = ("face", "edge", "vertex", "region")     # picked by 1-based index
_BY_ENTITY = ("sketch_line", "sketch_point")        # picked by sketch entity id
_PICK_FIELDS = {"part", "kind", "index", "entity", "point", "want"}
# a "connector" pick names a Part Studio's mate_connector feature in `part` (no index, no entity)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def parse_pick(i, raw):
    """One pick of a refs request, checked and normalised: {"part", "kind", "index" | "entity", "point", "want"}
    (`point` a list of three floats or None, `want` a sorted list). Raises ValueError, whose message names the
    field as `picks[i].<field>`; it never raises anything else, whatever `raw` holds."""
    at = f"picks[{i}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{at}: must be an object")
    extra = sorted(str(k) for k in raw if k not in _PICK_FIELDS)
    if extra:
        raise ValueError(f"{at}.{extra[0][:40]}: unknown field")
    part, kind = raw.get("part"), raw.get("kind")
    if not isinstance(part, str) or not 1 <= len(part) <= MAX_ID:
        raise ValueError(f"{at}.part: must be a string of 1 to {MAX_ID} characters")
    if not isinstance(kind, str) or kind not in PICK_KINDS:
        raise ValueError(f"{at}.kind: must be one of {', '.join(PICK_KINDS)}")
    out = {"part": part, "kind": kind}
    index, entity = raw.get("index"), raw.get("entity")
    if kind in _INDEXED:
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"{at}.index: must be an integer (1-based) for kind {kind}")
        if not 1 <= index <= MAX_INDEX:
            raise ValueError(f"{at}.index: must be 1 or more, the first {kind} is 1 (got {index})")
        out["index"] = index
    elif index is not None:
        raise ValueError(f"{at}.index: not used for kind {kind}")
    if kind in _BY_ENTITY:
        if not isinstance(entity, str) or not 1 <= len(entity) <= MAX_ID:
            raise ValueError(f"{at}.entity: must be a sketch entity id (a string) for kind {kind}")
        out["entity"] = entity
    elif entity is not None:
        raise ValueError(f"{at}.entity: not used for kind {kind}")
    point = raw.get("point")
    if point is not None:
        if not isinstance(point, list) or len(point) != 3 or not all(_num(c) for c in point):
            raise ValueError(f"{at}.point: must be a list of three numbers")
        try:
            pt = [float(c) for c in point]
        except OverflowError:
            raise ValueError(f"{at}.point: numbers must be finite and under {MAX_COORD:g}") from None
        if not all(math.isfinite(c) and abs(c) <= MAX_COORD for c in pt):
            raise ValueError(f"{at}.point: numbers must be finite and under {MAX_COORD:g}")
        point = pt
    out["point"] = point
    want = raw.get("want")
    if not isinstance(want, list) or not 1 <= len(want) <= len(WANTED):
        raise ValueError(f"{at}.want: must be a list of 1 to {len(WANTED)} kinds")
    for w in want:
        if not isinstance(w, str) or w not in WANTED:
            raise ValueError(f"{at}.want: unknown kind (one of {', '.join(sorted(WANTED))})")
    out["want"] = sorted(set(want))
    return out


def parse_picks(raw):
    """The list of picks of a refs request, each through `parse_pick`; ValueError names the field."""
    if not isinstance(raw, list):
        raise ValueError("picks: must be a list")
    if not 1 <= len(raw) <= MAX_PICKS:
        raise ValueError(f"picks: must hold 1 to {MAX_PICKS} picks (got {len(raw)})")
    return [parse_pick(i, p) for i, p in enumerate(raw)]


def jsonable(x):
    """`x` with numpy arrays and scalars as lists and numbers, tuples as lists: what a saved feature holds."""
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


def result_of(item):
    """The refs answer of a `make_item` item."""
    kind, ref = item
    return {"ok": True, "kind": kind, "ref": jsonable(ref), "name": item_name(item)}
