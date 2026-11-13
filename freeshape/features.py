"""Parametric features and the regeneration engine.

A feature is a plain dict: {"id", "type", "name", "suppressed", "params": {...}}.
Rebuilding replays features in order into a ModelState. Geometry references
(faces, edges, sketch regions) are stored as geometric signatures and re-found
after every rebuild, so editing an earlier feature doesn't break later ones
in the common cases.
"""
from __future__ import annotations

import base64
import math
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np

from . import kernel as K
from . import naming as N
from .sketch import Sketch
from .expr import parse_number, var_names

DEFAULT_COLOR = (0.60, 0.74, 0.85)      # Onshape-style steel blue

# Muted colours for new parts, in creation order (the first is DEFAULT_COLOR). Mid-light so they read on light and dark themes.
PART_PALETTE = (
    DEFAULT_COLOR,              # steel blue
    (0.93, 0.66, 0.45),         # apricot
    (0.55, 0.78, 0.60),         # sage green
    (0.84, 0.58, 0.68),         # dusty rose
    (0.72, 0.66, 0.88),         # lavender
    (0.90, 0.80, 0.45),         # mustard
    (0.50, 0.78, 0.78),         # teal
    (0.80, 0.62, 0.52),         # clay
    (0.66, 0.76, 0.46),         # olive
    (0.62, 0.68, 0.80),         # slate blue
)


def palette_color(n):
    """Colour for the n-th part (1-based) created in a replay."""
    return PART_PALETTE[(n - 1) % len(PART_PALETTE)]

FEATURE_LABELS = {
    "sketch": "Sketch", "extrude": "Extrude", "revolve": "Revolve", "fillet": "Fillet",
    "chamfer": "Chamfer", "shell": "Shell", "plane": "Plane", "mirror": "Mirror",
    "linear_pattern": "Linear pattern", "circular_pattern": "Circular pattern",
    "boolean": "Boolean", "import": "Import", "hole": "Hole", "variable": "Variable",
    "variable_studio_ref": "Variable Studio reference",
    "sweep": "Sweep", "loft": "Loft", "draft": "Draft", "split": "Split", "move": "Move", "thread": "Thread",
    "sm_base": "Sheet metal", "sm_flange": "Flange", "delete_face": "Delete face", "move_face": "Move face", "offset_surface": "Offset surface",
}

# ISO metric screw data (coarse threads).  clearance: close / normal / loose (ISO 273)
HOLE_SIZES = ["M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10", "M12"]
CLEARANCE = {"M2": (2.2, 2.4, 2.6), "M2.5": (2.7, 2.9, 3.1), "M3": (3.2, 3.4, 3.6), "M4": (4.3, 4.5, 4.8),
             "M5": (5.3, 5.5, 5.8), "M6": (6.4, 6.6, 7.0), "M8": (8.4, 9.0, 10.0), "M10": (10.5, 11.0, 12.0),
             "M12": (13.0, 13.5, 14.5)}
TAP_DRILL = {"M2": 1.6, "M2.5": 2.05, "M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5,
             "M12": 10.2}
COUNTERBORE = {"M2": (4.4, 2.2), "M2.5": (5.5, 2.7), "M3": (6.5, 3.3), "M4": (8.0, 4.4), "M5": (10.0, 5.4),
               "M6": (11.0, 6.4), "M8": (15.0, 8.6), "M10": (18.0, 10.8), "M12": (20.0, 13.0)}  # socket head
COUNTERSINK = {"M2": 4.4, "M2.5": 5.5, "M3": 6.3, "M4": 9.4, "M5": 10.4, "M6": 12.6, "M8": 17.3, "M10": 20.0,
               "M12": 24.0}  # 90° flat head


def hole_centres(sketch):
    """Hole positions in a sketch: its points and the centres of its circles.
    Returns ([(u, v)], [circle diameter or None])."""
    centres, sizes = [], []
    for eid, e in sketch.entities.items():
        if e["type"] == "point" and not e.get("virtual"):
            centres.append(tuple(sketch.points[e["p"]]))
            sizes.append(None)
        elif e["type"] == "circle":
            centres.append(tuple(sketch.points[e["c"]]))
            sizes.append(None if e.get("construction") else 2 * float(e["r"]))
    return centres, sizes


def hole_dims(p):
    """(drill diameter, counterbore (dia, depth) or None, countersink dia or None, label)."""
    std = p.get("standard", "custom")
    if std == "sketch":
        style = p.get("style", "simple")
        cb = (float(p.get("counterbore_dia", 9)), float(p.get("counterbore_depth", 3))) if style == "counterbore" else None
        cs = float(p.get("countersink_dia", 10)) if style == "countersink" else None
        return 1.0, cb, cs, "Size of the sketch circles"
    size = p.get("size", "M4")
    style = p.get("style", "simple")
    if std == "custom" or size not in TAP_DRILL:
        cb = (float(p.get("counterbore_dia", 9)), float(p.get("counterbore_depth", 3))) if style == "counterbore" else None
        cs = float(p.get("countersink_dia", 10)) if style == "countersink" else None
        return float(p.get("diameter", 5)), cb, cs, f"Ø{float(p.get('diameter', 5)):g}"
    if std == "tapped":
        d = TAP_DRILL[size]
        label = f"{size} tapped"
    else:
        d = CLEARANCE[size][{"close": 0, "normal": 1, "loose": 2}.get(p.get("fit", "normal"), 1)]
        label = f"{size} clearance"
    cb = COUNTERBORE[size] if style == "counterbore" else None
    cs = COUNTERSINK[size] if style == "countersink" else None
    return d, cb, cs, label


@dataclass
class Part:
    id: str
    name: str
    shape: object
    color: tuple = DEFAULT_COLOR
    face_colors: dict = None      # face index -> (r, g, b), e.g. from a coloured STEP file


class PartMap(dict):
    """Part id -> Part, that also accepts a studio's pre-I1.4 <fid>:<counter> ids through `aliases`
    (old id -> new id): a direct lookup by a stale id -- baked into an assembly instance, a drawing source,
    or any other code that looks a part up by id -- still finds it, with no caller needing to know about
    the rename. Iteration (keys/values/items/len) only ever sees the current ids; aliases are consulted
    only when a direct lookup misses."""
    def __init__(self, *args, aliases=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aliases = dict(aliases) if aliases else {}

    def __missing__(self, key):
        new = self.aliases.get(key)
        if new is not None and new != key and dict.__contains__(self, new):
            return dict.__getitem__(self, new)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if dict.__contains__(self, key):
            return True
        new = self.aliases.get(key)
        return new is not None and new != key and dict.__contains__(self, new)


def _surface_key_match(a, b, tol):
    """Do two faces lie on the same underlying surface (same plane, or same cylinder)?"""
    if a["type"] != b["type"]:
        return False
    if "n" in a and "n" in b:
        na, nb = np.array(a["n"]), np.array(b["n"])
        if na @ nb < 1 - 1e-6:
            return False
        return abs(na @ np.array(a["c"]) - nb @ np.array(b["c"])) < tol
    if "r" in a and "r" in b:
        if abs(a["r"] - b["r"]) > tol or abs(abs(np.dot(a["axis"], b["axis"])) - 1) > 1e-6:
            return False
        d = np.array(b["axis_p"]) - np.array(a["axis_p"])
        ax = np.array(a["axis"])
        return np.linalg.norm(d - (d @ ax) * ax) < tol
    return False


def carry_face_colors(old: Part, new_shape):
    """After a feature changes a part, keep the colours of faces that are still there —
    including faces that were trimmed (they stay on the same plane or cylinder)."""
    if not old.face_colors:
        return None
    old_faces = K.faces(old.shape)
    lo, hi = K.bbox(old.shape)
    tol = 1e-6 * (1 + float(np.linalg.norm(hi - lo)))
    sigs = []
    for i, col in old.face_colors.items():
        if i < len(old_faces):
            f = old_faces[i]
            flo, fhi = K.bbox(f)
            sigs.append((K.face_info(f), flo - tol * 10, fhi + tol * 10, col))
    out = {}
    for j, f in enumerate(K.faces(new_shape)):
        inf = K.face_info(f)
        c = np.array(inf["c"])
        # 1) unchanged face: same centre and area
        for oi, flo, fhi, col in sigs:
            if abs(oi["area"] - inf["area"]) <= 1e-6 * max(oi["area"], 1) and np.linalg.norm(np.array(oi["c"]) - c) < tol * 10:
                out[j] = col
                break
        else:
            # 2) trimmed face: same surface, and it sits within the old face's extent
            nlo, nhi = K.bbox(f)
            for oi, flo, fhi, col in sigs:
                if _surface_key_match(oi, inf, tol * 10) and np.all(nlo >= flo) and np.all(nhi <= fhi):
                    out[j] = col
                    break
    return out or None


@dataclass
class SketchResult:
    plane: K.Plane
    sketch: Sketch
    regions: list            # TopoDS_Face list
    region_info: list        # signature per region (see region_signature)
    bb: tuple = None         # (lo, hi) of all regions, for position-relative matching
    # Element names built from the sketch entity ids (naming.name_sketch): `named` names every region
    # face, edge and vertex, `region_names[i]` is the name of regions[i] (None if it has none).
    named: object = None
    region_names: list = field(default_factory=list)

    def region_index(self, face):
        """The position of a region face in `regions`, or None."""
        for i, r in enumerate(self.regions):
            if r.IsSame(face):
                return i
        return None


@dataclass
class ModelState:
    parts: dict = field(default_factory=dict)        # id -> Part (ordered)
    sketches: dict = field(default_factory=dict)     # feature id -> SketchResult
    planes: dict = field(default_factory=dict)       # feature id -> Plane
    part_counter: int = 0
    vars: dict = field(default_factory=dict)        # variable name -> value (mm / degrees / plain)
    sheet: dict = field(default_factory=dict)       # part id -> sheetmetal.SheetModel (sheet metal parts)
    feature_part_counts: dict = field(default_factory=dict)   # feature id -> parts it has created so far
    # new part id (<fid>/<n>) -> the <fid>:<counter> id it would have had before I1.4 (replay-wide counter).
    # Never pruned, so it keeps every id ever assigned across a whole replay -- used to build the alias
    # table a v1 studio is migrated with, and to rekey the part ids baked into tests/golden/.
    old_ids: dict = field(default_factory=dict)
    # ids of the features that found a reference only through the geometric signature matcher, because
    # the reference carries no element name (a legacy reference). Their result is a best guess.
    recovered: set = field(default_factory=set)
    # part id -> naming.NamedShape: the element map of that part's shape (docs/reference/element-naming.md).
    # The one place a part's names live; Part itself carries none, so a Part rebuilt with a new name or
    # colour keeps its map. Read it through `named()`, write it through `set_named()`.
    names: dict = field(default_factory=dict)

    def copy(self):
        return ModelState(dict(self.parts), dict(self.sketches), dict(self.planes), self.part_counter,
                          dict(self.vars), dict(self.sheet), dict(self.feature_part_counts), dict(self.old_ids),
                          set(self.recovered), {k: v for k, v in self.names.items() if k in self.parts})

    def named(self, pid):
        """The element map of part `pid`, or None when it has none or the part's shape has since been
        replaced by something that did not rename it (the map then describes a shape that is gone)."""
        part, named = self.parts.get(pid), self.names.get(pid)
        if part is None or named is None or named.shape is None or not named.shape.IsSame(part.shape):
            return None
        return named

    def set_named(self, pid, named):
        if named is None:
            self.names.pop(pid, None)
        else:
            self.names[pid] = named

    def new_part(self, shape, fid, color=None):
        self.part_counter += 1
        if color is None:
            color = palette_color(self.part_counter)
        n = self.feature_part_counts.get(fid, 0) + 1
        self.feature_part_counts[fid] = n
        pid = f"{fid}/{n}"
        self.old_ids[pid] = f"{fid}:{self.part_counter}"
        p = Part(pid, f"Part {self.part_counter}", shape, color)
        self.parts[pid] = p
        return p


# --------------------------------------------------------------------------- reference resolution

def make_ref(part_id, sub, kind, part_shape=None, named=None):
    """A reference to one face or edge: its signature (flat fields) and, when `named` (the part's
    NamedShape, i.e. `state.named(part_id)`) holds a name for `sub`, that name under `name`.
    A reference without a name is a legacy reference (docs/reference/element-naming.md, section 6)."""
    info = K.face_info(sub) if kind == "face" else K.edge_info(sub)
    ref = {"part": part_id, **info}
    name = named.name_of(sub) if named is not None else None
    if name:
        ref["name"] = name
    if part_shape is not None:
        lo, hi = K.bbox(part_shape)
        ref["bb"] = [lo.tolist(), hi.tolist()]
    return ref


def _norm_pos(c, bb):
    lo, hi = np.array(bb[0]), np.array(bb[1])
    span = np.maximum(hi - lo, 1e-6)
    return (np.array(c) - lo) / span


def _score(info, ref, scale, bb=None):
    if info["type"] != ref["type"]:
        return 1e9
    s = np.linalg.norm(np.array(info["c"]) - np.array(ref["c"])) / scale
    if bb is not None and "bb" in ref:
        # position relative to the part's bounding box survives resizing
        s = min(s, 0.5 * np.linalg.norm(_norm_pos(info["c"], bb) - _norm_pos(ref["c"], ref["bb"])))
    size_key = "area" if ref["kind"] == "face" else "len"
    a, b = info[size_key], ref[size_key]
    s += abs(a - b) / max(a, b, 1e-9)
    for key in ("n", "d", "axis"):
        if key in ref and key in info:
            dot = abs(float(np.dot(info[key], ref[key])))
            s += (1 - dot) * 5
    if "r" in ref and "r" in info:
        s += abs(info["r"] - ref["r"]) / max(ref["r"], 1e-9)
    return s


def _mark_recovered(state, fid=None):
    """Flag the feature being run as having found a reference only by signature. `fid` defaults to
    the feature `prepare` last set up on this state (runners do not pass their id)."""
    fid = fid or getattr(state, "current_fid", None)
    if fid is not None:
        state.recovered.add(fid)


def _named_lookup(state, ref, letter):
    """(part id, sub-shape or vertex) of the element a name refers to, or None when no part's element
    map has that name. A reference's own part is searched first when it still exists, then the other
    pieces a cut severed from the same solid (`set_part_shape` ids `<pid>.<i>`): the element may now
    be on any of them. Every part is searched when the reference's own part is gone."""
    own = ref.get("part")
    if own in state.parts:
        head, slash, tail = own.rpartition("/")
        root = head + slash + tail.split(".")[0]          # "<fid>/<n>.<i>.<j>" -> "<fid>/<n>"
        pids = [own] + [p for p in state.parts if slash and p != own and (p == root or p.startswith(root + "."))]
    else:
        pids = list(state.parts)
    for pid in pids:
        nm = state.named(pid)
        if nm is None:
            continue
        for sub, name in nm.named(letter):
            if name == ref["name"]:
                return pid, sub
    return None


def _lost_element(kind, ref):
    return K.KernelError(f"{kind.capitalize()} {ref['name']} is gone: the model no longer has an element "
                         "of that name")


def resolve(state: ModelState, ref, kind, fid=None):
    """The face/edge a reference points at. Returns (part_id, subshape) or raises KernelError.

    Policy (ADR 0003, docs/reference/element-naming.md, section 6): a reference that carries a name
    resolves to the element of that name and to nothing else; a name missing from the rebuilt maps is
    an error naming the lost element, never a nearest match. Only a legacy reference with no name uses
    the geometric `_score` matcher, and then feature `fid` (default: the feature running) is flagged
    recovered."""
    if ref.get("name"):
        hit = _named_lookup(state, ref, N.FACE if kind == "face" else N.EDGE)
        if hit is None:
            raise _lost_element(kind, ref)
        pid, sub = hit
        return pid, (K._as_face(sub) if kind == "face" else K._as_edge(sub))
    parts = [state.parts[ref["part"]]] if ref.get("part") in state.parts else list(state.parts.values())
    if not parts:
        raise K.KernelError("Missing reference: no parts")
    best, best_s = None, 1e9
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for p in parts:
        a, b = K.bbox(p.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    for p in parts:
        lo_p, hi_p = K.bbox(p.shape)
        bb = [lo_p, hi_p]
        subs = K.faces(p.shape) if kind == "face" else K.edges(p.shape)
        for s in subs:
            info = K.face_info(s) if kind == "face" else K.edge_info(s)
            sc = _score(info, ref, scale, bb)
            if sc < best_s:
                best, best_s = (p.id, s), sc
    if best is None or best_s > 0.6:
        raise K.KernelError(f"Missing {kind} reference")
    _mark_recovered(state, fid)
    return best


def resolve_plane(state: ModelState, ref) -> K.Plane:
    kind = ref.get("kind")
    if kind == "std":
        return K.std_plane(ref["name"])
    if kind == "plane":
        if ref["fid"] not in state.planes:
            raise K.KernelError("Missing plane reference")
        return state.planes[ref["fid"]]
    if kind == "face":
        _, f = resolve(state, ref["face"], "face")
        pl = K.planar_face_plane(f)
        if pl is None:
            raise K.KernelError("Face is not planar")
        # keep the stored sketch origin/x so sketches don't jump when the face changes size
        if "origin" in ref:
            o = np.array(ref["origin"], float)
            o = o - ((o - pl.origin) @ pl.normal) * pl.normal
            x = np.array(ref["xdir"], float)
            x = x - (x @ pl.normal) * pl.normal
            if np.linalg.norm(x) > 1e-6:
                pl = K.Plane(o, x / np.linalg.norm(x), pl.normal)
        return pl
    raise K.KernelError("Unknown plane reference")


def make_vertex_ref(part_id, pos, part_shape=None, named=None):
    """A reference to a vertex at `pos`; carries the vertex's name when `named` has one there."""
    ref = {"kind": "vertex", "part": part_id, "c": [float(x) for x in pos]}
    if named is not None:
        for v, name in named.named(N.VERTEX):
            if np.linalg.norm(_vertex_pos(v) - np.asarray(pos, float)) < 1e-7:
                ref["name"] = name
                break
    if part_shape is not None:
        lo, hi = K.bbox(part_shape)
        ref["bb"] = [lo.tolist(), hi.tolist()]
    return ref


def _vertex_pos(v):
    return K._np(K.BRep_Tool.Pnt_s(K._as_vertex(v)))


def resolve_point(state: ModelState, ref, fid=None) -> np.ndarray:
    """The position of the vertex a stored vertex reference points at. Same policy as `resolve`: a
    name is found or the feature errors; a nameless reference is matched by position and flagged."""
    if ref.get("name"):
        hit = _named_lookup(state, ref, N.VERTEX)
        if hit is None:
            raise _lost_element("vertex", ref)
        return _vertex_pos(hit[1])
    parts = [state.parts[ref["part"]]] if ref.get("part") in state.parts else list(state.parts.values())
    if not parts:
        raise K.KernelError("Missing vertex reference: no parts")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for p in parts:
        a, b = K.bbox(p.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    c0 = np.array(ref["c"], float)
    best, best_s = None, 1e9
    for p in parts:
        a, b = K.bbox(p.shape)
        for v in K.vertex_points(p.shape):
            sc = float(np.linalg.norm(v - c0)) / scale
            if "bb" in ref:
                sc = min(sc, 0.5 * float(np.linalg.norm(_norm_pos(v, [a, b]) - _norm_pos(c0, ref["bb"]))))
            if sc < best_s:
                best, best_s = v, sc
    if best is None or best_s > 0.6:
        raise K.KernelError("Missing vertex reference")
    _mark_recovered(state, fid)
    return np.asarray(best, float)


# --------------------------------------------------------------------------- planes
# A plane feature takes any mix of planes / planar faces, curved faces, edges and vertices; the plane type is
# either chosen by the user or worked out from what was selected ("auto"), as in Onshape.

PLANE_TYPES = [
    ("auto", "Automatic"), ("offset", "Offset"), ("plane_point", "Plane + point"), ("line_angle", "Line + angle"),
    ("point_normal", "Point + edge normal"), ("three_point", "Three points"), ("mid_plane", "Mid plane"),
    ("curve_point", "Curve + point"), ("tangent", "Tangent"),
]
PLANE_TYPE_NAMES = dict(PLANE_TYPES)


def plane_items(p):
    """[(kind, ref)] a plane feature was built from: kind is plane | face | edge | vertex."""
    refs = p.get("refs")
    if refs:
        return [(r["k"], r["ref"]) for r in refs]
    if p.get("ref"):
        return [("plane", p["ref"])]
    return []


def plane_role(kind, ref):
    if kind == "vertex":
        return "point"
    if kind == "plane":
        return "plane"
    if kind == "face":
        return "plane" if ref.get("type") == int(K.GeomAbs_Plane) else "surface"
    if kind == "edge":
        return "line" if ref.get("type") == int(K.GeomAbs_Line) else "curve"
    return "?"


def detect_plane_type(roles):
    """The plane type that fits a selection (a list of roles from plane_role), or None."""
    n = {r: roles.count(r) for r in ("plane", "surface", "line", "curve", "point", "?")}
    total = len(roles)
    if total == 0:
        return "offset"
    if n["?"]:
        return None
    if n["point"] == 3 and total == 3:
        return "three_point"
    if n["point"] == 1 and n["plane"] == 1 and total == 2:
        return "plane_point"
    if n["point"] == 1 and n["line"] == 1 and total == 2:
        return "point_normal"
    if n["point"] == 1 and n["curve"] == 1 and total == 2:
        return "curve_point"
    if n["plane"] == 2 and total == 2:
        return "mid_plane"
    if n["line"] == 1 and total - n["line"] - n["plane"] == 0 and n["plane"] <= 1:
        return "line_angle"
    if n["surface"] == 1 and n["point"] <= 1 and total == 1 + n["point"]:
        return "tangent"
    if n["plane"] == 1 and total == 1:
        return "offset"
    return None


def plane_type(p):
    """The effective type of a plane feature's params (resolves 'auto'); None when the selection fits nothing."""
    t = p.get("type") or "auto"
    if t == "auto":
        return detect_plane_type([plane_role(k, r) for k, r in plane_items(p)])
    return t


def _frame(origin, normal, xhint=None):
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    o = np.asarray(origin, float)
    if xhint is not None:
        x = np.asarray(xhint, float)
        x = x - (x @ n) * n
        if np.linalg.norm(x) > 1e-6:
            return K.Plane(o, x / np.linalg.norm(x), n)
    for c in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        x = c - (c @ n) * n
        if np.linalg.norm(x) > 0.3:
            return K.Plane(o, x / np.linalg.norm(x), n)
    raise K.KernelError("Can't orient that plane")


def build_plane(state, p):
    """The datum plane a plane feature describes."""
    items = plane_items(p)
    typ = plane_type(p)
    if typ is None:
        raise K.KernelError("That selection doesn't fit a plane type — pick a plane or face, an edge, or points")
    flip = bool(p.get("flip"))
    by = {"plane": [], "surface": [], "line": [], "curve": [], "point": []}
    for k, ref in items:
        by.setdefault(plane_role(k, ref), []).append((k, ref))

    def face_plane(k, ref):
        return resolve_plane(state, ref if k == "plane" else {"kind": "face", "face": ref})

    def one(role, what, n=1):
        got = by[role] if isinstance(role, str) else [x for r in role for x in by[r]]
        if len(got) < n:
            raise K.KernelError(f"Select {what}")
        return got[:n]

    def done(pl):
        return K.Plane(pl.origin, pl.xdir, -pl.normal) if flip and typ != "offset" else pl

    if typ == "offset":
        base = face_plane(*by["plane"][0]) if by["plane"] else K.std_plane("Top")
        return base.offset(float(p.get("offset", 10)) * (-1 if flip else 1))
    if typ == "plane_point":
        base = face_plane(*one("plane", "a plane or planar face")[0])
        pt = resolve_point(state, one("point", "a vertex for the plane to pass through")[0][1])
        return done(K.Plane(pt, base.xdir, base.normal))
    if typ in ("point_normal", "curve_point"):
        _, eref = one(("line", "curve"), "an edge")[0]
        pt = resolve_point(state, one("point", "a vertex")[0][1])
        _, e = resolve(state, eref, "edge")
        if typ == "point_normal" and eref.get("type") == int(K.GeomAbs_Line):
            n = np.array(K.edge_info(e)["d"], float)          # perpendicular to a straight edge, through the point
            return done(_frame(pt, n))
        on, n = K.edge_closest(e, pt)                          # perpendicular to the curve where the point lies on it
        return done(_frame(on, n))
    if typ == "three_point":
        pts = [resolve_point(state, r) for _, r in one("point", "three vertices", 3)]
        n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        if np.linalg.norm(n) < 1e-9 * max(1.0, float(np.linalg.norm(pts[1] - pts[0]))):
            raise K.KernelError("The three points are in a straight line")
        x = pts[1] - pts[0]
        return done(_frame(pts[0], n / np.linalg.norm(n), x / np.linalg.norm(x)))
    if typ == "mid_plane":
        a, b = [face_plane(*it) for it in one("plane", "two planes or planar faces", 2)]
        na, nb = a.normal, b.normal
        if abs(float(na @ nb)) > 1 - 1e-9:                    # parallel: halfway between them
            d = float(na @ (b.origin - a.origin)) / 2
            return done(_frame(a.origin + d * na, na, a.xdir))
        nm = na + nb if float(na @ nb) >= 0 else na - nb      # not parallel: the plane bisecting them
        nm = nm / np.linalg.norm(nm)
        M = np.array([na, nb, np.cross(na, nb)])
        o = np.linalg.solve(M, np.array([na @ a.origin, nb @ b.origin, 0.0]))
        return done(_frame(o, nm))
    if typ == "line_angle":
        _, eref = one("line", "a straight edge")[0]
        _, e = resolve(state, eref, "edge")
        info = K.edge_info(e)
        a, d = np.array(info["p0"], float), np.array(info["d"], float)
        d = d / np.linalg.norm(d)
        u0 = None
        if by["plane"]:                                       # zero degrees: parallel to the chosen plane
            u0 = np.cross(face_plane(*by["plane"][0]).normal, d)
        if u0 is None or np.linalg.norm(u0) < 1e-6:           # otherwise: the plane holding the edge and world Z
            for c in (np.array([0, 0, 1.0]), np.array([1.0, 0, 0])):
                u0 = c - (c @ d) * d
                if np.linalg.norm(u0) > 1e-6:
                    break
        u0 = u0 / np.linalg.norm(u0)
        ang = math.radians(float(p.get("angle", 45)))
        u = math.cos(ang) * u0 + math.sin(ang) * np.cross(d, u0)
        return done(K.Plane(a, d, np.cross(d, u)))
    if typ == "tangent":
        faces_ = by["surface"] + [it for it in by["plane"] if it[0] == "face"]
        if not faces_:
            raise K.KernelError("Select a curved face")
        face = resolve(state, faces_[0][1], "face")[1]
        pt = resolve_point(state, by["point"][0][1]) if by["point"] else None
        on, n = K.surface_point_normal(face, pt)
        return done(_frame(on, n))
    raise K.KernelError("Unknown plane type")


def region_signature(face):
    info = K.face_info(face)
    return {"c": info["c"], "area": info["area"], "loops": K.face_loops(face), "edges": len(K.edges(face))}


def _region_score(info, r, bb):
    scale = math.sqrt(max(info["area"], r["area"], 1e-9))
    s = np.linalg.norm(np.array(info["c"]) - np.array(r["c"])) / scale
    if bb is not None and r.get("bb"):
        s = min(s, np.linalg.norm(_norm_pos(info["c"], bb) - _norm_pos(r["c"], r["bb"])))
    s += abs(info["area"] - r["area"]) / max(info["area"], r["area"], 1e-9)
    if "loops" in r:
        s += abs(info["loops"] - r["loops"]) * 1.0
        s += min(abs(info["edges"] - r["edges"]), 4) * 0.15
    return s


def resolve_regions(state: ModelState, refs, fid=None):
    """The region faces a list of region references point at.

    A reference that carries a name resolves to the region of that name and to nothing else: a name
    missing from the rebuilt sketch is an error naming the lost region (ADR 0003,
    docs/reference/element-naming.md § 6), never a nearest match. Only a legacy reference with no name
    falls back to the geometric `_region_score` matcher, and then feature `fid` is flagged recovered."""
    out = []
    for r in refs:
        sk = state.sketches.get(r["sketch"])
        if sk is None:
            raise K.KernelError("Missing sketch")
        name = r.get("name")
        if name:
            if name not in sk.region_names:
                raise K.KernelError(f"Sketch region {name} is gone: the sketch no longer has a region "
                                    "bounded by those curves")
            out.append(sk.regions[sk.region_names.index(name)])
            continue
        if fid is not None:
            state.recovered.add(fid)
        best, best_s = None, 1e9
        for f, info in zip(sk.regions, sk.region_info):
            s = _region_score(info, r, sk.bb)
            if s < best_s:
                best, best_s = f, s
        if best is None or best_s > 1.5:
            raise K.KernelError("Missing sketch region")
        out.append(best)
    return out


def region_ref(state_or_result, sketch_fid, face):
    """Signature of a sketch region. Accepts a ModelState or the SketchResult itself."""
    sr = state_or_result
    if isinstance(sr, ModelState):
        sr = sr.sketches.get(sketch_fid)
    ref = {"sketch": sketch_fid, **region_signature(face)}
    i = sr.region_index(face) if sr is not None else None
    if i is not None and i < len(sr.region_names) and sr.region_names[i]:
        ref["name"] = sr.region_names[i]
    if sr is not None and sr.bb is not None:
        ref["bb"] = [list(map(float, sr.bb[0])), list(map(float, sr.bb[1]))]
    return ref


def resolve_axis(state: ModelState, ref):
    kind = ref.get("kind")
    if kind == "std":
        d = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[ref["name"]]
        return np.zeros(3), np.array(d, float)
    if kind == "sketch_line":
        sk = state.sketches.get(ref["sketch"])
        if sk is None or ref["line"] not in sk.sketch.entities:
            raise K.KernelError("Missing axis")
        e = sk.sketch.entities[ref["line"]]
        a = sk.plane.to_world(*sk.sketch.points[e["p1"]])
        b = sk.plane.to_world(*sk.sketch.points[e["p2"]])
        d = b - a
        if np.linalg.norm(d) < 1e-9:
            raise K.KernelError("Axis has zero length")
        return a, d / np.linalg.norm(d)
    if kind == "edge":
        _, e = resolve(state, ref["edge"], "edge")
        ln = K.edge_line(e)
        if ln is None:
            info = K.edge_info(e)
            if "axis" in info:
                return np.array(info["center"]), np.array(info["axis"])
            raise K.KernelError("Axis edge must be straight or circular")
        return ln
    raise K.KernelError("Select an axis")


# --------------------------------------------------------------------------- applying a body to the part set

def set_part_shape(state: ModelState, part: Part, shape, named=None):
    """Store a new shape for a part; disjoint solids become separate parts (like Onshape).

    `named` is the NamedShape of `shape` (from `name_feature`): each piece keeps the names of its own
    elements. Without it the part has no names until a feature names it."""
    pieces = K.solids(shape)
    if len(pieces) <= 1:
        one = pieces[0] if pieces else shape
        state.parts[part.id] = Part(part.id, part.name, one, part.color)
        state.set_named(part.id, named.restrict(one) if named is not None and named.shape is not None else None)
        return
    state.parts[part.id] = Part(part.id, part.name, pieces[0], part.color)
    state.set_named(part.id, named.restrict(pieces[0]) if named is not None else None)
    old_base = state.old_ids.get(part.id)
    for i, extra in enumerate(pieces[1:], 1):
        pid = f"{part.id}.{i}"
        if old_base is not None:
            state.old_ids[pid] = f"{old_base}.{i}"
        state.parts[pid] = Part(pid, f"{part.name} ({i + 1})", extra, part.color)
        state.set_named(pid, named.restrict(extra) if named is not None else None)


def _stamp_severed_siblings(state: ModelState, inputs, fid, op):
    """ADR 0008: the other pieces of a severed solid that a stamping feature ran on one piece of get the same
    pass-through stamp (the id without its `.<i>` suffixes and every id that extends it), so the pieces name
    as the one shape they are in FreeCAD. A piece that is itself an input is named by the operation."""
    given = {i for i in inputs or () if isinstance(i, str)}
    for pid in list(given):
        head, slash, tail = pid.rpartition("/")
        if not slash:
            continue
        root = head + slash + tail.split(".")[0]
        for sib in list(state.parts):
            if sib in given or not (sib == root or sib.startswith(root + ".")):
                continue
            nm = state.named(sib)
            if nm is not None and not (nm.stamped_from is not None and nm.stamped_from[0] == fid):
                given.add(sib)      # a family is stamped once, whichever of its pieces were the inputs
                state.set_named(sib, N.stamp_all(nm, fid, op))


def name_feature(state: ModelState, fid, inputs, shape, history, op, part=None, stamp=True):
    """The naming step of a feature runner: the NamedShape of `shape`, a kernel helper's result.

    How a feature runner uses naming (docs/reference/element-naming.md, "How a feature runner uses
    naming"): call the kernel helper with `with_history=True`, which returns `(shape, history)`,
    then call this once per helper call with

      state    the ModelState being built;
      fid      the feature's id, which becomes the tag of the fragment it adds to every name;
      inputs   what the helper was given, in its order: part ids (resolved to that part's map, so
               call this BEFORE popping or replacing the part), a sketch's `SketchResult.named`, or
               any NamedShape. None and parts without a map are skipped;
      shape, history   the helper's result and history;
      op       the helper's name (`"extrude"`, `"revolve"`, `"fuse"`, `"cut"`, `"common"`, ...: a key
               of `naming.OP_FOR`) or an op code;
      part     optional part id: the result is also stored as that part's map (`state.names`);
      stamp    False keeps an untouched element's name as it is (what `apply_body` does).

    The map is returned either way. A result that cannot be named (a feature id that cannot go in a
    name) comes back with no names rather than failing the feature; its references then fail loudly
    on the next rebuild, as ADR 0003 asks. To make the result a part, pass the map on:
    `apply_body(..., named=...)`, `set_part_shape(..., named=...)` or `state.set_named(pid, named)`.
    """
    sources = []
    for i in inputs or ():
        n = state.named(i) if isinstance(i, str) else i
        if n is not None and n.stamped_from is not None and n.stamped_from[0] == fid \
                and n.stamped_from[1] is not None:
            n = n.stamped_from[1]        # stamped as a sibling by this feature; it runs on this piece too
        if n is not None:
            sources.append(n)
    try:
        named = N.name_result(shape, history, sources, fid, N.OP_FOR.get(op, op), stamp)
    except N.NamingError:
        named = N.NamedShape(shape, {})
    if stamp:
        named.stamped_from = (fid, None)        # this map carries the feature's stamp; its siblings are stamped too
        _stamp_severed_siblings(state, inputs, fid, op)
    if part is not None:
        state.set_named(part, named)
    return named


def apply_body(state: ModelState, body, op: str, fid: str, scope=None, named=None):
    """Merge a tool body into the model according to op: new / add / remove / intersect.

    `named` is the body's NamedShape, or a list of them when the body is a compound of named parts
    (a boolean's tools). Every part the body changes gets its element map from the boolean's history;
    an element the body leaves alone keeps its name. With no `named` the body's own elements have no
    names (a feature not yet routed through `name_feature`) but the part's other faces keep theirs."""
    if body is None:
        raise K.KernelError("Feature produced no geometry")
    sols = K.solids(body)
    if not sols:
        raise K.KernelError("Feature produced no solid")
    tool = [] if named is None else list(named) if isinstance(named, (list, tuple)) else [named]
    targets = [state.parts[p] for p in (scope or state.parts) if p in state.parts]
    if op == "new" or (op == "add" and not targets):
        for s in sols:
            state.set_named(state.new_part(s, fid).id, _piece(tool, s))
        return
    if op == "add":
        remaining = []
        for s in sols:
            hit = [p for p in targets if K.touches_or_intersects(p.shape, s)]
            if not hit:
                remaining.append(s)
                continue
            keep = hit[0]
            shape, maps = keep.shape, [state.named(keep.id)]
            for p in hit[1:]:
                shape, h = K.fuse(shape, p.shape, with_history=True)
                maps = [name_feature(state, fid, maps + [state.named(p.id)], shape, h, "fuse", stamp=False)]
                state.parts.pop(p.id, None)
                targets.remove(p)
            shape, h = K.fuse(shape, s, with_history=True)
            result = name_feature(state, fid, maps + tool, shape, h, "fuse", stamp=False)
            state.parts[keep.id] = Part(keep.id, keep.name, shape, keep.color)
            state.set_named(keep.id, result)
            targets[targets.index(keep)] = state.parts[keep.id]
        for s in remaining:
            state.set_named(state.new_part(s, fid).id, _piece(tool, s))
        return
    hit_any = False
    for p in list(targets):
        if not K.intersects(p.shape, body):
            continue
        hit_any = True
        make, code = (K.cut, "cut") if op == "remove" else (K.common, "common")
        res, h = make(p.shape, body, with_history=True)
        result = name_feature(state, fid, [state.named(p.id)] + tool, res, h, code, stamp=False)
        if not K.solids(res):
            state.parts.pop(p.id)
            continue
        set_part_shape(state, p, res, result)
    if not hit_any:
        raise K.KernelError("Feature does not intersect any part")


def _piece(tool, solid):
    """The element map of one solid of a tool body: the names its sub-shapes have in the tool maps."""
    out = N.NamedShape(solid, {})
    for t in tool:
        out.names.update(t.restrict(solid).names)
    return out


# --------------------------------------------------------------------------- feature implementations

# --------------------------------------------------------------------------- variables & expressions

# params that hold numbers — these may also be expressions like "#wall * 2"
NUMERIC_KEYS = {"depth", "depth2", "angle", "radius", "distance", "thickness", "offset", "count", "spacing",
                "diameter", "counterbore_dia", "counterbore_depth", "dx", "dy", "dz", "rotate",
                "draft", "pitch", "twist", "scale_end", "major", "length", "clearance", "thread_clearance",
                "countersink_dia", "k", "relief_scale"}


def prepare(f, state):
    """A copy of feature f with every expression evaluated against the variables defined so far.
    Also records which feature is about to run, so `resolve` can flag it recovered."""
    state.current_fid = f.get("id")
    p = f.get("params", {})
    if not any(isinstance(p.get(k), str) for k in NUMERIC_KEYS):
        return f
    p2 = dict(p)
    for k in NUMERIC_KEYS:
        v = p2.get(k)
        if isinstance(v, str):
            val = parse_number(v, angle=(k in ("angle", "rotate", "draft", "twist")), variables=state.vars)
            if val is None:
                bad = [n for n in var_names(v) if n not in state.vars]
                raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else f"Can't read '{v}'")
            p2[k] = int(round(val)) if k == "count" else val
    return dict(f, params=p2)


def run_variable(state, f):
    p = f["params"]
    name = str(p.get("name", "")).strip().lstrip("#")
    if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
        raise K.KernelError("Variable names use letters, digits and _ (not starting with a digit)")
    val = parse_number(p.get("expr", ""), variables=state.vars)
    if val is None:
        bad = [n for n in var_names(p.get("expr", "")) if n not in state.vars]
        raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else "Enter a value or expression")
    state.vars[name] = val


def run_variable_studio_ref(state, f):
    """Put a Variable Studio's evaluated variables into state.vars at this point in the history. A later
    "variable" feature of the same name overrides it. Document.compute hands the Studio's values in as
    params["resolved"] (it also hashes them into the rebuild cache key); None means the link found nothing."""
    values = f["params"].get("resolved")
    if values is None:
        src = f["params"].get("source") or {}
        raise K.KernelError(f"Variable Studio not found: {src.get('name') or src.get('path') or src.get('tab') or '(none linked)'}")
    state.vars.update(values)


def run_sketch(state, f):
    p = f["params"]
    plane = resolve_plane(state, p["plane"])
    sk = Sketch.from_dict(p["sketch"])
    # dimensions driven by variables are re-evaluated and the sketch re-solved
    driven = [c for c in sk.constraints if isinstance(c.get("expr"), str)]
    for c in driven:
        val = parse_number(c["expr"], angle=(c["type"] == "angle"), variables=state.vars)
        if val is None:
            bad = [n for n in var_names(c["expr"]) if n not in state.vars]
            raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else f"Can't read '{c['expr']}'")
        c["value"] = val
    if driven:
        sk.solve()
    regions, history, edges = K.sketch_regions(plane, sk.curves(), with_history=True)
    bb = K.bbox(K.compound(regions)) if regions else None
    try:
        named = N.name_sketch(regions, history, edges, f["id"])
        names = [named.name_of(r) for r in regions]
    except N.NamingError:
        named, names = None, [None] * len(regions)     # unnameable ids: references fall back to signatures
    state.sketches[f["id"]] = SketchResult(plane, sk, regions, [region_signature(r) for r in regions], bb,
                                           named, names)


def _profiles(state, p, fid=None):
    profs = resolve_regions(state, p.get("regions", []), fid)
    for fr in p.get("faces", []):
        _, face = resolve(state, fr, "face")
        profs.append(face)
    return profs


def _profile_names(state, p):
    """The NamedShapes the profile faces of `_profiles` are named in: each sketch whose regions are
    used (its edges name the side faces, its region names the end faces, § 4) and each part a profile
    face was picked from. Part ids are passed as such so the map is read when the step runs."""
    out = []
    for r in p.get("regions", []):
        sk = state.sketches.get(r["sketch"])
        if sk is not None and sk.named is not None and all(o is not sk.named for o in out):
            out.append(sk.named)
    out.extend(dict.fromkeys(fr["part"] for fr in p.get("faces", []) if fr.get("part") in state.parts))
    return out


def _profile_normal(state, p, profs):
    if p.get("regions"):
        return state.sketches[p["regions"][0]["sketch"]].plane.normal
    pl = K.planar_face_plane(profs[0])
    if pl is None:
        raise K.KernelError("Profile face must be planar")
    return pl.normal


def signed_extent(value, flip=False):
    """Onshape-style signed distance: a negative value means the other way. Returns (abs value, flip)."""
    v = float(value)
    if v < 0:
        return -v, not flip
    return v, flip


def run_extrude(state, f):
    p = f["params"]
    profs = _profiles(state, p, f["id"])
    if not profs:
        raise K.KernelError("Select sketch regions or a planar face")
    n = _profile_normal(state, p, profs)
    op = p.get("op", "add")
    end = p.get("end", "blind")
    depth, flip = signed_extent(p.get("depth", 10), p.get("flip", False))
    symmetric = end == "symmetric"
    cutting = op in ("remove", "intersect")
    if cutting and not p.get("regions"):
        # cutting from a face goes into the part by default
        flip = not flip

    def make(direction):
        if end == "through":
            lo, hi = np.full(3, 1e18), np.full(3, -1e18)
            for part in state.parts.values():
                a, b = K.bbox(part.shape)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
            dd = float(np.linalg.norm(hi - lo)) * 2 + 10 if state.parts else depth
            both = p.get("both", False) or (cutting and p.get("regions"))
            return K.extrude(profs, direction, dd, depth2=dd if both else 0.0, with_history=True)
        depth2 = float(p.get("depth2", 0)) if p.get("second", False) and not symmetric else 0.0
        if depth + depth2 <= 0 and depth2:
            raise K.KernelError("The second depth cancels the first")
        return K.extrude(profs, direction, depth, symmetric=symmetric, depth2=depth2, with_history=True)

    d = -n if flip else n
    body, hist = make(d)
    if cutting and end == "blind" and not p.get("second") and state.parts:
        # like Onshape, a cut that misses everything is tried in the other direction
        targets = [state.parts[i] for i in (p.get("scope") or state.parts) if i in state.parts]
        if not any(K.intersects(t.shape, body) for t in targets):
            alt, alt_hist = make(-d)
            if any(K.intersects(t.shape, alt) for t in targets):
                body, hist = alt, alt_hist
    named = name_feature(state, f["id"], _profile_names(state, p), body, hist, "extrude")
    apply_body(state, body, op, f["id"], p.get("scope"), named)


def run_revolve(state, f):
    p = f["params"]
    profs = _profiles(state, p, f["id"])
    a, d = resolve_axis(state, p.get("axis", {}))
    end = p.get("end", "full")
    angle, flip = signed_extent(360.0 if end == "full" else p.get("angle", 180), p.get("flip", False))
    if flip:
        d = -d
    body, hist = K.revolve(profs, a, d, angle, symmetric=(end == "symmetric"), with_history=True)
    named = name_feature(state, f["id"], _profile_names(state, p), body, hist, "revolve")
    apply_body(state, body, p.get("op", "add"), f["id"], p.get("scope"), named)


def _group_by_part(state, refs, kind):
    groups = {}
    for r in refs:
        pid, sub = resolve(state, r, kind)
        groups.setdefault(pid, []).append(sub)
    return groups


def run_fillet(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("edges", []), "edge")
    if not groups:
        raise K.KernelError("Select edges to fillet")
    for pid, es in groups.items():
        part = state.parts[pid]
        res, hist = K.fillet(part.shape, es, float(p.get("radius", 1)), with_history=True)
        named = name_feature(state, f["id"], [pid], res, hist, "fillet")
        state.parts[pid] = Part(pid, part.name, res, part.color)
        state.set_named(pid, named)


def run_chamfer(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("edges", []), "edge")
    if not groups:
        raise K.KernelError("Select edges to chamfer")
    for pid, es in groups.items():
        part = state.parts[pid]
        res, hist = K.chamfer(part.shape, es, float(p.get("distance", 1)), with_history=True)
        named = name_feature(state, f["id"], [pid], res, hist, "chamfer")
        state.parts[pid] = Part(pid, part.name, res, part.color)
        state.set_named(pid, named)


def run_shell(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select faces to remove")
    for pid, fs in groups.items():
        part = state.parts[pid]
        res, hist = K.shell(part.shape, fs, float(p.get("thickness", 1)), p.get("outward", False),
                            with_history=True)
        named = name_feature(state, f["id"], [pid], res, hist, "shell")
        state.parts[pid] = Part(pid, part.name, res, part.color)
        state.set_named(pid, named)


def run_delete_face(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select faces to delete")
    for pid, fs in groups.items():
        part = state.parts[pid]
        res, hist = K.delete_faces(part.shape, fs, p.get("mode", "heal"), with_history=True)
        named = name_feature(state, f["id"], [pid], res, hist, "delete_face", stamp=False)
        state.parts[pid] = Part(pid, part.name, res, part.color)
        state.set_named(pid, named)


def run_move_face(state, f):
    p = f["params"]
    refs = p.get("faces", [])
    if not refs:
        raise K.KernelError("Select flat faces to move")
    dist = float(p.get("distance", 1)) * (-1 if p.get("flip") else 1)
    for r in refs:                     # one at a time: each move changes the part the next face is found in
        pid, face = resolve(state, r, "face")
        part = state.parts[pid]
        res, hist, tool, tool_hist = K.move_face(part.shape, face, dist, with_history=True)
        named = None
        if tool is not None:     # the extruded face names the tool, the tool and the part name the result
            tool_named = name_feature(state, f["id"], [pid], tool, tool_hist, "extrude")
            named = name_feature(state, f["id"], [pid, tool_named], res, hist, "fuse" if dist > 0 else "cut",
                                 stamp=False)
        elif state.named(pid) is not None:
            named = state.named(pid)
        state.parts[pid] = Part(pid, part.name, res, part.color)
        state.set_named(pid, named)


def run_offset_surface(state, f):
    p = f["params"]
    refs = p.get("faces", [])
    if not refs:
        raise K.KernelError("Select faces to offset")
    dist = float(p.get("distance", 1)) * (-1 if p.get("flip") else 1)
    for r in refs:                     # each face becomes its own new surface body
        pid, face = resolve(state, r, "face")
        res, hist = K.offset_surface(face, dist, with_history=True)
        named = name_feature(state, f["id"], [pid], res, hist, "offset_surface")
        state.set_named(state.new_part(res, f["id"]).id, named)


def run_plane(state, f):
    state.planes[f["id"]] = build_plane(state, f["params"])


def _selected_parts(state, p):
    ids = [i for i in p.get("parts", []) if i in state.parts]
    if not ids:
        raise K.KernelError("Select parts")
    return ids


def run_mirror(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    plane = resolve_plane(state, p["plane"])
    bodies, named = [], []
    for i in ids:
        body, h = K.mirror(state.parts[i].shape, plane, with_history=True)
        named.append(name_feature(state, f["id"], [i], body, h, "mirror"))
        bodies.append(body)
    body = K.compound(bodies)
    apply_body(state, body, p.get("op", "add"), f["id"], ids if p.get("op", "add") == "add" else None, named)


def _pattern_instance_name(state, fid, pid, copy, history, op, k):
    """The names of instance k (1-based; 0 is the source) of a pattern. The instance index goes in
    the tag ("F5_2"), so it is part of every name of that copy and a change of count leaves the
    other instances' names alone. FreeCAD's `;:C<n>` disambiguator (spec section 7) is not an index
    to read back; this keeps the same one-name-per-instance effect with an index that is stable."""
    return name_feature(state, f"{fid}_{k}", [pid], copy, history, op)


def run_linear_pattern(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    dref = p.get("direction", {"kind": "std", "name": "X"})
    _, d = resolve_axis(state, dref)
    if p.get("flip"):
        d = -d
    n = int(p.get("count", 3))
    sp = float(p.get("spacing", 20))
    copies, named = [], []
    for i in ids:
        for k in range(1, n):
            c, h = K.pattern_translate(state.parts[i].shape, d * sp * k, with_history=True)
            named.append(_pattern_instance_name(state, f["id"], i, c, h, "linear_pattern", k))
            copies.append(c)
    if not copies:
        raise K.KernelError("Count must be at least 2")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"], None, named)


def run_circular_pattern(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    a, d = resolve_axis(state, p.get("axis", {"kind": "std", "name": "Z"}))
    n = int(p.get("count", 4))
    total = float(p.get("angle", 360))
    step = total / n if abs(total - 360) < 1e-9 else total / max(n - 1, 1)
    copies, named = [], []
    for i in ids:
        for k in range(1, n):
            c, h = K.pattern_rotate(state.parts[i].shape, a, d, step * k, with_history=True)
            named.append(_pattern_instance_name(state, f["id"], i, c, h, "circular_pattern", k))
            copies.append(c)
    if not copies:
        raise K.KernelError("Count must be at least 2")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"], None, named)


def run_boolean(state, f):
    p = f["params"]
    op = p.get("op", "union")
    tools = [i for i in p.get("tools", []) if i in state.parts]
    targets = [i for i in p.get("targets", []) if i in state.parts]
    if op == "union":
        allp = tools + targets
        if len(allp) < 2:
            raise K.KernelError("Select at least two parts")
        keep = state.parts[allp[0]]
        shape, maps = keep.shape, [state.named(keep.id)]
        for i in allp[1:]:
            other = state.named(i)
            shape, h = K.fuse(shape, state.parts.pop(i).shape, with_history=True)
            maps = [name_feature(state, f["id"], maps + [other], shape, h, "fuse", stamp=False)]
        set_part_shape(state, keep, shape, maps[0])
        return
    if not tools or not targets:
        raise K.KernelError("Select tool and target parts")
    body = K.compound([state.parts[i].shape for i in tools])
    tool_names = [state.named(i) for i in tools]
    if not p.get("keep_tools"):
        for i in tools:
            state.parts.pop(i)
    apply_body(state, body, "remove" if op == "subtract" else "intersect", f["id"], targets,
               [n for n in tool_names if n is not None])


def run_import(state, f):
    p = f["params"]
    data = base64.b64decode(p["data"])
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        try:
            items = K.import_step_with_colors(path)
        except Exception:
            items = [(s, None, None, {}) for s in K.import_step(path)]   # plain geometry as a fallback
        for shape, name, color, fcols in items:
            part = state.new_part(shape, f["id"], tuple(color) if color else None)
            state.parts[part.id] = Part(part.id, name or part.name, part.shape, part.color, fcols or None)
            state.set_named(part.id, _import_names(part.shape, f["id"]))
    finally:
        os.unlink(path)


def _import_names(shape, fid):
    """The element map of an imported solid. A STEP file carries no history, and FreeCAD's reader
    attaches no element map either (spec I1.7), so every element is seeded from the feature id and
    its position in the shape's own sub-shape order, which the same bytes always reproduce."""
    try:
        return N.seed(shape, fid, N.OP_FOR["import"])
    except N.NamingError:
        return None


def _tool_names(state, fid, shape, label):
    """The NamedShape of a tool body a feature builds from nothing (a hole's cylinder, a thread's rod, the
    plane face a split cuts with): every element is seeded `<label><kind><position>;MAK;:H<fid>`, so its
    faces carry the feature id (docs/reference/element-naming.md, I1.9). `label` tells the feature's tools
    apart (the hole's third counterbore, the first thread rod) and is the same on every rebuild."""
    keys = N.seed(shape, fid).names
    labels = {k: f"{label}{k[0].lower()}{k[1]}" for k in keys}
    try:
        return N.seed(shape, fid, N.OP_MAKER, labels)
    except N.NamingError:
        return N.NamedShape(shape, {})


def run_hole(state, f):
    """Holes at sketch points: simple, counterbored or countersunk; custom size or ISO metric."""
    p = f["params"]
    sk = state.sketches.get(p.get("sketch"))
    if sk is None:
        raise K.KernelError("Select a sketch with points or circles for the hole centres (click it, or pick it above)")
    centres, sizes = hole_centres(sk.sketch)
    if not centres:
        raise K.KernelError("Add sketch points or circles to place holes")
    if not state.parts:
        raise K.KernelError("There's no part to put holes in")
    dia, cb, cs, _label = hole_dims(p)
    if dia <= 0:
        raise K.KernelError("Hole diameter must be positive")
    through = p.get("end", "through") == "through"
    depth = float(p.get("depth", 10))
    n = np.asarray(sk.plane.normal, float)
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for part in state.parts.values():
        a_, b_ = K.bbox(part.shape)
        lo, hi = np.minimum(lo, a_), np.maximum(hi, b_)
    big = float(np.linalg.norm(hi - lo)) * 2 + 10
    shapes = [pt.shape for pt in state.parts.values()]
    back = max(dia * 0.02, 1e-3)      # start a hair above the surface for clean cuts

    def material_on(direction):
        probe = K.cylinder(sk.plane.to_world(*centres[0]), direction, dia / 2, min(big, max(depth, 1.0)))
        return sum(K.volume(K.common(sh, probe)) for sh in shapes if K.intersects(sh, probe))

    d = -n if material_on(-n) >= material_on(n) else n
    modelled = p.get("standard") == "tapped" and p.get("model_thread") and p.get("size") in K.ISO_PITCH
    if modelled:
        _model_hole_threads(state, f, p, sk, centres, d, through, depth, back, big)
    tools = []
    seeds = []       # one NamedShape per tool; `tool()` records it, so tool k has the same label every rebuild

    def tool(shape, role):
        tools.append(shape)
        seeds.append(_tool_names(state, f["id"], shape, f"h{len(tools)}{role}"))
    from_circles = p.get("standard") == "sketch"
    if from_circles and not any(sizes):
        raise K.KernelError("“Size of the sketch circles” needs circles in the sketch")
    for (u, v), circle_d in zip(centres, sizes):
        if from_circles:
            if not circle_d:
                continue                              # a plain point: no size to take
            dia = circle_d
        c = np.asarray(sk.plane.to_world(u, v), float)
        start = c - d * back
        if modelled:
            pass                                  # the threaded bore itself was cut above
        elif through:
            tool(K.cylinder(c - d * big, d, dia / 2, big * 2), "bore")
        else:
            tool(K.cylinder(start, d, dia / 2, depth + back), "bore")
        if not through and p.get("tip", True):
            # 118° drill point at the bottom of a blind hole
            tip_h = (dia / 2) / math.tan(math.radians(59))
            tool(K.cone(c + d * depth, d, dia / 2, 0.0, tip_h), "tip")
        if cb:
            cdia, cdep = cb
            if cdia <= dia:
                raise K.KernelError("Counterbore must be wider than the hole")
            tool(K.cylinder(start, d, cdia / 2, cdep + back), "bore2")
        if cs:
            if cs <= dia:
                raise K.KernelError("Countersink must be wider than the hole")
            h = (cs - dia) / 2                       # 90° countersink
            tool(K.cone(c - d * back, d, cs / 2 + back, dia / 2, h + back), "sink")
    if tools:
        body, hist = K.fuse_all(tools, with_history=True)
        named = name_feature(state, f["id"], seeds, body, hist, "fuse_all", stamp=False)
        apply_body(state, body, "remove", f["id"], None, named)


def _material_range(shapes, origin, axis, radius, big):
    """Where along `axis` (distances from `origin`) the parts have material within `radius`."""
    probe = K.cylinder(np.asarray(origin) - np.asarray(axis) * big, axis, radius, big * 2)
    zs = []
    for sh in shapes:
        if not K.intersects(sh, probe):
            continue
        com = K.common(sh, probe)
        pts = [np.asarray(q, float) for e in K.edges(com) for q in K.discretize_edge(e, radius * 0.05)]
        zs += [float((q - origin) @ axis) for q in pts]
    return (min(zs), max(zs)) if zs else None


def _model_hole_threads(state, f, p, sk, centres, d, through, depth, back, big):
    """Tapped holes with real ISO thread geometry, cut straight into the parts."""
    size = p.get("size")
    major, pitch = float(size[1:]), K.ISO_PITCH[size]
    clearance = float(p.get("thread_clearance", 0) or 0)
    for ci, (u, v) in enumerate(centres, 1):
        c = np.asarray(sk.plane.to_world(u, v), float)
        rng = _material_range([pt.shape for pt in state.parts.values()], c, d, major / 2 + pitch, big)
        if rng is None:
            continue
        z0, z1 = (rng[0], rng[1]) if through else (max(rng[0], -back), min(rng[1], depth))
        if z1 - z0 < 1e-6:
            continue
        for part in list(state.parts.values()):
            probe = K.cylinder(c + d * z0, d, major / 2 + pitch, z1 - z0)
            if not K.intersects(part.shape, probe):
                continue
            new, hist, plugs = K.cut_internal_thread(part.shape, c, d, major, pitch, z0, z1, with_history=True,
                                                     extend=(True, through), clearance=clearance,
                                                     left=bool(p.get("left_hand")))
            seeds = [_tool_names(state, f["id"], g, f"c{ci}t{n}") for n, g in enumerate(plugs, 1)]
            set_part_shape(state, part, new, name_feature(state, f["id"], [part.id] + seeds, new, hist, "cut",
                                                          stamp=False))


def _nearest_size(diameter, internal):
    """ISO size for a round face: holes match the tap drill, shafts match the major diameter."""
    if internal:
        return min(TAP_DRILL, key=lambda k: abs(TAP_DRILL[k] - diameter))
    return min((k for k in K.ISO_PITCH if k in TAP_DRILL or k in ("M14", "M16", "M20", "M24")),
               key=lambda k: abs(float(k[1:]) - diameter))


def thread_spec(state, p, face):
    """What a Thread feature will do to one round face: dict with kind, size, major, pitch, z0, z1 …"""
    p0, ax, r, zmin, zmax, is_hole = K.cylinder_face_frame(face)
    size = p.get("size", "auto")
    if size == "custom":
        major, pitch, label = float(p.get("major", 2 * r)), float(p.get("pitch", 1.0)), "custom"
    else:
        if size == "auto" or size not in K.ISO_PITCH:
            size = _nearest_size(2 * r, is_hole)
        major, pitch, label = float(size[1:]), K.ISO_PITCH[size], size
    if pitch <= 0:
        raise K.KernelError("Pitch must be positive")
    return {"p0": p0, "axis": ax, "r": r, "zmin": zmin, "zmax": zmax, "internal": is_hole,
            "major": major, "pitch": pitch, "size": label}


def run_thread(state, f):
    """Real (modelled) ISO threads on round faces: tap a hole, or thread a shaft."""
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select round faces (holes or shafts) to thread")
    left = bool(p.get("left_hand"))
    clearance = float(p.get("clearance", 0) or 0)
    for pid, fs in groups.items():
        specs = [thread_spec(state, p, face) for face in fs]
        part = state.parts[pid]
        shape = part.shape
        named = state.named(pid)       # follows `shape` through the threads, one map per thread
        for si, sp in enumerate(specs, 1):
            P, ax, p0 = sp["pitch"], sp["axis"], sp["p0"]
            z0, z1 = sp["zmin"], sp["zmax"]
            # which ends open into air? (probe just past each end, inside the thread's depth)
            radial = np.cross(ax, [1.0, 0, 0] if abs(ax[0]) < 0.9 else [0, 1.0, 0])
            radial /= np.linalg.norm(radial)
            rp = sp["r"] + (0.3 * P if sp["internal"] else -0.3 * P)
            open_lo = not K.material_at(shape, p0 + ax * (z0 - 0.3 * P) + radial * rp)
            open_hi = not K.material_at(shape, p0 + ax * (z1 + 0.3 * P) + radial * rp)
            if p.get("length_mode") == "length":
                L = float(p.get("length", z1 - z0))
                if L <= 0:
                    raise K.KernelError("Thread length must be positive")
                if open_lo or not open_hi:
                    z1 = min(z1, z0 + L)          # measured from the open (lower) end
                    open_hi = False
                else:
                    z0 = max(z0, z1 - L)
                    open_lo = False
            if sp["internal"]:
                core = sp["r"] + 0.002 if abs(sp["r"] - (sp["major"] / 2 - K.THREAD_H * P)) < 0.01 * P else sp["r"]
                shape, hist, tools = K.cut_internal_thread(shape, p0, ax, sp["major"], P, z0, z1, core_r=core,
                                                           extend=(open_lo, open_hi), left=left,
                                                           clearance=clearance, with_history=True)
                op = "cut"
            else:
                if abs(sp["major"] / 2 - sp["r"]) > 0.25 * P:
                    raise K.KernelError(f"Shaft Ø{2 * sp['r']:.4g} doesn't suit {sp['size']} — pick the size or use Custom")
                shape, hist, tools = K.external_thread(shape, p0, ax, sp["r"], P, z0, z1, left=left,
                                                       clearance=clearance, with_history=True)
                op = "fuse"
            seeds = [_tool_names(state, f["id"], g, f"s{si}t{n}") for n, g in enumerate(tools, 1)]
            named = name_feature(state, f["id"], [named] + seeds, shape, hist, op, stamp=False)
        set_part_shape(state, part, shape, named)


# --------------------------------------------------------------------------- sweep / loft / draft / split / move

def _path_wire(state, refs):
    from . import sketch_ops
    edges = []
    for r in refs:
        if r.get("kind") == "sketch_chain":
            sr = state.sketches.get(r["sketch"])
            if sr is None or r["entity"] not in sr.sketch.entities:
                raise K.KernelError("Missing sweep path")
            sk = sr.sketch
            e0 = sk.entities[r["entity"]]
            if e0["type"] == "circle":
                curves = [{"type": "circle", "c": sk.points[e0["c"]], "r": e0["r"]}]
            else:
                chain, _closed = sketch_ops._chain(sk, r["entity"])
                ids = {k for k, _ in chain}
                tmp = Sketch.from_dict(sk.to_dict())
                tmp.entities = {k: dict(v, construction=False) for k, v in tmp.entities.items() if k in ids}
                curves = tmp.curves()
            edges += K.make_sketch_edges(sr.plane, curves)
        elif r.get("kind") == "edge":
            _, e = resolve(state, r["edge"], "edge")
            edges.append(e)
    return K.make_wire(edges)


def run_sweep(state, f):
    p = f["params"]
    profs = _profiles(state, p, f["id"])
    if not p.get("path"):
        raise K.KernelError("Select the path to sweep along")
    body, hist = K.sweep(profs, _path_wire(state, p["path"]), with_history=True)
    named = name_feature(state, f["id"], _profile_names(state, p), body, hist, "sweep")
    apply_body(state, body, p.get("op", "add"), f["id"], p.get("scope"), named)


def run_loft(state, f):
    p = f["params"]
    profs = []
    for item in p.get("profiles", []):
        if item["kind"] == "region":
            profs += resolve_regions(state, [item["ref"]], f["id"])
        else:
            profs.append(resolve(state, item["ref"], "face")[1])
    body, hist = K.loft(profs, p.get("ruled", False), with_history=True)
    named = name_feature(state, f["id"], _loft_names(state, p), body, hist, "loft")
    apply_body(state, body, p.get("op", "add"), f["id"], p.get("scope"), named)


def _loft_names(state, p):
    """What `_profile_names` gives a sweep, for a loft's profile list: the sketch of each region
    profile and the part each face profile was picked from."""
    regions, faces = [], []
    for item in p.get("profiles", []):
        if item["kind"] == "region":
            regions.append(item["ref"])
        else:
            faces.append({"part": item["ref"].get("part")})
    return _profile_names(state, {"regions": regions, "faces": faces})


def run_draft(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select faces to draft")
    neutral = resolve_plane(state, p.get("neutral") or {"kind": "std", "name": "Top"})
    pull = -neutral.normal if p.get("flip") else neutral.normal
    for pid, fs in groups.items():
        part = state.parts[pid]
        res, hist = K.draft(part.shape, fs, pull, float(p.get("angle", 3)), neutral, with_history=True)
        named = name_feature(state, f["id"], [pid], res, hist, "draft")
        state.parts[pid] = Part(pid, part.name, res, part.color)
        state.set_named(pid, named)


def run_split(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    plane = resolve_plane(state, p.get("plane") or {"kind": "std", "name": "Right"})
    for pid in ids:
        part = state.parts[pid]
        pieces, hist, tool = K.split_by_plane(part.shape, plane, with_history=True)
        if p.get("keep") == "front":
            pieces = [s_ for s_ in pieces if (K.mass_props(s_)["centroid"] - plane.origin) @ plane.normal > 0]
        elif p.get("keep") == "back":
            pieces = [s_ for s_ in pieces if (K.mass_props(s_)["centroid"] - plane.origin) @ plane.normal < 0]
        if not pieces:
            raise K.KernelError("Nothing left on that side of the plane")
        whole = K.compound(pieces) if len(pieces) > 1 else pieces[0]
        # the splitter's pieces are named together; set_part_shape gives each piece the names of its own elements
        named = name_feature(state, f["id"], [pid, _tool_names(state, f["id"], tool, "cut")], whole, hist, "split")
        set_part_shape(state, part, whole, named)


def run_move(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    v = (float(p.get("dx", 0)), float(p.get("dy", 0)), float(p.get("dz", 0)))
    ang = float(p.get("rotate", 0))
    a, d = (np.zeros(3), np.array([0, 0, 1.0]))
    if abs(ang) > 1e-12:
        a, d = resolve_axis(state, p.get("axis") or {"kind": "std", "name": "Z"})
    for pid in ids:
        part = state.parts[pid]
        moved, hist = K.transform(part.shape, v, a, d, ang, with_history=True)
        named = name_feature(state, f["id"], [pid], moved, hist, "move")
        if p.get("copy"):
            np_ = state.new_part(moved, f["id"], part.color)
            state.parts[np_.id] = Part(np_.id, part.name + " (copy)", moved, part.color, part.face_colors)
            state.set_named(np_.id, named)
        else:
            state.parts[pid] = Part(pid, part.name, moved, part.color, part.face_colors)
            state.set_named(pid, named)


def run_sm_base(state, f):
    """Sheet metal from sketch regions: a flat sheet of the given thickness (one part per region)."""
    from . import sheetmetal as SM
    p = f["params"]
    regions = resolve_regions(state, p.get("regions", []), f["id"])
    if not regions:
        raise K.KernelError("Select the sketch region(s) for the sheet")
    t, r, k = float(p.get("thickness", 1.5)), float(p.get("radius", 1.5)), float(p.get("k", 0.44))
    if t <= 0:
        raise K.KernelError("The thickness must be more than 0")
    if not (0 < k <= 1):
        raise K.KernelError("The K-factor is between 0 and 1 (0.44 suits most steel and aluminium)")
    plane = state.sketches[p["regions"][0]["sketch"]].plane
    for face in regions:
        solid, model, hist = SM.make_base(face, plane, t, r, k, bool(p.get("flip")),
                                          "rect" if p.get("relief", True) else "none",
                                          float(p.get("relief_scale", 1.5)), with_history=True)
        # a sheet is a prism of the region: named like an extrude from the sketch's names
        named = name_feature(state, f["id"], _profile_names(state, p), solid, hist, "extrude")
        part = state.new_part(solid, f["id"])
        state.set_named(part.id, named)
        state.sheet[part.id] = model


def run_sm_flange(state, f):
    """Flanges: bend the sheet up (or down) along the chosen edges."""
    from . import sheetmetal as SM
    p = f["params"]
    refs = p.get("edges", [])
    if not refs:
        raise K.KernelError("Pick the edges to put flanges on")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for part in state.parts.values():
        a, b = K.bbox(part.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    for ref in refs:
        pid = ref.get("part")
        if pid not in state.sheet:
            # the ref may name a part that has since been renumbered: try every sheet metal part
            pid = next(iter(state.sheet), None) if len(state.sheet) == 1 else pid
        model = state.sheet.get(pid)
        part = state.parts.get(pid)
        if model is None or part is None:
            raise K.KernelError("Flanges go on sheet metal parts — make the part with Sheet metal first")
        panel, a, b, o = model.find_edge(ref, scale)
        radius = float(p["radius"]) if p.get("own_radius") else None
        shape, model, hist, seeds = SM.add_flange(part.shape, model, panel, a, b, o, float(p.get("length", 20)),
                                                  float(p.get("angle", 90)), radius, not p.get("down"),
                                                  p.get("position", "outside"), f["id"], with_history=True)
        # the part's own faces keep their names (stamp=False); the bend and the new panel are named from seeds
        named = name_feature(state, f["id"], [pid] + seeds, shape, hist, "fuse", stamp=False)
        state.parts[pid] = Part(pid, part.name, shape, part.color)
        state.set_named(pid, named)
        state.sheet[pid] = model


RUNNERS = {
    "sketch": run_sketch, "extrude": run_extrude, "revolve": run_revolve, "fillet": run_fillet,
    "chamfer": run_chamfer, "shell": run_shell, "plane": run_plane, "mirror": run_mirror,
    "linear_pattern": run_linear_pattern, "circular_pattern": run_circular_pattern,
    "boolean": run_boolean, "import": run_import, "hole": run_hole, "variable": run_variable,
    "variable_studio_ref": run_variable_studio_ref,
    "sweep": run_sweep, "loft": run_loft, "draft": run_draft, "split": run_split, "move": run_move,
    "thread": run_thread, "sm_base": run_sm_base, "sm_flange": run_sm_flange,
    "delete_face": run_delete_face, "move_face": run_move_face, "offset_surface": run_offset_surface,
}


def default_params(ftype: str, state: ModelState | None = None) -> dict:
    has_parts = bool(state and state.parts)
    op = "add" if has_parts else "new"
    return {
        "sketch": {"plane": {"kind": "std", "name": "Top"}, "sketch": Sketch().to_dict()},
        "extrude": {"regions": [], "faces": [], "end": "blind", "depth": 25.0, "depth2": 10.0,
                    "second": False, "flip": False, "op": op},
        "revolve": {"regions": [], "faces": [], "axis": {}, "end": "full", "angle": 180.0,
                    "flip": False, "op": op},
        "fillet": {"edges": [], "radius": 2.0},
        "chamfer": {"edges": [], "distance": 1.0},
        "shell": {"faces": [], "thickness": 2.0, "outward": False},
        "delete_face": {"faces": [], "mode": "heal"},
        "move_face": {"faces": [], "distance": 2.0, "flip": False},
        "offset_surface": {"faces": [], "distance": 2.0, "flip": False},
        "plane": {"type": "auto", "refs": [], "offset": 20.0, "angle": 45.0, "flip": False},
        "mirror": {"parts": [], "plane": {"kind": "std", "name": "Right"}, "op": "add"},
        "linear_pattern": {"parts": [], "direction": {"kind": "std", "name": "X"}, "count": 3,
                           "spacing": 30.0, "flip": False, "op": "new"},
        "circular_pattern": {"parts": [], "axis": {"kind": "std", "name": "Z"}, "count": 6,
                             "angle": 360.0, "op": "new"},
        "boolean": {"op": "union", "tools": [], "targets": [], "keep_tools": False},
        "import": {"data": "", "filename": ""},
        "variable": {"name": "", "expr": "10"},
        "variable_studio_ref": {"source": {}},
        "thread": {"faces": [], "size": "auto", "major": 6.0, "pitch": 1.0, "length_mode": "full", "length": 10.0,
                   "left_hand": False, "clearance": 0.0},
        "hole": {"sketch": None, "standard": "clearance", "size": "M4", "fit": "normal", "diameter": 5.0,
                 "model_thread": False, "thread_clearance": 0.0, "left_hand": False,
                 "depth": 10.0, "end": "through", "style": "simple", "tip": True,
                 "counterbore_dia": 9.0, "counterbore_depth": 3.0, "countersink_dia": 10.0},
        "sweep": {"regions": [], "faces": [], "path": [], "op": op},
        "loft": {"profiles": [], "ruled": False, "op": op},
        "draft": {"faces": [], "neutral": {"kind": "std", "name": "Top"}, "angle": 3.0, "flip": False},
        "split": {"parts": [], "plane": {"kind": "std", "name": "Right"}, "keep": "both"},
        "move": {"parts": [], "dx": 0.0, "dy": 0.0, "dz": 0.0, "axis": {"kind": "std", "name": "Z"},
                 "axis_std": "Z", "rotate": 0.0, "copy": False},
        "sm_base": {"regions": [], "thickness": 1.5, "radius": 1.5, "k": 0.44, "flip": False, "relief": True,
                    "relief_scale": 1.5},
        "sm_flange": {"edges": [], "length": 20.0, "angle": 90.0, "position": "outside", "down": False,
                      "own_radius": False, "radius": 1.5},
    }[ftype]


def _matched_faces(src_shape, src_faces, dst_shape):
    """Faces of dst_shape that are the given faces of src_shape — unchanged, or trimmed by a later feature."""
    if src_shape.IsSame(dst_shape):
        return set(src_faces)
    fake = Part("x", "x", src_shape, DEFAULT_COLOR, {i: (1, 1, 1) for i in src_faces})
    return set((carry_face_colors(fake, dst_shape) or {}).keys())


def feature_new_faces(before: ModelState, after: ModelState):
    """{part id: set of face indices} that a feature created (faces of `after` with no counterpart in `before`)."""
    out = {}
    for pid, part in after.parts.items():
        old = before.parts.get(pid) or before.parts.get(pid.split(".")[0])
        n = len(K.faces(part.shape))
        if old is None:
            out[pid] = set(range(n))
            continue
        if old.shape.IsSame(part.shape):
            continue
        existing = _matched_faces(old.shape, range(len(K.faces(old.shape))), part.shape)
        new = set(range(n)) - existing
        if new:
            out[pid] = new
    return out


def map_faces_to(state_from: ModelState, faces_by_part, state_to: ModelState):
    """Follow faces from one model state to a later one (the faces the user sees now)."""
    out = {}
    for pid, fis in faces_by_part.items():
        src = state_from.parts.get(pid)
        if src is None:
            continue
        targets = [p for q, p in state_to.parts.items() if q == pid or q.split(".")[0] == pid]
        for p in targets:
            got = _matched_faces(src.shape, fis, p.shape)
            if got:
                out[p.id] = got
    return out
