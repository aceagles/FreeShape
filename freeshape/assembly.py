"""Assemblies: instances of parts placed in space and connected by mates, like Onshape's Assembly tab.

* An **instance** is a part from a Part Studio tab (live-linked: edit the part and the assembly updates),
  a saved .fshape file, or an embedded STEP file, plus a 4x4 placement. A fixed instance never moves.
* A **mate** joins a mate connector (a frame: origin + Z + X axes) on one instance to one on another.
  Connectors come from what you click: a flat face, a round face (its axis, at the end nearest the click),
  a circular edge (its centre and axis), or a straight edge.
* The solver moves the free instances, by as little as possible, until every mate holds. While a part is
  dragged, the mates have priority and the drag follows as closely as they allow — so a revolute spins,
  a slider slides and a fastened part just comes along.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import uuid

import numpy as np

from . import kernel as K
from . import measure as MS
from . import migrations as M
from . import sketchref

MATE_TYPES = {
    "fastened": ("Fastened", "No motion — the parts are locked together"),
    "revolute": ("Revolute", "Rotates about the connector's Z axis, like a hinge or an axle"),
    "slider": ("Slider", "Slides along the Z axis without rotating"),
    "cylindrical": ("Cylindrical", "Slides along and rotates about the Z axis, like a shaft in a bearing"),
    "planar": ("Planar", "Slides and rotates on a plane, like a block on a table"),
    "pin_slot": ("Pin slot", "A pin in a slot: slides along the first connector's X axis and rotates about Z"),
    "parallel": ("Parallel", "Keeps the two Z axes parallel; the part moves and turns freely otherwise"),
    "ball": ("Ball", "Rotates in every direction about a point"),
}
MATE_DOF = {"fastened": 0, "revolute": 1, "slider": 1, "cylindrical": 2, "pin_slot": 2, "planar": 3, "parallel": 4, "ball": 3}
# The named free degrees of freedom of each mate type: its Mate values (translations in mm, rotations in degrees).
MATE_VALUE_DOFS = {
    "fastened": (),
    "revolute": ("rz",),
    "slider": ("tz",),
    "cylindrical": ("tz", "rz"),
    "planar": ("tx", "ty", "rz"),
    "pin_slot": ("tx", "rz"),
    "parallel": ("tx", "ty", "tz", "rz"),
    "ball": (),
}
# The kind of each freedom (from its name: r* turns, t* slides), its stored unit, and how a residual in that
# unit is scaled to mm like the mate residuals: a rotation as mm of arc at the part's size L, a translation as is.
DOF_KINDS = {
    "rotation": {"unit": "deg", "scale": lambda L: np.radians(1.0) * L, "ui_unit": "rev", "per_ui_unit": 360.0},
    "translation": {"unit": "mm", "scale": lambda L: 1.0, "ui_unit": "mm", "per_ui_unit": 1.0},
}


def dof_kind(dof):
    """The kind of a Mate value name: "rz" is a rotation, "tz" a translation."""
    return "rotation" if dof.startswith("r") else "translation"


# Relation types: the kind of freedom each side accepts. The user's ratio is in b's UI unit per a's UI unit
# (DOF_KINDS "ui_unit": a rotation counts in revolutions, a translation in mm); "ratio_unit" is its label.
# Stored values are in DOF_KINDS "unit" (deg, mm), so relation_ratio() converts: value_b = sign x ratio x
# value_a + phase holds in stored units (ADR 0005). "same_mate" marks a type whose two sides are one mate.
RELATION_TYPES = {
    "linear": {"label": "Linear", "a": "translation", "b": "translation", "ratio_unit": "mm/mm",
               "help": "Ties one slide to another: the second moves ratio times as far"},
    "gear": {"label": "Gear", "a": "rotation", "b": "rotation", "ratio_unit": "turns/turn",
             "help": "Turns the driven mate ratio turns for each turn of the driving mate"},
    "rack_pinion": {"label": "Rack and pinion", "a": "rotation", "b": "translation", "ratio_unit": "mm/rev",
                    "help": "Moves the rack ratio mm for each turn of the pinion"},
    "screw": {"label": "Screw", "a": "rotation", "b": "translation", "ratio_unit": "mm/rev", "same_mate": True,
              "help": "Moves a cylindrical mate along its axis ratio mm for each turn about it"},
}


def relation_ratio(r):
    """r's ratio in stored units (b's deg or mm per a's deg or mm), converted from the user's ratio by the
    unit sizes the type's two kinds carry in DOF_KINDS."""
    spec = RELATION_TYPES[r["type"]]
    return float(r["ratio"]) * DOF_KINDS[spec["b"]]["per_ui_unit"] / DOF_KINDS[spec["a"]]["per_ui_unit"]


FILE_FORMAT = "freeshape-assembly"


def _upgrade_part_id_syntax(data):
    """FILE_VERSION 1 -> 2 (I1.4): an instance's source may still name its Part Studio part with the old
    <fid>:<counter> id. Nothing stored here needs to change for that -- LinkResolver translates it through
    the linked studio's alias table (links.py) until the save-time repair step (links.repair_source)
    rewrites it to <fid>/<n> -- so this step only marks the file as built by code that understands the new
    syntax (bumping the version lets a build from before I1.4 refuse to load it instead of guessing wrong)."""
    return data


M.register("assembly", _upgrade_part_id_syntax)


def _add_groups(data):
    """Assembly file version 2 -> 3 (I4.1): add the empty list of groups (rigid sets of instances)."""
    data.setdefault("groups", [])
    return data


M.register("assembly", _add_groups)


def _upgrade_mate_values(data):
    """Assembly file version 3 -> 4 (I1.2): each mate stores its Mate values ("values"). Measuring them needs the
    resolved parts, so this step only marks the version; AssemblyDoc.load fills any mate that has no
    values from the saved pose once the instances are resolved."""
    return data


M.register("assembly", _upgrade_mate_values)


def _add_mate_limits(data):
    """Assembly file version 4 -> 5 (I5.1): a mate may carry "limits", {dof: [min, max]} with either end null.
    Older files have none, so this step only marks the version."""
    return data


M.register("assembly", _add_mate_limits)


def _add_relations(data):
    """Assembly file version 5 -> 6 (I2.1): add the empty list of Relations."""
    data.setdefault("relations", [])
    return data


M.register("assembly", _add_relations)


def _upgrade_standard_part_source(data):
    """Assembly file version 6 -> 7 (Standard Parts I2.1; version 2 -> 3 on the standard-parts branch before it
    was merged with Mates Parity): an instance's source may be {"kind": "standard", "type": "sp:...", ...}, a
    Standard part generated by a Plugin. Nothing stored needs to change; bumping the version makes a build
    from before it refuse the file with its newer-version message instead of failing on the unknown kind."""
    return data


M.register("assembly", _upgrade_standard_part_source)


def _move_geometry_to_blobs(data):
    """Assembly file version 7 -> 8 (I4.9, ADR 0009): binary data moves to the store's blobs, addressed by sha256.
    An instance's base64 `_brep` becomes `_brep_blob`, the key of the same bytes; the base64 STEP files in
    `blobs` leave the file and a `step` source's `blob` becomes the sha256 key of the decoded bytes. The bytes
    wait in `data[blobstore.HELD]` for `from_data`, which keeps them until the next save writes them out: a
    step has no store to write to (a loose file, a read-only Version)."""
    from . import blobstore
    old = data.pop("blobs", None)
    renamed = {}
    for key, text in (old.items() if isinstance(old, dict) else ()):
        new = blobstore.hold_base64(data, text)
        if new is not None:
            renamed[key] = new
    for inst in data.get("instances", []):
        src = inst.get("source") or {}
        if src.get("kind") == "step" and src.get("blob") in renamed:
            src["blob"] = renamed[src["blob"]]
        key = blobstore.hold_base64(data, inst.pop("_brep", None))
        if key is not None:
            inst["_brep_blob"] = key
    return data


M.register("assembly", _move_geometry_to_blobs)


INSTANCE_COLORS = [(0.60, 0.74, 0.85), (0.72, 0.74, 0.78), (0.86, 0.66, 0.40), (0.56, 0.78, 0.56),
                   (0.85, 0.55, 0.55), (0.70, 0.60, 0.85), (0.50, 0.78, 0.78), (0.85, 0.78, 0.45)]


# =========================================================================== small rigid-body maths

def unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-15 else v


def perp(z):
    """A deterministic unit vector perpendicular to z (global X if possible, else global Y)."""
    z = unit(z)
    for a in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        x = a - (a @ z) * z
        if np.linalg.norm(x) > 0.3:
            return unit(x)
    return unit(np.cross(z, [1.0, 0, 0]))


def rodrigues(w):
    """Rotation matrix for a rotation vector (axis * angle in radians)."""
    th = float(np.linalg.norm(w))
    if th < 1e-14:
        return np.eye(3)
    k = w / th
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(th) * Kx + (1 - np.cos(th)) * (Kx @ Kx)


def rot_about(axis, angle_rad, point=(0, 0, 0)):
    """4x4 rotation about an axis through a point."""
    M = np.eye(4)
    R = rodrigues(unit(axis) * angle_rad)
    p = np.asarray(point, float)
    M[:3, :3] = R
    M[:3, 3] = p - R @ p
    return M


def translation(v):
    M = np.eye(4)
    M[:3, 3] = np.asarray(v, float)
    return M


def orthonormalize(M):
    """Remove numerical drift from a rigid transform."""
    M = np.array(M, float)
    U, _s, Vt = np.linalg.svd(M[:3, :3])
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    M[:3, :3] = R
    M[3] = [0, 0, 0, 1]
    return M


def frame_matrix(fr):
    """Connector dict {o, z, x} -> 4x4 whose columns are x, y, z, origin."""
    z = unit(fr["z"])
    x = unit(np.asarray(fr["x"], float) - (np.asarray(fr["x"], float) @ z) * z)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = x, np.cross(z, x), z, fr["o"]
    return M


def transform_frame(M, fr):
    M = np.asarray(M, float)
    return {"o": (M[:3, :3] @ fr["o"] + M[:3, 3]), "z": M[:3, :3] @ fr["z"], "x": M[:3, :3] @ fr["x"]}


# =========================================================================== mate connectors from geometry

def _adjacent_faces(shape, edge):
    out = []
    for f in K.faces(shape):
        if any(e.IsSame(edge) for e in K.edges(f)):
            out.append(f)
    return out


def connector_frame(shape, kind, index, click=None):
    """A mate connector (in the part's own coordinates) for a face or edge of `shape`.
    `click` (a point near where the user clicked) chooses the end of a round face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus, \
        GeomAbs_Line, GeomAbs_Circle
    centre = (np.add(*K.bbox(shape))) / 2
    if kind == "face":
        f = K.faces(shape)[index]
        info = K.face_info(f)
        ad = BRepAdaptor_Surface(f)
        st = ad.GetType()
        if st == GeomAbs_Plane:
            z = unit(K.face_outward_normal(f))  # not info["n"]: that points inward on a left-handed plane
            o = np.array(info["c"], float)
            # X along the longest straight edge, so square things line up squarely. This only picks X when the
            # connector is made: the connector then stores X against the plane's own axes ("xb"; see
            # connector_x_ref), because an edit can change which edge is longest.
            best, x = 0.0, None
            for e in K.edges(f):
                ei = K.edge_info(e)
                if "d" in ei and ei["len"] > best:
                    best, x = ei["len"], np.array(ei["d"], float)
            x = unit(x - (x @ z) * z) if x is not None else perp(z)
            px = K._np(ad.Plane().XAxis().Direction())
            px = unit(px - (px @ z) * z)
            return {"o": o, "z": z, "x": x, "what": "flat face", "xb": (px, np.cross(z, px))}
        if st in (GeomAbs_Cylinder, GeomAbs_Cone):
            if st == GeomAbs_Cylinder:
                p0, d, _r, zmin, zmax, _hole = K.cylinder_face_frame(f)
            else:
                ax = ad.Cone().Axis()
                p0, d = K._np(ax.Location()), unit(K._np(ax.Direction()))
                zs = [float((v - p0) @ d) for v in K._vertices_np(f)] or [0.0]
                poly = [np.asarray(q, float) for e in K.edges(f) for q in K.discretize_edge(e, 0.05)]
                zs += [float((q - p0) @ d) for q in poly]
                zmin, zmax = min(zs), max(zs)
            d = unit(d)
            zc = (zmin + zmax) / 2
            if click is not None:
                zk = float((np.asarray(click, float) - p0) @ d)
                cand = [(abs(zk - zmin), zmin, -1), (abs(zk - zc), zc, 0), (abs(zk - zmax), zmax, 1)]
                _dist, zz, end = min(cand)
            else:
                zz, end = zc, 0
            o = p0 + zz * d
            z = -d if end < 0 else d
            where = {-1: "end", 0: "middle", 1: "end"}[end]
            return {"o": o, "z": z, "x": perp(z), "what": f"round face ({where})"}
        if st == GeomAbs_Sphere:
            o = K._np(ad.Sphere().Location())
            return {"o": o, "z": np.array([0, 0, 1.0]), "x": np.array([1.0, 0, 0]), "what": "sphere centre"}
        if st == GeomAbs_Torus:
            ax = ad.Torus().Axis()
            z = unit(K._np(ax.Direction()))
            return {"o": K._np(ax.Location()), "z": z, "x": perp(z), "what": "torus axis"}
        # anything else: its centroid, normal at the middle
        from OCP.BRepLProp import BRepLProp_SLProps
        u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
        v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
        pr = BRepLProp_SLProps(ad, u, v, 1, 1e-7)
        z = unit(K._np(pr.Normal()))
        from OCP.TopAbs import TopAbs_REVERSED
        if f.Orientation() == TopAbs_REVERSED:
            z = -z
        return {"o": np.array(info["c"], float), "z": z, "x": perp(z), "what": "face"}

    e = K.edges(shape)[index]
    info = K.edge_info(e)
    ad = BRepAdaptor_Curve(e)
    ct = ad.GetType()
    if ct == GeomAbs_Circle:
        o = np.array(info["center"], float)
        z = unit(info["axis"])
        # point Z out of the material: along the outward normal of the flat face the circle sits on
        sign = None
        for f in _adjacent_faces(shape, e):
            if "n" in K.face_info(f):
                fn = K.face_outward_normal(f)
                if abs(float(np.dot(fn, z))) > 0.99:
                    sign = 1.0 if float(np.dot(fn, z)) > 0 else -1.0
                    break
        if sign is None:
            sign = 1.0 if float((o - centre) @ z) >= 0 else -1.0
        z = sign * z
        return {"o": o, "z": z, "x": perp(z), "what": "circular edge"}
    if ct == GeomAbs_Line:
        p0, p1 = np.array(info["p0"], float), np.array(info["p1"], float)
        z = unit(p1 - p0)
        return {"o": (p0 + p1) / 2, "z": z, "x": perp(z), "what": "straight edge"}
    t = (ad.FirstParameter() + ad.LastParameter()) / 2
    from OCP.gp import gp_Pnt, gp_Vec
    p, v = gp_Pnt(), gp_Vec()
    ad.D1(t, p, v)
    z = unit(np.array([v.X(), v.Y(), v.Z()]))
    return {"o": K._np(p), "z": z, "x": perp(z), "what": "edge"}


def _x_basis(fr):
    """The two axes a connector's X is stored against: the plane's own X and Z x X for a flat face (FreeCAD's
    Assembly orients a planar-face joint by the plane's own placement too), else perp(Z) and Z x perp(Z)."""
    if fr.get("xb") is not None:
        return fr["xb"]
    z = unit(fr["z"])
    bx = perp(z)
    return bx, np.cross(z, bx)


def connector_x_ref(fr):
    """The stored X reference of a connector frame: X's components [u, v] along the frame's base axes."""
    bx, by = _x_basis(fr)
    x = np.asarray(fr["x"], float)
    return [float(x @ bx), float(x @ by)]


def apply_x_ref(fr, x_ref):
    """`fr` with X rebuilt from a stored X reference, so X does not depend on the face's edge lengths."""
    try:
        u, v = float(x_ref[0]), float(x_ref[1])
    except (TypeError, ValueError, IndexError):
        return fr
    bx, by = _x_basis(fr)
    x = u * bx + v * by
    if np.linalg.norm(x) < 1e-9:
        return fr
    return dict(fr, x=unit(x))


# =========================================================================== hover snap points

SNAP_WHAT = {"center": "circle centre", "mid": "edge midpoint", "vertex": "vertex", "centroid": "face centre"}


def snap_candidates(shape, kind, index):
    """Snap points (part coordinates) offered while hovering a face or edge: circle/arc centres, straight-edge
    midpoints, vertices and, for a face, its centroid. Each is {"type", "p", "edge"} where `edge` is the index of
    the edge it comes from (centres and midpoints) or None."""
    from OCP.GeomAbs import GeomAbs_Line, GeomAbs_Circle
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    all_e = K.edges(shape)

    def eidx(e):
        for i, o in enumerate(all_e):
            if o.IsSame(e):
                return i
        return None

    if kind == "face":
        f = K.faces(shape)[index]
        planar = "n" in K.face_info(f)
        subs, vsrc = K.edges(f), f
    else:
        subs, vsrc, planar = [all_e[index]], all_e[index], True
    centres, mids = [], []
    for e in subs:
        ct = BRepAdaptor_Curve(e).GetType()
        ei = K.edge_info(e)
        if ct == GeomAbs_Circle:
            centres.append({"type": "center", "p": np.array(ei["center"], float), "edge": eidx(e)})
        elif ct == GeomAbs_Line and planar:
            mids.append({"type": "mid", "p": (np.array(ei["p0"], float) + np.array(ei["p1"], float)) / 2,
                         "edge": eidx(e)})
    verts = [{"type": "vertex", "p": np.asarray(v, float), "edge": None} for v in K._vertices_np(vsrc)]
    out = centres + verts + mids
    if kind == "face":
        out.append({"type": "centroid", "p": np.array(K.face_info(K.faces(shape)[index])["c"], float), "edge": None})
    lo, hi = K.bbox(shape)
    tol = max(float(np.linalg.norm(hi - lo)), 1e-6) * 1e-6
    kept = []
    for c in out:                                   # coincident points: the first (most specific) wins
        if not any(np.linalg.norm(c["p"] - k["p"]) < tol for k in kept):
            kept.append(c)
    return kept


def snap_frame(shape, kind, index, snap, named=None):
    """Connector frame for a snap point on face/edge `index`. `snap` is the stored form {"type", "p", "edge_ref"}.
    Z is the face normal (the circle axis, signed outward, for a centre); X is the part's X axis projected
    onto the plane (perp), so it does not depend on which edge happens to be longest. `named` is the part's
    element map, used to find the snap edge by its name."""
    t = snap["type"]
    p = np.asarray(snap["p"], float)
    if t == "center":
        ei = resolve_element(shape, snap["edge_ref"], "edge", named)[0]
        base = connector_frame(shape, "edge", ei)
        o = np.array(K.edge_info(K.edges(shape)[ei])["center"], float)
    elif t == "mid":
        ei = resolve_element(shape, snap["edge_ref"], "edge", named)[0]
        info = K.edge_info(K.edges(shape)[ei])
        o = (np.array(info["p0"], float) + np.array(info["p1"], float)) / 2
        base = connector_frame(shape, kind, index, o if kind == "face" else None)
    elif t == "vertex":
        vs = [c["p"] for c in snap_candidates(shape, kind, index) if c["type"] == "vertex"]
        if not vs:
            raise ValueError("no vertex")
        o = min(vs, key=lambda v: float(np.linalg.norm(v - p)))
        base = connector_frame(shape, kind, index, o if kind == "face" else None)
    else:
        base = connector_frame(shape, "face", index)
        o = base["o"]
    z = unit(base["z"])
    return {"o": np.asarray(o, float), "z": z, "x": perp(z), "what": SNAP_WHAT.get(t, "snap point")}


def frame_to_json(fr):
    return {"o": [float(a) for a in fr["o"]], "z": [float(a) for a in fr["z"]], "x": [float(a) for a in fr["x"]],
            "what": fr.get("what", "")}


def frame_from_json(d):
    return {"o": np.array(d["o"], float), "z": unit(d["z"]), "x": unit(d["x"]), "what": d.get("what", "")}


def _ref_signature(shape, kind, index, named=None):
    """The stored reference to face/edge `index` of `shape`: its signature and, when the part's element map
    `named` names it, its name (features.make_ref)."""
    from .features import make_ref
    sub = K.faces(shape)[index] if kind == "face" else K.edges(shape)[index]
    ref = make_ref(None, sub, kind, shape, named)
    ref.pop("part", None)
    return json.loads(json.dumps(ref, default=lambda a: np.asarray(a).tolist()))


def resolve_element(shape, ref, kind, named=None):
    """(index, recovered) of the face/edge of `shape` a stored reference points at, through features.resolve
    (docs/reference/element-naming.md, section 6.4): a named reference finds the element of that name or
    raises a KernelError naming it; a reference without a name (or a part that has no element map) is
    matched by its signature and `recovered` is True. A part with no element map and a nameless
    reference (a STEP part, a headless reader) has nothing better to offer, so that one is not flagged."""
    from .features import ModelState, Part, resolve
    pid, fid = "part", "connector"
    state = ModelState(parts={pid: Part(pid, pid, shape)}, names={pid: named} if named is not None else {})
    had_name = bool(ref.get("name"))
    if named is None:
        ref = {k: v for k, v in ref.items() if k != "name"}
    _, sub = resolve(state, dict(ref, part=pid), kind, fid=fid)
    subs = K.faces(shape) if kind == "face" else K.edges(shape)
    for i, s in enumerate(subs):
        if s.IsSame(sub):
            return i, fid in state.recovered and (named is not None or had_name)
    raise K.KernelError(f"Missing {kind} reference")


# =========================================================================== mate residuals

def mate_residuals(m, FA, FB, L):
    """Residual vector of one mate given world connector frames A and B. Zero when the mate holds.
    Default alignment is Z axes opposed (faces touching / pin seated); `flip` aligns them instead."""
    oA, zA, xA = FA["o"], FA["z"], FA["x"]
    oB, zB, xB = FB["o"], FB["z"], FB["x"]
    t = m["type"]
    s = 1.0 if m.get("flip") else -1.0
    d = float(m.get("offset", 0.0) or 0.0)
    ang = np.radians(float(m.get("angle", 0.0) or 0.0))
    yA = np.cross(zA, xA)
    xt = np.cos(ang) * xA + np.sin(ang) * yA
    out = []
    if t in ("fastened", "revolute", "ball"):
        out.append(oB - (oA + d * zA))
    if t != "ball":
        out.append((zB - s * zA) * L)               # parallel holds only this (opposed, or aligned with flip)
    if t in ("fastened", "slider"):
        out.append((xB - xt) * L)
    if t in ("slider", "cylindrical"):
        dv = oB - oA
        out.append(dv - (dv @ zA) * zA)
    if t == "planar":
        out.append(np.array([(oB - oA) @ zA - d]))
    if t == "pin_slot":
        # B's origin on the line through A's origin along A's X (offset d along Z); the slot ignores `angle`
        dv = oB - oA
        out.append(np.array([dv @ yA, dv @ zA - d]))
    return np.concatenate(out) if out else np.zeros(0)


def snap_transform(m, FA, FB):
    """The rigid motion that carries connector B exactly onto its mated position against A
    (a configuration that satisfies every mate type)."""
    s = 1.0 if m.get("flip") else -1.0
    d = float(m.get("offset", 0.0) or 0.0)
    ang = np.radians(float(m.get("angle", 0.0) or 0.0))
    zA, xA = unit(FA["z"]), unit(FA["x"])
    yA = np.cross(zA, xA)
    xt = np.cos(ang) * xA + np.sin(ang) * yA
    zt = s * zA
    if m["type"] == "parallel":
        # turn B about its own origin by the smallest rotation that makes the Z axes parallel; no translation
        zB = unit(FB["z"])
        axis = np.cross(zB, zt)
        sn, cs = float(np.linalg.norm(axis)), float(zB @ zt)
        if sn < 1e-12:
            if cs > 0:
                return np.eye(4)
            xb = unit(FB["x"])
            axis, sn = xb, 1.0                      # already antiparallel: half a turn about any axis across Z
        return rot_about(axis / sn, np.arctan2(sn, cs), FB["o"])
    target = frame_matrix({"o": FA["o"] + d * zA, "z": zt, "x": xt})
    if m["type"] in ("slider", "cylindrical", "planar", "pin_slot"):
        # keep B where it is along the free directions: only fix what the mate fixes
        dv = FB["o"] - FA["o"]
        if m["type"] == "pin_slot":
            o = FA["o"] + d * zA + (dv @ xA) * xA
        elif m["type"] == "planar":
            o = FB["o"] - ((dv @ zA) - d) * zA
        else:
            o = FA["o"] + (dv @ zA) * zA
        target[:3, 3] = o
    if m["type"] in ("revolute", "cylindrical", "planar", "pin_slot", "ball"):
        # keep B's current spin about the axis instead of forcing X onto A's X
        xb = np.asarray(FB["x"], float)
        if m["type"] == "ball":
            return translation(FA["o"] + d * zA - FB["o"])
        xb = xb - (xb @ zt) * zt
        if np.linalg.norm(xb) > 1e-6:
            xb = unit(xb)
            M = frame_matrix({"o": target[:3, 3], "z": zt, "x": xb})
            # the frame's own z can't change sign by projecting, so this is a valid target
            target = M
    return target @ np.linalg.inv(frame_matrix(FB))


def _wrap_deg(a):
    """Wrap an angle in degrees to (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a <= -180.0 + 1e-9 else a


def mate_values(m, FA, FB):
    """The Mate values of one mate, measured from the world connector frames A and B.
    Returns {name: value} for the names in MATE_VALUE_DOFS[type]; empty for fastened and ball.
    Zero is the aligned pose snap_transform gives (flip, offset and angle already applied, B's
    X on the target X). Translations are in mm along A's axes: tz along A's Z, tx and ty along the
    target X and its Y (A's Z crossed with it). rz is the turn of B's X about A's Z, in degrees."""
    names = MATE_VALUE_DOFS[m["type"]]
    if not names:
        return {}
    ang = np.radians(float(m.get("angle", 0.0) or 0.0))
    if m["type"] in ("pin_slot", "parallel"):
        ang = 0.0                                   # the slot runs along A's X whatever `angle` says
    zA, xA = unit(FA["z"]), unit(FA["x"])
    yA = np.cross(zA, xA)
    xt = np.cos(ang) * xA + np.sin(ang) * yA
    yt = np.cross(zA, xt)
    dv = np.asarray(FB["o"], float) - np.asarray(FA["o"], float)
    xB = np.asarray(FB["x"], float)
    out = {}
    for n in names:
        if n == "tz":
            out[n] = float(dv @ zA)
        elif n == "tx":
            out[n] = float(dv @ xt)
        elif n == "ty":
            out[n] = float(dv @ yt)
        else:
            out[n] = _wrap_deg(float(np.degrees(np.arctan2(xB @ yt, xB @ xt))))
    return out


# =========================================================================== the document

ANIMATE_PLAYBACKS = ("single", "reciprocate", "loop")
ANIMATE_MAX_RANGE = 36000.0   # degrees (or mm): the widest range one Animate run covers


def animate_values(start, end, steps, playback):
    """Values an Animate run steps through: `steps` evenly spaced values from start to end ("single" and "loop",
    where the caller repeats the sequence), or that sequence followed by its reverse without repeating the end
    value ("reciprocate"). Raises ValueError for steps below 2, an unknown playback, or a range over 36000."""
    if playback not in ANIMATE_PLAYBACKS:
        raise ValueError(f"Unknown playback {playback!r}")
    steps = int(steps)
    if steps < 2:
        raise ValueError("Animate needs at least 2 steps")
    if abs(float(end) - float(start)) > ANIMATE_MAX_RANGE:
        raise ValueError(f"Animate range is limited to {ANIMATE_MAX_RANGE:g}")
    seq = [float(v) for v in np.linspace(float(start), float(end), steps)]
    if playback == "reciprocate":
        seq += seq[-2::-1]
    return seq


def is_sketch_source(source):
    """True when an instance source is a Sketch instance's: {"kind": "sketch", "uid", "sketch"} (I1.2)."""
    return isinstance(source, dict) and source.get("kind") == "sketch"


def capture_context(asm, iid):
    """Plain data for editing instance `iid` in context (ADR 0004): every other instance with a part, as it
    stands now. A Sketch instance is a neighbour like a part (its shape is edges and vertices, its element names
    are empty); `iid` itself must be a part instance. Each neighbour has its id, name, content hash (sha256 of its BREP bytes), BREP bytes,
    element names (as `_named` has them, {} when the part has none) and `T`, its placement relative to `iid`
    (inverse of `iid`'s transform times the neighbour's, 4x4 nested lists). `solved` is False while any
    mate is shown red. Reads the current state; changes nothing."""
    import hashlib
    from . import blobstore
    if asm.instance(iid) is None:
        raise K.KernelError(f"No instance {iid}")
    if asm.is_sketch_instance(iid):
        raise K.KernelError("A Context belongs to a part: it can't be taken from a Sketch instance")
    inv = np.linalg.inv(asm.T(iid))
    neighbours = []
    for inst in asm.instances:
        nid = inst["id"]
        if nid == iid or nid not in asm.parts:
            continue
        data = blobstore.brep_bytes(asm.parts[nid].shape)
        named = asm._named(nid)
        neighbours.append({"id": nid, "name": inst["name"], "hash": hashlib.sha256(data).hexdigest(),
                           "brep": data, "names": dict(named.names) if named is not None else {},
                           "T": (inv @ asm.T(nid)).tolist()})
    solved = not any(v.startswith("Can't be solved") for v in asm.errors.values())
    return {"assembly": asm.uid, "instance": iid, "solved": solved, "neighbours": neighbours}


class AssemblyDoc:
    kind = "assembly"

    def __init__(self):
        self.uid = uuid.uuid4().hex
        self.name = "Assembly 1"
        self.instances: list[dict] = []
        self.mates: list[dict] = []
        self.groups: list[dict] = []             # {"id", "name", "instances": [ids]}; an instance is in at most one
        # Relations: {"id", "type", "name", "a": {"mate", "dof"}, "b": {"mate", "dof"}, "ratio", "reverse",
        # "phase", "suppressed"}; value_b = sign x ratio x value_a + phase (ADR 0005)
        self.relations: list[dict] = []
        # explicit mate connectors at an instance's origin: {id, inst, name, kind: "origin"}; additive in .fasm
        self.connectors: list[dict] = []
        # held blobs, {sha256 key: bytes}: STEP files and saved part copies read or made since the load, written
        # by the next save (write_blobs); the file names only the keys (I4.9)
        self.blobs: dict[str, bytes] = {}
        self.blob_view = None                    # this tab's blobs in a Document store (docstore.BlobView), if any
        self._breps: dict[int, tuple] = {}       # id(shape) -> (shape, key, bytes) of a part copy; memory only
        self._copy_geometry: dict = {}           # sha256 of a saved part copy -> its blobstore.geometry_key
        self.path = None
        self.dirty = False
        self.listeners = []
        self.resolver = None                     # callable(source) -> features.Part (set by the main window)
        # callable(source) -> naming.NamedShape or None: the element map of the part a source links to.
        # Left None, it is found on the resolver when that is a LinkResolver method (links.py).
        self.named_resolver = None
        # callable(source) -> [{"id", "name", "frame"}] or None: the mate connectors the Part Studio publishes for
        # the part a source links to (links.resolve_connectors). Left None, it is found on the resolver likewise.
        self.connector_resolver = None
        self.studio_resolver = None      # callable(src) -> links.StudioPackage; default: the resolver's resolve_studio
        self._recovered = set()                  # (mate id, "a"/"b") found by signature, not by name; in memory only
        self.parts: dict[str, object] = {}       # instance id -> Part in its own coordinates
        self._fallback: dict[str, object] = {}   # instance id -> shape stored in the file
        self._step_cache: dict[str, list] = {}
        self._standard_cache: dict[tuple, object] = {}   # (type, plugin version, params) -> shape; memory only
        self.errors: dict[str, str] = {}         # instance / mate id -> message
        self._conflicts: set = set()             # mates and relations left out because they can't hold with the others
        self._rel_filter = None                  # relation ids allowed a residual inside a solve; None = all
        self._limit_failed: set = set()          # mates whose limits the last enforcement could not meet
        self._limits_active = False              # the last static solve ended with a limit held
        self._limit_holds: dict = {}             # mate id -> {dof: bound}; active limits, set only inside a solve
        self.dof = 0
        self._undo: list[str] = []
        self._animate = None                     # snapshot taken by begin_animate, in memory only
        self._redo: list[str] = []
        self._counter = 0
        # Part-Studio-shaped attributes some generic window code looks at
        from .features import ModelState
        self.features = []
        self.hidden_parts = set()
        self.hidden_features = set()
        self.state = ModelState()

    # ------------------------------------------------------------------ ids
    def new_id(self, prefix):
        self._counter += 1
        used = {i["id"] for i in self.instances} | {m["id"] for m in self.mates} | {g["id"] for g in self.groups}
        used |= {r["id"] for r in self.relations} | {c["id"] for c in self.connectors}
        while f"{prefix}{self._counter}" in used:
            self._counter += 1
        return f"{prefix}{self._counter}"

    def instance(self, iid):
        return next((i for i in self.instances if i["id"] == iid), None)

    def mate(self, mid):
        return next((m for m in self.mates if m["id"] == mid), None)

    def is_empty(self):
        return not self.instances

    # ------------------------------------------------------------------ undo / redo
    def _snapshot(self):
        return json.dumps({"instances": self.instances, "mates": self.mates, "name": self.name,
                           "groups": self.groups, "relations": self.relations, "connectors": self.connectors})

    def _restore(self, snap):
        d = json.loads(snap)
        self.instances, self.mates = d["instances"], d["mates"]
        self.groups = d.get("groups", [])
        self.relations = d.get("relations", [])
        self.connectors = d.get("connectors", [])

    def checkpoint(self):
        self._undo.append(self._snapshot())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())
            self.dirty = True
            self.refresh(solve=False)

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self.dirty = True
            self.refresh(solve=False)

    def notify(self):
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ parts
    def _blob_get(self, key):
        """A blob of this Assembly: held in memory, else read from its store (blobstore.read_held). Raises
        blobstore.BlobError naming the key."""
        from . import blobstore
        return blobstore.read_held(self.blobs, key, self.blob_view, self.path)

    def _step_parts(self, src):
        from . import blobstore
        key = src["blob"]
        if key not in self._step_cache:
            import tempfile
            try:
                data = self._blob_get(key)
            except blobstore.BlobError as e:
                raise K.KernelError(f"The imported STEP file {src.get('filename') or ''} is missing: {e}") from e
            fd, path = tempfile.mkstemp(suffix=".step")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            try:
                self._step_cache[key] = K.import_step_with_colors(path)
            finally:
                os.remove(path)
        return self._step_cache[key]

    @staticmethod
    def _standard_label(src):
        return f'Standard part {src.get("type")} (plugin "{src.get("plugin")}")'

    def _standard_part(self, inst):
        """Build (or fetch from the cache) the Part of a Standard part instance. Raises KernelError naming the
        Plugin id and type when the type is not registered or validate / build fails."""
        import json as _json
        from .features import Part, DEFAULT_COLOR
        from .pyfeature import registry
        src = inst["source"]
        label = self._standard_label(src)
        typ = src.get("type")
        cls = registry.standard_part(typ)
        if cls is None:
            raise K.KernelError(f"{label} is not installed")
        pid = typ.split("/")[0][len(registry.STANDARD_PREFIX):]
        info = registry.plugin(pid)
        try:
            part = cls()
            params = part.validate(dict(src.get("params") or {}))
            key = (typ, info.version if info else "", _json.dumps(params, sort_keys=True, default=str))
            if key not in self._standard_cache:
                self._standard_cache[key] = part.build(params)
            return Part(inst["id"], part.designation(params), self._standard_cache[key], DEFAULT_COLOR, None)
        except Exception as e:
            raise K.KernelError(f"{label} failed: {e}")

    def _resolve(self, inst):
        from .features import Part
        src = inst["source"]
        if src.get("kind") == "standard":
            return self._standard_part(inst)
        if src.get("kind") == "step":
            sols = self._step_parts(src)
            i = int(src.get("index", 0))
            if i >= len(sols):
                raise K.KernelError("The part is no longer in the STEP file")
            sol, nm, col, fcols = sols[i]
            from .features import DEFAULT_COLOR
            base = os.path.splitext(src.get("filename") or "Part")[0]
            return Part(inst["id"], nm or base, sol, col or DEFAULT_COLOR, fcols or None)
        if self.resolver is None:
            raise K.KernelError("Part Studio not open")
        return self.resolver(src)

    def _named(self, iid):
        """The element map of instance `iid`'s part, or None when it has none (a STEP part, a saved copy)."""
        inst, part = self.instance(iid), self.parts.get(iid)
        if inst is None or part is None or inst["source"].get("kind") in ("step", "standard") \
                or is_sketch_source(inst["source"]):
            return None
        fn = self.named_resolver or getattr(getattr(self.resolver, "__self__", None), "resolve_named", None)
        if fn is None:
            return None
        try:
            nm = fn(inst["source"])
        except Exception:
            return None
        return nm if nm is not None and nm.shape is not None and nm.shape.IsSame(part.shape) else None

    def is_sketch_instance(self, iid):
        """True when instance `iid` is a Sketch instance (its source is a studio sketch), not a part instance."""
        inst = self.instance(iid)
        return inst is not None and is_sketch_source(inst["source"])

    def sketch_out(self, iid):
        """The published engine.contract.SketchOut (plane, solved sketch, hash) of Sketch instance `iid`, read
        through the resolver's resolve_studio, or None for any other instance, a missing instance, or a studio
        or sketch that can't be read now. Coordinates are the studio's; apply T(iid) for the world."""
        if not self.is_sketch_instance(iid):
            return None
        src = self.instance(iid)["source"]
        fn = self.studio_resolver or getattr(getattr(self.resolver, "__self__", None), "resolve_studio", None)
        if fn is None:
            return None
        try:
            return fn(src).sketches.get(str(src.get("sketch")))
        except Exception:
            return None

    def published_connectors(self, iid):
        """The mate connectors instance `iid` offers: what its Part Studio publishes plus the explicit origin
        connectors added to it (None when neither exists)."""
        pub = self._studio_connectors(iid)
        mine = [{"id": c["id"], "name": c.get("name", ""), "frame": {"o": [0.0, 0.0, 0.0], "z": [0.0, 0.0, 1.0],
                                                                       "x": [1.0, 0.0, 0.0]}}
                for c in self.connectors if c["inst"] == iid]
        return pub if not mine else (pub or []) + mine

    def add_instance_origin_connector(self, iid, name=None):
        """An explicit mate connector at instance `iid`'s part origin (one per instance). Returns its id."""
        inst = self.instance(iid)
        if inst is None:
            raise K.KernelError("No such instance")
        have = next((c for c in self.connectors if c["inst"] == iid), None)
        if have is not None:
            return have["id"]
        self.checkpoint()
        c = {"id": self.new_id("C"), "inst": iid, "name": name or f"{inst['name']} origin", "kind": "origin"}
        self.connectors.append(c)
        self.notify()
        return c["id"]

    def _studio_connectors(self, iid):
        """[{"id", "name", "frame"}] the Part Studio publishes for instance `iid`'s part (frames in the part's
        coordinates, plain lists), or None when it can't be asked (a STEP part, the studio is closed)."""
        inst = self.instance(iid)
        if inst is None or inst["source"].get("kind") in ("step", "standard"):
            return None     # a Standard part's own connectors are named (`connector_by_name`), not published
        fn = self.connector_resolver or getattr(getattr(self.resolver, "__self__", None), "resolve_connectors", None)
        if fn is None:
            return None
        try:
            return fn(inst["source"])
        except Exception:
            return None

    def _published_named_connector(self, iid, cid):
        """A mate end on the part's own published connector `cid`: {"inst", "named": cid, ...}. Its frame is
        re-read from the Part Studio on every refresh. (Explicit origin connectors go through here too.)"""
        for c in self.published_connectors(iid) or []:
            if str(c["id"]) == str(cid):
                fr = frame_to_json({**c["frame"], "what": f"connector {c['name']}" if c.get("name") else "connector"})
                kind = "origin" if any(x["id"] == str(cid) and x["inst"] == iid for x in self.connectors) else "connector"
                return {"inst": iid, "kind": kind, "index": None, "click": None, "ref": None,
                        "named": str(cid), "name": c.get("name", ""), "frame": fr}
        raise K.KernelError(f"The part has no published mate connector {cid!r}")

    def sketch_end(self, iid, eid, role):
        """A mate end on sketch geometry of Sketch instance `iid` (ADR 0011):
        {"inst": iid, "kind": "sketch", "entity": eid, "role": role, "etype": <point|line|circle|arc>,
         "sig": <geometric signature>, "frame": <frame_to_json dict, in the instance's own coordinates>}.
        `eid` is a sketch entity id or a bare point id; `role` is one of sketchref.ROLES the entity has. The
        frame is re-read from the published sketch on every refresh. Raises K.KernelError when `iid` is not a
        Sketch instance, its sketch can't be read, or the entity or role doesn't exist."""
        out = self.sketch_out(iid)
        if out is None:
            raise K.KernelError("Not a Sketch instance, or its sketch can't be read")
        plane, sk = self._sketch_data(out)
        d = sketchref.describe(sk, eid, role)
        fr = sketchref.frame(plane, sk, eid, role)
        return {"inst": iid, "kind": "sketch", "entity": eid, "role": role, "etype": d["etype"], "sig": d["sig"],
                "frame": frame_to_json({**fr, "what": f"sketch {eid} {role}"})}

    @staticmethod
    def _sketch_data(out):
        pl = out.plane
        plane = {"origin": list(pl["origin"]), "xdir": list(pl["xdir"]), "normal": list(pl["normal"])} \
            if isinstance(pl, dict) else {"origin": list(pl.origin), "xdir": list(pl.xdir), "normal": list(pl.normal)}
        return plane, out.sketch

    def _refresh_sketch_end(self, m, c, cache):
        """Recompute sketch end `c` of mate `m` from the current published sketch; a missing or retyped entity
        gives the mate a named error (it stays in doc.mates and is left out of the solve)."""
        iid = c["inst"]
        if iid not in cache:
            cache[iid] = self.sketch_out(iid)
        out = cache[iid]
        if out is None:
            return                              # sketch not readable: keep the last frame, as for a closed studio
        plane, sk = self._sketch_data(out)
        try:
            sketchref.check(sk, c)
            fr = sketchref.frame(plane, sk, c["entity"], c["role"])
        except K.KernelError as e:
            self.errors[m["id"]] = f"Lost its sketch geometry: {e}"
            return
        c["frame"] = frame_to_json({**fr, "what": f"sketch {c['entity']} {c['role']}"})

    def _refresh_named_ends(self):
        """Re-read the frame of every named mate end; a connector the studio no longer publishes turns its mate red.
        Sketch ends (kind "sketch") are recomputed from the published sketch the same way."""
        cache = {}
        sk_cache = {}
        for m in self.mates:
            for side in ("a", "b"):
                c = m.get(side)
                if c and c.get("kind") == "sketch" and c["inst"] in self.parts:
                    self._refresh_sketch_end(m, c, sk_cache)
                    continue
                if not c or not c.get("named") or c["inst"] not in self.parts:
                    continue
                if c.get("kind") == "origin":      # an explicit connector: kept in this file, not in the studio
                    own = next((x for x in self.connectors if x["id"] == c["named"] and x["inst"] == c["inst"]), None)
                    if own is None:
                        self.errors[m["id"]] = f"Lost its mate connector “{c.get('name') or c['named']}”: it was removed"
                    else:
                        c["name"] = own.get("name", "")
                        c["frame"] = frame_to_json({"o": [0.0, 0.0, 0.0], "z": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0],
                                                    "what": f"connector {c['name']}"})
                    continue
                if c["inst"] not in cache:
                    cache[c["inst"]] = self._studio_connectors(c["inst"])
                pub = cache[c["inst"]]
                if pub is None:
                    continue                    # studio not reachable: keep the last frame, as for a closed studio
                hit = next((p for p in pub if str(p["id"]) == c["named"]), None)
                if hit is None:
                    self.errors[m["id"]] = (f"Lost its mate connector “{c.get('name') or c['named']}”: "
                                            "the Part Studio no longer publishes it")
                    continue
                c["name"] = hit.get("name", c.get("name", ""))
                c["frame"] = frame_to_json({**hit["frame"], "what": f"connector {c['name']}" if c["name"] else "connector"})

    def recovered_connectors(self):
        """[(mate id, "a"/"b")] of the connectors the last refresh found by signature instead of by name: legacy
        references (made before elements were named) and named ones on a part that has no element map. Their
        result is a best guess; re-pick the connector to name it."""
        live = {(m["id"], side) for m in self.mates for side in ("a", "b")}
        self._recovered &= live
        return sorted(self._recovered)

    def refresh(self, solve=True, notify=True):
        """Re-read every instance's part (picking up Part Studio edits), update mates, re-solve."""
        from .features import Part
        self.errors = {}
        changed = set()
        for inst in self.instances:
            old = self.parts.get(inst["id"])
            try:
                p = self._resolve(inst)
            except Exception as e:
                if inst["source"].get("kind") == "standard":
                    # a Plugin was removed or its part now fails: keep the last shape if there is one
                    if old is not None:
                        self.parts[inst["id"]] = old
                        self.errors[inst["id"]] = f"{e} — showing the last version of the part"
                    else:
                        self.errors[inst["id"]] = str(e)
                        self.parts.pop(inst["id"], None)
                    continue
                if old is not None and inst["source"].get("kind") != "step":
                    # the Part Studio was closed or the part removed: keep showing the last version
                    self.parts[inst["id"]] = old
                    self.errors[inst["id"]] = (
                        "Linked Part Studio not found — showing the copy saved in this assembly"
                        if inst["id"] in self._fallback else f"{e} — showing the last version of the part")
                    continue
                shp, lost = self._fallback.get(inst["id"]), ""
                if shp is None and inst.get("_brep_blob"):
                    try:
                        shp = K.shape_from_brep(self._blob_get(inst["_brep_blob"]))
                        self._fallback[inst["id"]] = shp
                    except Exception as why:
                        shp, lost = None, f"; its saved copy is unreadable: {why}"
                if shp is None:
                    self.errors[inst["id"]] = f"Part not found: {e}{lost}"
                    self.parts.pop(inst["id"], None)
                    continue
                p = Part(inst["id"], inst.get("part_name") or inst["name"], shp,
                         tuple(inst.get("color") or INSTANCE_COLORS[0]), None)
                p.material = inst.get("material")           # the saved copy keeps the material it was saved with
                self.errors[inst["id"]] = "Linked Part Studio not found — showing the copy saved in this assembly"
            if old is None or not old.shape.IsSame(p.shape):
                changed.add(inst["id"])
            self.parts[inst["id"]] = p
            inst["part_name"] = p.name
            inst["material"] = getattr(p, "material", None)     # a Part Studio override; STEP and standard parts: none
        for iid in list(self.parts):
            if self.instance(iid) is None:
                self.parts.pop(iid)
        # re-find mate connectors on parts whose geometry changed
        for m in self.mates:
            for side in ("a", "b"):
                c = m[side]
                if c.get("kind") == "named":
                    # a named connector has no ref: its frame comes from its part's own list of connectors
                    if c["inst"] in changed and c["inst"] in self.parts:
                        try:
                            c["frame"] = self.connector_by_name(c["inst"], c["name"])
                        except K.KernelError as e:
                            self.errors[m["id"]] = f"Lost its connector after the part changed: {e}"
                    continue
                if c["inst"] in changed and c.get("ref") and c["inst"] in self.parts:
                    shp = self.parts[c["inst"]].shape
                    named = self._named(c["inst"])
                    self._recovered.discard((m["id"], side))
                    try:
                        ok_idx, rec = resolve_element(shp, c["ref"], c["kind"], named)
                    except K.KernelError as e:
                        self.errors[m["id"]] = f"Lost its connector after the part changed: {e}"
                        continue
                    if rec:
                        self._recovered.add((m["id"], side))
                    try:
                        if c.get("snap"):
                            fr = snap_frame(shp, c["kind"], ok_idx, c["snap"], named)
                        else:
                            fr = connector_frame(shp, c["kind"], ok_idx, c.get("click"))
                        if c.get("x_ref") is not None:
                            fr = apply_x_ref(fr, c["x_ref"])
                        else:
                            # a connector from an older file: X by the longest-edge rule this once, then kept
                            c["x_ref"] = connector_x_ref(fr)
                        c["frame"] = frame_to_json(fr)
                        c["index"] = ok_idx
                    except Exception:
                        pass
        self._refresh_named_ends()
        if solve:
            self.solve()
        else:
            self._update_status()
        if notify:
            self.notify()

    # ------------------------------------------------------------------ geometry access
    def T(self, iid):
        inst = self.instance(iid)
        return np.array(inst["T"], float) if inst else np.eye(4)

    def world_shape(self, iid):
        p = self.parts.get(iid)
        return None if p is None else K.located(p.shape, self.T(iid))

    def visible_instances(self):
        return [i for i in self.instances if not i.get("hidden") and not i.get("suppressed") and i["id"] in self.parts]

    def _source_key(self, src):
        if src.get("kind") == "step":
            return ("step", src.get("blob"), src.get("index", 0))
        if is_sketch_source(src):
            return ("sketch", src.get("tab") or src.get("uid"), src.get("sketch"))
        if src.get("kind") == "standard":
            return ("standard", src.get("type"), json.dumps(src.get("params") or {}, sort_keys=True, default=str))
        return ("studio", src.get("tab") or src.get("uid"), src.get("part"))

    def instance_color(self, inst):
        """The instance's own colour, else its part's — except plain default-grey parts, which get a colour
        each (copies of the same part share one) so the assembly is easy to read."""
        from .features import DEFAULT_COLOR
        p = self.parts.get(inst["id"])
        if inst.get("color"):
            return tuple(inst["color"])
        if p is None:
            return INSTANCE_COLORS[0]
        if max(abs(a - b) for a, b in zip(p.color, DEFAULT_COLOR)) > 1e-3:
            return p.color
        keys = []
        for i in self.instances:
            q = self.parts.get(i["id"])
            if q is not None and not i.get("color") and \
                    max(abs(a - b) for a, b in zip(q.color, DEFAULT_COLOR)) <= 1e-3:
                k = self._source_key(i["source"])
                if k not in keys:
                    keys.append(k)
        k = self._source_key(inst["source"])
        return INSTANCE_COLORS[keys.index(k) % len(INSTANCE_COLORS)] if k in keys else INSTANCE_COLORS[0]

    def bounds(self):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for inst in self.instances:
            s = self.world_shape(inst["id"])
            if s is not None:
                a, b = K.bbox(s)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        if not np.all(np.isfinite(lo)):
            return np.zeros(3), np.zeros(3)
        return lo, hi

    def length_scale(self):
        lo, hi = self.bounds()
        return max(float(np.linalg.norm(hi - lo)) / 4, 5.0)

    # ------------------------------------------------------------------ editing
    def _unique_name(self, base):
        names = {i["name"] for i in self.instances}
        n = 1
        while f"{base} <{n}>" in names:
            n += 1
        return f"{base} <{n}>"

    def add_step_blob(self, data_b64):
        """Hold an imported STEP file (base64 text, or its bytes) until the next save writes it to the blobs;
        returns its key for a `step` source's `blob`."""
        from . import blobstore
        raw = bytes(data_b64) if isinstance(data_b64, (bytes, bytearray)) else base64.b64decode(data_b64)
        key = blobstore.key_of(raw)
        self.blobs[key] = raw
        return key

    def insert(self, source, T=None, name=None, checkpoint=True):
        """Add an instance. Returns its id. Without a placement, it goes beside what's already there."""
        if checkpoint:
            self.checkpoint()
        inst = {"id": self.new_id("I"), "name": "", "source": copy.deepcopy(source), "T": np.eye(4).tolist(),
                "fixed": False, "hidden": False}
        self.instances.append(inst)
        try:
            p = self._resolve(inst)
        except Exception as e:
            self.instances.remove(inst)
            raise K.KernelError(f"Couldn't insert the part: {e}")
        self.parts[inst["id"]] = p
        inst["part_name"] = p.name
        inst["material"] = getattr(p, "material", None)
        inst["name"] = self._unique_name(name or p.name)
        if len(self.instances) == 1:
            inst["fixed"] = True                    # like Onshape: the first instance is fixed in place
        elif T is None:
            others = [i for i in self.instances if i is not inst and i["id"] in self.parts]
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            for o in others:
                a, b = K.bbox(self.world_shape(o["id"]))
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
            plo, phi = K.bbox(p.shape)
            if np.all(np.isfinite(lo)):
                gap = max(0.15 * float(np.linalg.norm(phi - plo)), 5.0)
                v = np.array([hi[0] + gap - plo[0], (lo[1] + hi[1]) / 2 - (plo[1] + phi[1]) / 2,
                              lo[2] - plo[2]])
                T = translation(v)
        if T is not None:
            inst["T"] = np.asarray(T, float).tolist()
        self._update_status()
        self.notify()
        return inst["id"]

    def insert_standard_part(self, type_id, params, T=None):
        """Add an instance of a Standard part. plugin and plugin_version come from the registry; the stored
        params are the cleaned ones from the part's validate(). Returns the instance id."""
        from .pyfeature import registry
        cls = registry.standard_part(type_id)
        if cls is None:
            raise K.KernelError(f'Standard part {type_id} is not installed')
        pid = type_id.split("/")[0][len(registry.STANDARD_PREFIX):]
        info = registry.plugin(pid)
        try:
            clean = cls().validate(dict(params or {}))
        except Exception as e:
            raise K.KernelError(f'Standard part {type_id} (plugin "{pid}"): {e}')
        src = {"kind": "standard", "type": type_id, "plugin": pid,
               "plugin_version": info.version if info else "", "params": copy.deepcopy(clean)}
        return self.insert(src, T)

    def standard_notices(self):
        """{instance id: {"stored", "installed", "text"}} for each Standard part instance whose stored
        plugin_version differs from the installed Plugin's. The instance still builds with the installed code;
        the notice is not an error. Same wording as pyfeature registry.notices: "made with fasteners 0.9.0,
        rebuilt with 1.0.0". An instance whose type or Plugin is not installed is unresolved (its own error in
        `errors`) and gets no version notice. Computed on demand; the stored version is never changed here."""
        from .pyfeature import registry
        out = {}
        for inst in self.instances:
            src = inst["source"]
            if src.get("kind") != "standard" or registry.standard_part(src.get("type")) is None:
                continue
            pid = src["type"].split("/")[0][len(registry.STANDARD_PREFIX):]
            info = registry.plugin(pid)
            stored = src.get("plugin_version") or ""
            if info is None or stored == info.version:
                continue
            out[inst["id"]] = {"stored": stored, "installed": info.version,
                               "text": f"made with {pid} {stored or '(no version)'}, rebuilt with {info.version}"}
        return out

    def acknowledge_standard_versions(self, iids=None):
        """Write the installed Plugin version into the marked Standard part instances in `iids` (all marked
        ones when None), in one undo checkpoint, which clears their notices. Ids that are not marked, not
        Standard parts or do not exist are ignored. Returns the list of instance ids changed, in document
        order; no checkpoint is made when it is empty."""
        notices = self.standard_notices()
        want = set(notices) if iids is None else set(notices) & set(iids)
        changed = [i["id"] for i in self.instances if i["id"] in want]
        if not changed:
            return []
        self.checkpoint()
        for iid in changed:
            self.instance(iid)["source"]["plugin_version"] = notices[iid]["installed"]
        self.notify()
        return changed

    def delete_instances(self, ids):
        self.checkpoint()
        ids = set(ids)
        self.instances = [i for i in self.instances if i["id"] not in ids]
        self.mates = [m for m in self.mates if m["a"]["inst"] not in ids and m["b"]["inst"] not in ids]
        self.connectors = [c for c in self.connectors if c["inst"] not in ids]
        for i in ids:
            self.parts.pop(i, None)
        for g in self.groups:
            g["instances"] = [i for i in g["instances"] if i not in ids]
        self.groups = [g for g in self.groups if len(g["instances"]) >= 2]   # a group needs two members
        self.solve()
        self.notify()

    def group_of(self, iid):
        """The group an instance belongs to, or None."""
        return next((g for g in self.groups if iid in g["instances"]), None)

    def add_group(self, instance_ids):
        """Make a group of instances: they move as one rigid body in the solver. Returns the group id."""
        ids = list(dict.fromkeys(instance_ids))
        if len(ids) < 2:
            raise ValueError("A group needs at least two instances")
        for iid in ids:
            if self.instance(iid) is None:
                raise ValueError(f"No instance {iid}")
            g = self.group_of(iid)
            if g is not None:
                raise ValueError(f"{self.instance(iid)['name']} is already in {g['name']}")
        self.checkpoint()
        used = {int("".join(c for c in g["name"] if c.isdigit()) or 0) for g in self.groups}
        n = 1
        while n in used:
            n += 1
        gid = self.new_id("G")
        self.groups.append({"id": gid, "name": f"Group {n}", "instances": ids})
        self.solve()
        self.notify()
        return gid

    def delete_group(self, gid):
        """Dissolve a group; its instances stay where they are."""
        if not any(g["id"] == gid for g in self.groups):
            raise ValueError(f"No group {gid}")
        self.checkpoint()
        self.groups = [g for g in self.groups if g["id"] != gid]
        self.solve()
        self.notify()

    def delete_mates(self, ids):
        self.checkpoint()
        self.mates = [m for m in self.mates if m["id"] not in set(ids)]
        for r in self.relations:                  # a relation stays, inert, when one of its mates goes (I2.3/I2.5)
            if r["a"]["mate"] in set(ids) or r["b"]["mate"] in set(ids):
                r["phase"] = None
        self._update_status()
        self.notify()

    def set_fixed(self, iid, value):
        self.checkpoint()
        self.instance(iid)["fixed"] = bool(value)
        self.solve()
        self.notify()

    def set_hidden(self, iid, value):
        self.checkpoint()
        self.instance(iid)["hidden"] = bool(value)
        self.notify()

    def isolate(self, iid):
        """Hide every instance but `iid`. One undo step; undo brings back each instance's earlier state."""
        self.checkpoint()
        for i in self.instances:
            i["hidden"] = i["id"] != iid
        self.notify()

    def show_all(self):
        """Show every instance. One undo step."""
        self.checkpoint()
        for i in self.instances:
            i["hidden"] = False
        self.notify()

    def set_instance_suppressed(self, iid, flag):
        """Leave an instance out (or put it back): it drops out of the solve, the visible instances, the bill of
        materials, interference and export; its mates and relations go inactive. One undo step."""
        inst = self.instance(iid)
        if inst is None:
            raise ValueError(f"No instance {iid!r}")
        self.checkpoint()
        inst["suppressed"] = bool(flag)
        if not flag:
            # mates that come back take their values from the current pose and their relations recapture the
            # phase from it (ADR 0005), before the solve, so nothing moves through a relation
            back = [m for m in self.mates if m.get("a") and m.get("b") and not m.get("suppressed")
                    and iid in (m["a"]["inst"], m["b"]["inst"]) and not self.mate_inactive(m)]
            for m in back:
                m["values"] = {}
            self._update_mate_values(back, fill_only=True)
            ids = {m["id"] for m in back}
            for r in self.relations:
                if not r.get("suppressed") and ids & {r["a"]["mate"], r["b"]["mate"]}:
                    self._capture_phase(r)
        self.solve()
        self.notify()

    def instance_suppressed(self, iid):
        return bool((self.instance(iid) or {}).get("suppressed"))

    def mate_inactive(self, m):
        """True when mate `m` (a dict or an id) is not in use because one of its instances is suppressed."""
        m = self.mate(m) if isinstance(m, str) else m
        return bool(m and m.get("a") and m.get("b")
                    and (self.instance_suppressed(m["a"]["inst"]) or self.instance_suppressed(m["b"]["inst"])))

    def rename(self, obj_id, name):
        self.checkpoint()
        o = self.instance(obj_id) or self.mate(obj_id)
        if o is not None:
            o["name"] = name
        self.notify()

    def set_color(self, iid, color):
        self.checkpoint()
        self.instance(iid)["color"] = list(color) if color is not None else None
        self.notify()

    def set_suppressed(self, mid, value):
        self.checkpoint()
        self.mate(mid)["suppressed"] = bool(value)
        self.solve()
        self.notify()

    def move_instance(self, iid, M_world, checkpoint=True, solve=True):
        """Apply a world-space rigid motion to an instance (a fixed one too — this is the explicit move)."""
        if checkpoint:
            self.checkpoint()
        inst = self.instance(iid)
        inst["T"] = orthonormalize(np.asarray(M_world, float) @ np.array(inst["T"], float)).tolist()
        if solve:
            self.solve(keep=iid)
        self.notify()

    def connector(self, iid, kind, index, click_world=None, snap=None):
        """Make a connector dict for a picked face/edge of an instance (stored in part coordinates).
        `snap` is a candidate from snap_points(); the connector is then built at that point."""
        p = self.parts[iid]
        T = self.T(iid)
        click = None
        if click_world is not None:
            Ti = np.linalg.inv(T)
            click = (Ti[:3, :3] @ np.asarray(click_world, float) + Ti[:3, 3]).tolist()
        named = self._named(iid)
        out = {"inst": iid, "kind": kind, "index": int(index), "click": click,
               "ref": _ref_signature(p.shape, kind, index, named)}
        if snap is not None:
            st = {"type": snap["type"], "p": [float(a) for a in snap["p"]],
                  "edge_ref": _ref_signature(p.shape, "edge", snap["edge"], named) if snap.get("edge") is not None else None}
            out["snap"] = st
            fr = snap_frame(p.shape, kind, index, st, named)
        else:
            fr = connector_frame(p.shape, kind, index, click)
        out["frame"] = frame_to_json(fr)
        out["x_ref"] = connector_x_ref(fr)
        return out

    def snap_points(self, iid, kind, index):
        """Hover snap candidates for a face/edge of an instance, each with its world position under "pw"."""
        p = self.parts.get(iid)
        if p is None:
            return []
        T = self.T(iid)
        out = []
        for c in snap_candidates(p.shape, kind, index):
            d = dict(c)
            d["pw"] = T[:3, :3] @ c["p"] + T[:3, 3]
            out.append(d)
        return out

    def world_frame(self, c):
        return transform_frame(self.T(c["inst"]), frame_from_json(c["frame"]))

    def connector_by_name(self, iid, name):
        """THE lookup of a named connector: the frame (JSON form, in the part's own coordinates) that instance
        `iid`'s part offers under `name`. Raises KernelError when the instance or the name is not there.
        Only Standard parts offer connectors by name (their `connectors(params)`); `named_connector` and the
        re-find block in `refresh` come here for them. A Part Studio's published mate connectors are found by id
        instead, through `published_connectors` and `_refresh_named_ends`."""
        from .pyfeature import registry
        inst = self.instance(iid)
        if inst is None:
            raise K.KernelError("The instance no longer exists")
        src = inst["source"]
        if src.get("kind") != "standard":
            raise K.KernelError(f'This part has no connector named "{name}"')
        label = self._standard_label(src)
        cls = registry.standard_part(src.get("type"))
        if cls is None:
            raise K.KernelError(f"{label} is not installed")
        try:
            frames = cls().connectors(dict(src.get("params") or {}))
        except Exception as e:
            raise K.KernelError(f"{label} failed: {e}")
        if name not in frames:
            raise K.KernelError(f'{label} has no connector named "{name}"')
        fr = dict(frames[name])
        fr.setdefault("what", name)
        return frame_to_json(frame_from_json(fr))

    def named_connector(self, iid, name):
        """A mate end on a part's own connector `name`. Two kinds of part offer them:
        - a Standard part's named connector, e.g. a screw's "under_head": {"inst", "kind": "named", "name",
          "frame"}, re-derived from the Plugin on change (`connector_by_name`);
        - a studio part's published mate connector, or an explicit origin connector, looked up by its id
          (`_published_named_connector`: kind "connector"/"origin", "named": id).
        An id of an explicit origin connector on the instance wins, so a Standard part can carry one too.
        No index, click, ref or snap. Raises KernelError when the part offers no such name."""
        inst = self.instance(iid)
        if inst is not None and inst["source"].get("kind") == "standard" and not any(
                c["id"] == str(name) and c["inst"] == iid for c in self.connectors):
            return {"inst": iid, "kind": "named", "name": name, "frame": self.connector_by_name(iid, name)}
        return self._published_named_connector(iid, name)

    def set_standard_params(self, iids, changes):
        """Apply the parameter `changes` (a dict) to each Standard part instance in `iids`, as ONE undo step.
        Each instance is checked on its own (its type's validate, and a build): one that rejects the change
        is left exactly as it was. Then the parts are rebuilt, named connector frames re-derived and the mates
        solved. Returns {"changed": [iid, ...], "rejected": {iid: message}}. An instance whose cleaned
        params equal its current ones is in neither. With nothing to change, no undo step is made."""
        from .pyfeature import registry
        todo, rejected = [], {}
        for iid in dict.fromkeys(iids):
            inst = self.instance(iid)
            if inst is None:
                rejected[iid] = "No such instance"
                continue
            src = inst["source"]
            if src.get("kind") != "standard":
                rejected[iid] = "Not a Standard part"
                continue
            cls = registry.standard_part(src.get("type"))
            if cls is None:
                rejected[iid] = f'{self._standard_label(src)} is not installed'
                continue
            try:
                clean = cls().validate({**(src.get("params") or {}), **changes})
                self._standard_part({"id": iid, "source": {**src, "params": clean}})    # builds into the cache
            except Exception as e:
                rejected[iid] = str(e)
                continue
            if clean != src.get("params"):
                todo.append((inst, clean))
        if todo:
            self.checkpoint()
            for inst, clean in todo:
                inst["source"]["params"] = copy.deepcopy(clean)
            self.refresh()
        return {"changed": [i["id"] for i, _ in todo], "rejected": rejected}

    def make_mate(self, mtype):
        n = 1
        label = MATE_TYPES[mtype][0]
        names = {m["name"] for m in self.mates}
        while f"{label} {n}" in names:
            n += 1
        return {"id": self.new_id("M"), "type": mtype, "name": f"{label} {n}", "a": None, "b": None,
                "flip": False, "offset": 0.0, "angle": 0.0, "suppressed": False, "values": {}}

    def _update_mate_values(self, mates, fill_only=False):
        """Measure each mate's Mate values from the current pose and store them. A rotation is unwrapped to
        the turn nearest its stored value, so turns past 180 degrees are counted; a missing value (or one
        being filled for the first time) is the wrapped measure. fill_only keeps mates that already have values."""
        for m in mates:
            if not m.get("a") or not m.get("b") or (fill_only and m.get("values")):
                continue
            if m["type"] not in MATE_VALUE_DOFS:
                continue
            try:
                new = mate_values(m, self.world_frame(m["a"]), self.world_frame(m["b"]))
            except Exception:
                continue
            old = m.get("values") or {}
            m["values"] = {n: (float(old[n]) + _wrap_deg(v - float(old[n])) if n.startswith("r") and n in old else v)
                           for n, v in new.items()}

    def set_limits(self, mid, dof, lo, hi):
        """Limit one freedom of a mate to [lo, hi] (either end None for no bound; both None removes the limit).
        Values are unwrapped Mate values, so a rotation limit may span more than one turn."""
        m = self.mate(mid)
        if m is None:
            raise ValueError(f"No mate {mid}")
        names = MATE_VALUE_DOFS.get(m["type"], ())
        if not names:
            raise ValueError(f"A {m['type']} mate has no freedoms to limit")
        if dof not in names:
            raise ValueError(f"A {m['type']} mate has no freedom {dof!r}; it has {', '.join(names)}")
        lo = None if lo is None else float(lo)
        hi = None if hi is None else float(hi)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"Limit minimum {lo} is above maximum {hi}")
        self.checkpoint()
        limits = dict(m.get("limits") or {})
        if lo is None and hi is None:
            limits.pop(dof, None)
        else:
            limits[dof] = [lo, hi]
        if limits:
            m["limits"] = limits
        else:
            m.pop("limits", None)
        self.solve()
        self.notify()

    def _hold_residuals(self, m, FA, FB, L):
        """Residuals holding mate m's active limits (self._limit_holds) at their bounds. A rotation is measured
        unwrapped against the stored value, as _update_mate_values does, and counted in mm of arc."""
        holds = self._limit_holds.get(m["id"])
        if not holds:
            return np.zeros(0)
        new = mate_values(m, FA, FB)
        stored = m.get("values") or {}
        out = []
        for dof, bound in holds.items():
            v = new[dof]
            if dof.startswith("r"):
                if dof in stored:
                    v = float(stored[dof]) + _wrap_deg(v - float(stored[dof]))
                out.append(np.radians(v - bound) * L)
            else:
                out.append(v - bound)
        return np.array(out, float)

    @staticmethod
    def _limit_violations(mates, holds):
        """{mate id: {dof: nearer bound}} for each limited freedom outside its range that is not already held.
        Compares the stored (unwrapped) Mate values, so call it after _update_mate_values."""
        out = {}
        for m in mates:
            vals = m.get("values") or {}
            for dof, (lo, hi) in (m.get("limits") or {}).items():
                if dof not in vals or dof in holds.get(m["id"], {}):
                    continue
                v = float(vals[dof])
                if lo is not None and v < float(lo) - 1e-9:
                    out.setdefault(m["id"], {})[dof] = float(lo)
                elif hi is not None and v > float(hi) + 1e-9:
                    out.setdefault(m["id"], {})[dof] = float(hi)
        return out

    def _enforce_limits(self, mates, iters=40, keep=None, drag=None):
        """Active-set limit enforcement after a solve that has already run over `mates` and stored their values.
        Each round holds every newly violated freedom at its nearer bound and solves again, for at most as many
        rounds as there are limited freedoms. With no limits on `mates` it does nothing at all. A round whose solve
        leaves a mate unsatisfied is undone and enforcement stops; the mates whose limits were in that round go
        into self._limit_failed for solve() to report as conflicts. While drive_mate holds a step value, the
        rounds solve without it, so an active limit stops the drive. Returns the holds that ended active,
        {mate id: {dof: bound}}."""
        self._limit_failed = set()
        limited = sum(len(m.get("limits") or {}) for m in mates)
        if not limited:
            return {}
        holds: dict = {}
        drive = self._drive
        try:
            for _round in range(limited):
                new = self._limit_violations(mates, holds)
                if not new:
                    break
                trial = {k: dict(v) for k, v in holds.items()}
                for mid, d in new.items():
                    trial.setdefault(mid, {}).update(d)
                before = {i["id"]: list(i["T"]) for i in self.instances}
                self._limit_holds = trial
                self._drive = None
                bad = self._solve_core(mates, drag, iters, keep)
                self._drive = drive
                if bad:
                    for i in self.instances:
                        i["T"] = before[i["id"]]
                    self._limit_failed = set(new)
                    break
                holds = trial
                self._limit_holds = {}
                self._update_mate_values(mates)
        finally:
            self._limit_holds = {}
            self._drive = drive
        return holds

    def add_mate(self, m, checkpoint=True):
        if checkpoint:
            self.checkpoint()
        m["values"] = {}
        self.mates.append(m)
        self.place_for_mate(m)
        self.solve()
        self.notify()

    def replace_mate(self, m):
        self.checkpoint()
        m["values"] = {}          # editing a mate resets its values to the measured pose (ADR 0005)
        for i, old in enumerate(self.mates):
            if old["id"] == m["id"]:
                self.mates[i] = m
        touched = [r for r in self.relations if m["id"] in (r["a"]["mate"], r["b"]["mate"])]
        for r in touched:
            r["phase"] = None     # ...and recaptures the phase of its relations, after the solve
        self.place_for_mate(m)
        self.solve()
        for r in touched:
            self._capture_phase(r)
        self.notify()

    # ------------------------------------------------------------------ driving a mate to a value
    _drive = None        # (mate id, freedom, value) while drive_mate holds one freedom at a step value

    def _drive_residual(self, m, FA, FB, L):
        """The hook drive_mate uses: one extra residual holding a freedom of mate m at the step value
        (rotations in mm of arc at the part's size, like the mate residuals). Empty when nothing is driven."""
        if self._drive is None or self._drive[0] != m["id"]:
            return np.zeros(0)
        _mid, dof, value = self._drive
        d = mate_values(m, FA, FB)[dof] - value
        if dof.startswith("r"):
            return np.array([np.radians(_wrap_deg(d)) * L])
        return np.array([d])

    def _drive_steps(self, m, dof, target):
        """Move freedom `dof` of mate m towards `target` in steps of at most 30 degrees or 0.25 x length_scale()
        mm, each solved with the freedom held at the step value. Stops at the last step that held, with the
        poses and values of that step. Returns (reached value, ok). No checkpoint, no notify: callers add
        their own (Set value, limits, Animate)."""
        if not m.get("values"):
            self._update_mate_values([m], fill_only=True)
        cur = float((m.get("values") or {}).get(dof, 0.0))
        step = 30.0 if dof.startswith("r") else 0.25 * self.length_scale()
        n = max(1, int(np.ceil(abs(target - cur) / step - 1e-9))) if target != cur else 0
        ok, at_limit = True, False
        try:
            for k in range(1, n + 1):
                want = cur + (target - cur) * k / n
                poses = {i["id"]: list(i["T"]) for i in self.instances}
                held = dict(m["values"])
                self._drive = (m["id"], dof, want)
                solved = self.solve()
                got = float(m["values"].get(dof, want))
                # a relation left out of the solve doesn't stop the drive: the others still hold
                rel_only = bool(self._conflicts) and all(self.relation(c) is not None for c in self._conflicts)
                if (solved or rel_only) and m["id"] not in self._conflicts and abs(got - want) < 1e-5:
                    continue
                if (solved or rel_only) and m["id"] not in self._conflicts and self._limits_active:
                    ok = False        # a limit holds the motion here: keep this pose, it is the limit position
                    at_limit = True
                    break
                for i in self.instances:
                    i["T"] = poses[i["id"]]
                m["values"] = held
                ok = False
                break
        finally:
            self._drive = None
        if not ok and not at_limit:
            self.solve()          # settle errors and status at the last step that held
        else:
            self._update_status()  # the last step's status counted the drive residual; recount without it
        return float(m["values"].get(dof, cur)), ok

    def drive_mate(self, mid, dof, target):
        """Move freedom `dof` of mate `mid` to `target` (degrees or mm, a turn past 180 counts) while every other
        mate stays enforced. Returns (reached value, ok); ok is False when a step could not hold and the
        motion stopped at the last one that did. One undo checkpoint, one notify."""
        m = next((x for x in self.mates if x["id"] == mid), None)
        if m is None:
            raise ValueError(f"No mate {mid!r}")
        if dof not in MATE_VALUE_DOFS.get(m["type"], ()):
            raise ValueError(f"Mate {mid!r} ({m['type']}) has no freedom {dof!r}")
        if self.mate_inactive(m):                       # a mate on a suppressed instance can't be driven
            return (m.get("values") or {}).get(dof, 0.0), False
        self.checkpoint()
        out = self._drive_steps(m, dof, float(target))
        self.notify()
        return out

    # ------------------------------------------------------------------ animate
    def begin_animate(self):
        """Remember instance transforms and mate values so end_animate can put them back. No undo checkpoint.
        A second call while an animation is open keeps the first snapshot."""
        if getattr(self, "_animate", None) is not None:
            return
        self._animate = {
            "T": {i["id"]: json.dumps(i["T"]) for i in self.instances},
            "values": {m["id"]: json.dumps(m.get("values")) for m in self.mates},
            "errors": dict(self.errors), "conflicts": set(self._conflicts), "dof": self.dof, "dirty": self.dirty,
        }

    def animate_frame(self, mid, dof, value):
        """Pose mate `mid` at `value` through the drive stepping. No checkpoint. Returns the value reached,
        which differs from `value` when a step could not hold. Notifies so views redraw."""
        m = self.mate(mid)
        if m is None:
            raise ValueError(f"No mate {mid!r}")
        if dof not in MATE_VALUE_DOFS.get(m["type"], ()):
            raise ValueError(f"Mate {mid!r} ({m['type']}) has no freedom {dof!r}")
        self.begin_animate()
        reached, _ok = self._drive_steps(m, dof, float(value))
        self.notify()
        return reached

    def end_animate(self):
        """Put back what begin_animate saw, exactly, and notify. Does nothing when no animation is open.
        Relations are not touched, and the undo stack is not either."""
        snap = getattr(self, "_animate", None)
        if snap is None:
            return
        self._animate = None
        for i in self.instances:
            if i["id"] in snap["T"]:
                i["T"] = json.loads(snap["T"][i["id"]])
        for m in self.mates:
            if m["id"] in snap["values"]:
                m["values"] = json.loads(snap["values"][m["id"]])
        self.errors, self._conflicts = snap["errors"], snap["conflicts"]
        self.dof, self.dirty = snap["dof"], snap["dirty"]
        self.notify()

    # ------------------------------------------------------------------ relations (ADR 0005)
    def relation(self, rid):
        return next((r for r in self.relations if r["id"] == rid), None)

    def make_relation(self, rtype):
        """A new, unattached relation of a type in RELATION_TYPES; fill "a" and "b" ({"mate", "dof"}) and add it."""
        label = RELATION_TYPES[rtype]["label"]
        names = {r["name"] for r in self.relations}
        n = 1
        while f"{label} {n}" in names:
            n += 1
        return {"id": self.new_id("R"), "type": rtype, "name": f"{label} {n}", "a": None, "b": None,
                "ratio": 1.0, "reverse": False, "phase": 0.0, "suppressed": False}

    def _check_relation(self, r):
        """Raise ValueError unless r names two existing mate freedoms of the kinds its type accepts."""
        spec = RELATION_TYPES.get(r.get("type"))
        if spec is None:
            raise ValueError(f"Unknown relation type {r.get('type')!r}")
        for side in ("a", "b"):
            ref = r.get(side) or {}
            m = self.mate(ref.get("mate"))
            if m is None:
                raise ValueError(f"Relation side {side} names no mate")
            if ref.get("dof") not in MATE_VALUE_DOFS.get(m["type"], ()):
                raise ValueError(f"Mate {m['name']!r} ({m['type']}) has no freedom {ref.get('dof')!r}")
            if dof_kind(ref["dof"]) != spec[side]:
                raise ValueError(f"A {spec['label']} relation needs a {spec[side]} on side {side}, "
                                 f"not {ref['dof']!r} of mate {m['name']!r}")
        if spec.get("same_mate") and r["a"]["mate"] != r["b"]["mate"]:
            raise ValueError(f"A {spec['label']} relation needs both sides on one mate")
        if (r["a"]["mate"], r["a"]["dof"]) == (r["b"]["mate"], r["b"]["dof"]):
            raise ValueError("A relation needs two different freedoms")
        ratio = float(r.get("ratio", 1.0))
        if not np.isfinite(ratio) or ratio == 0.0:
            raise ValueError("The ratio must be a non-zero number")

    def _capture_phase(self, r):
        """Set r's phase so it holds at the current stored Mate values: nothing moves (ADR 0005). Leaves the
        phase None (the relation inert) while either freedom has no value yet."""
        ma, mb = self.mate(r["a"]["mate"]), self.mate(r["b"]["mate"])
        if ma is None or mb is None:
            r["phase"] = None
            return
        self._update_mate_values([ma, mb], fill_only=True)
        va, vb = (ma.get("values") or {}).get(r["a"]["dof"]), (mb.get("values") or {}).get(r["b"]["dof"])
        if va is None or vb is None:
            r["phase"] = None
            return
        sign = -1.0 if r.get("reverse") else 1.0
        r["phase"] = float(vb) - sign * relation_ratio(r) * float(va)

    def add_relation(self, r, checkpoint=True):
        """Add a relation, capturing its phase from the current values; no instance moves."""
        self._check_relation(r)
        if checkpoint:
            self.checkpoint()
        r["ratio"] = float(r.get("ratio", 1.0))
        self._capture_phase(r)
        self.relations.append(r)
        self.notify()

    def replace_relation(self, r):
        """Replace the relation with r's id (an edit), recapturing the phase; no instance moves."""
        if self.relation(r["id"]) is None:
            raise ValueError(f"No relation {r['id']!r}")
        self._check_relation(r)
        self.checkpoint()
        r["ratio"] = float(r.get("ratio", 1.0))
        self._capture_phase(r)
        self.relations = [r if x["id"] == r["id"] else x for x in self.relations]
        self.notify()

    def set_relation_suppressed(self, rid, value):
        """Suppress or unsuppress a relation. Unsuppressing recaptures the phase, so nothing moves."""
        r = self.relation(rid)
        if r is None:
            raise ValueError(f"No relation {rid!r}")
        self.checkpoint()
        r["suppressed"] = bool(value)
        if not r["suppressed"]:
            self._capture_phase(r)
        self.notify()

    def delete_relation(self, rid):
        if self.relation(rid) is None:
            raise ValueError(f"No relation {rid!r}")
        self.checkpoint()
        self.relations = [r for r in self.relations if r["id"] != rid]
        self.notify()

    def _relation_value(self, m, dof, Ts, frames):
        """Freedom `dof` of mate m measured at trial poses Ts, a rotation unwrapped to the turn nearest
        its stored value (as _update_mate_values does after a solve)."""
        FA = transform_frame(Ts[m["a"]["inst"]], frames[m["id"]][0])
        FB = transform_frame(Ts[m["b"]["inst"]], frames[m["id"]][1])
        v = mate_values(m, FA, FB)[dof]
        old = (m.get("values") or {}).get(dof)
        if dof_kind(dof) == "rotation" and old is not None:
            v = float(old) + _wrap_deg(v - float(old))
        return v

    def _relation_residuals(self, mates, Ts, frames, L):
        """The hook relations use: one residual per active relation whose two mates are both in `mates`,
        value_b - (sign x ratio x value_a + phase) in b's unit, scaled to mm by DOF_KINDS. Empty without relations."""
        if not self.relations:
            return np.zeros(0)
        by_id = {m["id"]: m for m in mates}
        out = []
        for r in self.relations:
            if r.get("suppressed") or r.get("phase") is None or not r.get("a") or not r.get("b"):
                continue
            if self._rel_filter is not None and r["id"] not in self._rel_filter:
                continue
            ma, mb = by_id.get(r["a"]["mate"]), by_id.get(r["b"]["mate"])
            if ma is None or mb is None:
                continue
            da, db = r["a"]["dof"], r["b"]["dof"]
            if da not in MATE_VALUE_DOFS.get(ma["type"], ()) or db not in MATE_VALUE_DOFS.get(mb["type"], ()):
                continue
            sign = -1.0 if r.get("reverse") else 1.0
            va = self._relation_value(ma, da, Ts, frames)
            vb = self._relation_value(mb, db, Ts, frames)
            out.append((vb - (sign * relation_ratio(r) * va + float(r["phase"]))) * DOF_KINDS[dof_kind(db)]["scale"](L))
        return np.array(out, float)

    def _relations_in(self, ids, fn):
        """Run fn() with only the relations in `ids` given a residual."""
        old, self._rel_filter = self._rel_filter, set(ids)
        try:
            return fn()
        finally:
            self._rel_filter = old

    def _relation_left_out(self, r, mates):
        """Error text when relation r can't be used because a mate of it is missing, suppressed or itself in
        conflict (the relation is then inert and the text names that mate); else None."""
        for end in ("a", "b"):
            mid = (r.get(end) or {}).get("mate")
            m = self.mate(mid) if mid else None
            if m is None:
                return f"Left out: its mate {mid or '?'} no longer exists"
            if m.get("suppressed"):
                return f"Left out: its mate {m['name']} is suppressed"
            if self.mate_inactive(m):
                return f"Left out: its mate {m['name']} is on a suppressed instance"
            if m["id"] in self._conflicts or m["id"] in self.errors or all(x["id"] != m["id"] for x in mates):
                return f"Left out: its mate {m['name']} can't be solved or is not in use"
        return None

    # ------------------------------------------------------------------ solving
    def _groups(self, exclude=None):
        """Connected groups of instances through (unsuppressed) mates, ignoring mate `exclude`."""
        parent = {i["id"]: i["id"] for i in self.instances}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for m in self.mates:
            if m is exclude or m.get("suppressed") or not m.get("a") or not m.get("b") or self.mate_inactive(m):
                continue
            a, b = m["a"]["inst"], m["b"]["inst"]
            if a in parent and b in parent:
                parent[find(a)] = find(b)
        for g in self.groups:                       # a group is one body, whatever mates it has
            ids = [i for i in g["instances"] if i in parent]
            for i in ids[1:]:
                parent[find(i)] = find(ids[0])
        groups = {}
        for iid in parent:
            groups.setdefault(find(iid), set()).add(iid)
        return {iid: groups[find(iid)] for iid in parent}

    def place_for_mate(self, m):
        """Before solving, move one side's whole group straight into the mated position. Starting from an
        exactly aligned pose avoids the solver getting stuck on a part that needs flipping over."""
        if not m.get("a") or not m.get("b") or m.get("suppressed") or self.mate_inactive(m):
            return
        a, b = m["a"]["inst"], m["b"]["inst"]
        if a not in self.parts or b not in self.parts or a == b:
            return
        groups = self._groups(exclude=m)
        ga, gb = groups[a], groups[b]
        if ga is gb or ga == gb:
            return                                  # already connected another way: let the solver sort it out
        fixed = {i["id"] for i in self.instances if i.get("fixed")}
        FA, FB = self.world_frame(m["a"]), self.world_frame(m["b"])
        if not (gb & fixed):
            M = snap_transform(m, FA, FB)
            move = gb
        elif not (ga & fixed):
            # move A's group onto B: swap roles (mirror the mate's definition)
            M = np.linalg.inv(snap_transform(m, FA, FB))
            move = ga
        else:
            return
        for iid in move:
            inst = self.instance(iid)
            inst["T"] = orthonormalize(M @ np.array(inst["T"], float)).tolist()

    def _active_mates(self):
        # a mate between two members of one group can't move either of them: leave it out and say why
        for k in [k for k, v in self.errors.items() if v.startswith("Both ends are in ")]:
            self.errors.pop(k)
        for m in self.mates:
            if m.get("a") and m.get("b") and not self.mate_inactive(m):
                g = self.group_of(m["a"]["inst"])
                if g is not None and m["b"]["inst"] in g["instances"]:
                    self.errors[m["id"]] = f"Both ends are in {g['name']}, which moves as one body"
        return [m for m in self.mates if not m.get("suppressed") and m.get("a") and m.get("b")
                and not self.mate_inactive(m) and m["a"]["inst"] in self.parts and m["b"]["inst"] in self.parts
                and m["id"] not in self.errors]

    def _residuals(self, mates, Ts, frames, L):
        out = []
        for m in mates:
            FA = transform_frame(Ts[m["a"]["inst"]], frames[m["id"]][0])
            FB = transform_frame(Ts[m["b"]["inst"]], frames[m["id"]][1])
            out.append(mate_residuals(m, FA, FB, L))
            out.append(self._drive_residual(m, FA, FB, L))
            if self._limit_holds:
                out.append(self._hold_residuals(m, FA, FB, L))
        out.append(self._relation_residuals(mates, Ts, frames, L))
        return np.concatenate(out) if out else np.zeros(0)

    def solve(self, drag=None, iters=40, keep=None):
        """Satisfy the mates by moving free instances as little as possible.
        drag: {"inst", "local" point, "target" world point, "P" 2x3 screen-plane projector} — the dragged part
        follows the target as far as the mates allow. keep: an instance that should not move if avoidable.
        Mates that can't hold together with the earlier ones are left out (and shown red), like Onshape."""
        self._rel_filter = None
        for k in [k for k, v in self.errors.items() if v.startswith("Can't be solved")]:
            self.errors.pop(k)
        for k in [k for k, v in self.errors.items() if v.startswith("Limit can't be met")]:
            self.errors.pop(k)
        mates = self._active_mates()
        if drag is not None and self.instance_suppressed(drag["inst"]):
            return False                                # a suppressed instance can't be dragged
        if drag is not None:
            live = [m for m in mates if m["id"] not in self._conflicts]
            self._rel_filter = {r["id"] for r in self.relations if r["id"] not in self._conflicts}
            try:
                self._solve_core(live, drag, iters, keep)
                self._update_mate_values(live)
                self._enforce_limits(live, iters, keep, drag)      # limit rounds use the same relation set
            finally:
                self._rel_filter = None
            self._update_status()
            return True
        before = {i["id"]: list(i["T"]) for i in self.instances}
        bad = self._solve_core(mates, None, iters, keep)
        self._conflicts = set()
        if bad:
            self._rel_filter = set()        # the mates are re-added without relations; relations follow below
        if bad and len(mates) > 1:
            # find the culprits: add the mates one at a time, keeping each one that still solves
            for i in self.instances:
                i["T"] = before[i["id"]]
            good = []
            for m in mates:
                snap = {i["id"]: list(i["T"]) for i in self.instances}
                if self._solve_core(good + [m], None, iters, keep):
                    for i in self.instances:
                        i["T"] = snap[i["id"]]
                    self._conflicts.add(m["id"])
                else:
                    good.append(m)
        if bad and self.relations:
            # relations: re-add them one at a time on top of the mates that held, keeping each that still solves
            for i in self.instances:
                i["T"] = before[i["id"]]
            held = [m for m in mates if m["id"] not in self._conflicts]
            self._solve_core(held, None, iters, keep)
            rels = [r for r in self.relations if not r.get("suppressed") and r.get("phase") is not None
                    and self._relation_left_out(r, held) is None]
            kept = set()
            for r in rels:
                snap = {i["id"]: list(i["T"]) for i in self.instances}
                self._rel_filter = kept | {r["id"]}
                if self._solve_core(held, None, iters, keep):
                    for i in self.instances:
                        i["T"] = snap[i["id"]]
                    self._conflicts.add(r["id"])
                else:
                    kept.add(r["id"])
            self._rel_filter = kept
            self._solve_core(held, None, iters, keep)
        self._update_mate_values([m for m in mates if m["id"] not in self._conflicts])
        self._limits_active = bool(self._enforce_limits([m for m in mates if m["id"] not in self._conflicts], iters, keep))
        for mid in self._limit_failed:         # a limit that cannot be met with the other mates (I5.2)
            self._conflicts.add(mid)
            self.errors[mid] = "Limit can't be met together with the other mates"
        self._update_status()
        self._rel_filter = None
        return not self._conflicts and not bad

    def _solve_core(self, mates, drag, iters, keep):
        """One least-squares solve over the given mates. Returns the ids of mates left unsatisfied."""
        # a group is one body: its members share one set of six variables, and one fixed member fixes all
        gkey = {iid: g["id"] for g in self.groups for iid in g["instances"]}
        body = lambda iid: gkey.get(iid, iid)
        fixed = {body(i["id"]) for i in self.instances if i.get("fixed") and not i.get("suppressed")}
        involved = set()
        for m in mates:
            involved |= {body(m["a"]["inst"]), body(m["b"]["inst"])}
        if drag is not None:
            involved.add(body(drag["inst"]))
        members: dict = {}
        for i in self.instances:
            if i["id"] in self.parts and not i.get("suppressed"):
                members.setdefault(body(i["id"]), []).append(i["id"])
        free = [k for k in members if k in involved and k not in fixed]
        L = self.length_scale()
        frames = {m["id"]: (frame_from_json(m["a"]["frame"]), frame_from_json(m["b"]["frame"])) for m in mates}
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        if not free:
            return self._failing(mates, Ts, frames, L)
        pivots = {}
        for key in free:
            boxes = [K.bbox(self.world_shape(iid)) for iid in members[key]]
            pivots[key] = (np.min([b[0] for b in boxes], axis=0) + np.max([b[1] for b in boxes], axis=0)) / 2
        n = 6 * len(free)
        # rotations are measured in mm of arc at the part's size, and cost a little more than sliding
        # (while dragging, much more: parts should slide with the mouse and only turn when a mate makes them)
        rot_cost = 1.5 if drag is None else 8.0
        # a part being kept still (e.g. one just moved by hand) is expensive to move
        cost = np.ones(n)
        for k, iid in enumerate(free):
            cost[6 * k:6 * k + 3] = rot_cost
            if keep is not None and iid == body(keep):
                cost[6 * k:6 * k + 6] *= 50.0

        def apply(Ts0, x):
            out = dict(Ts0)
            for k, key in enumerate(free):
                w = x[6 * k:6 * k + 3] / L
                v = x[6 * k + 3:6 * k + 6]
                c = pivots[key]
                R = rodrigues(w)
                D = np.eye(4)
                D[:3, :3] = R
                D[:3, 3] = c - R @ c + v
                for iid in members[key]:
                    out[iid] = D @ Ts0[iid]
            return out

        def drag_res(Ts_):
            if drag is None:
                return np.zeros(0)
            T = Ts_[drag["inst"]]
            if "points" in drag or "dirs" in drag:
                # the move / rotate triad: points to follow, and directions (lengths in mm) to turn towards
                out = [T[:3, :3] @ lp + T[:3, 3] - tp for lp, tp in drag.get("points", [])]
                out += [T[:3, :3] @ lv - tv for lv, tv in drag.get("dirs", [])]
                return np.concatenate(out)
            p = T[:3, :3] @ drag["local"] + T[:3, 3]
            return drag["P"] @ (p - drag["target"])

        def jac(fun, Ts0, r0):
            J = np.zeros((len(r0), n))
            h = 1e-6 * L
            for j in range(n):
                dx = np.zeros(n)
                dx[j] = h
                J[:, j] = (fun(apply(Ts0, dx)) - r0) / h
            return J

        mfun = lambda Ts_: self._residuals(mates, Ts_, frames, L)
        tol = 1e-9 * L
        S = np.diag(1.0 / cost)                     # variable scaling: min-norm in scaled space

        def clamp(dx):
            # keep each step modest so big moves don't fling parts around
            mx = float(np.max(np.abs(dx))) if len(dx) else 0.0
            return dx * (0.5 * L / mx) if mx > 0.5 * L else dx

        def correct(T0, steps):
            """Gauss-Newton on the mates alone (minimum-norm steps, so nothing moves needlessly)."""
            cur_ = T0
            for _ in range(steps):
                rm = mfun(cur_)
                em = float(np.linalg.norm(rm)) if len(rm) else 0.0
                if em < tol:
                    break
                Jm = jac(mfun, cur_, rm) @ S
                dx = clamp(S @ (-np.linalg.pinv(Jm, rcond=1e-6) @ rm))
                alpha, ok = 1.0, False
                for _ls in range(10):
                    trial = apply(cur_, alpha * dx)
                    r2 = mfun(trial)
                    if float(np.linalg.norm(r2)) < em:
                        cur_, ok = trial, True
                        break
                    alpha *= 0.5
                if not ok:
                    break
            return cur_

        cur = correct(Ts, iters if drag is None else 6)
        if drag is None and len(mates) and n <= 48:
            # stuck (e.g. a linkage starting out dead straight, where the first-order motion can't close a gap):
            # nudge the free parts a little and try again, keeping the best result
            def err(T_):
                r = mfun(T_)
                return float(np.linalg.norm(r)) if len(r) else 0.0
            best, best_e = cur, err(cur)
            rng = np.random.default_rng(12345)
            for attempt in range(6):
                if best_e < tol * 10:
                    break
                x = np.zeros(n)
                for k in range(len(free)):
                    x[6 * k:6 * k + 3] = rng.normal(0, 0.25 + 0.1 * attempt, 3) * L
                    x[6 * k + 3:6 * k + 6] = rng.normal(0, 0.05, 3) * L
                trial = correct(apply(best, x), min(iters, 25))
                e = err(trial)
                if e < best_e:
                    best, best_e = trial, e
            cur = best
        if drag is not None:
            for _it in range(iters):
                rd = drag_res(cur)
                ed = float(np.linalg.norm(rd))
                if ed < 1e-6 * L:
                    break
                rm = mfun(cur)
                if len(rm):
                    Jm = jac(mfun, cur, rm) @ S
                    N = np.eye(n) - np.linalg.pinv(Jm, rcond=1e-6) @ Jm
                else:
                    N = np.eye(n)
                Jd = jac(drag_res, cur, rd) @ S
                dx = clamp(S @ (N @ (np.linalg.pinv(Jd @ N, rcond=1e-6) @ (-rd))))
                alpha, ok = 1.0, False
                for _ls in range(6):
                    # step within what the mates allow, then pull back onto the mates exactly
                    trial = correct(apply(cur, alpha * dx), 4)
                    if float(np.linalg.norm(drag_res(trial))) < ed:
                        cur, ok = trial, True
                        break
                    alpha *= 0.5
                if not ok:
                    break
        for key in free:
            for iid in members[key]:
                self.instance(iid)["T"] = orthonormalize(cur[iid]).tolist()
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        return self._failing(mates, Ts, frames, L)

    def _failing(self, mates, Ts, frames, L):
        out = set()
        for m in mates:
            r = self._residuals([m], Ts, frames, L)
            if len(r) and float(np.linalg.norm(r)) > 1e-4 * max(L, 1):
                out.add(m["id"])
        return out

    def _update_status(self, Ts=None, mates=None, frames=None, L=None):
        """Per-mate 'holds or not' and the remaining degrees of freedom."""
        for k in [k for k, v in self.errors.items() if v.startswith(("Can't be solved", "Left out:"))]:
            self.errors.pop(k)
        mates = self._active_mates() if mates is None else mates
        L = self.length_scale() if L is None else L
        frames = frames or {m["id"]: (frame_from_json(m["a"]["frame"]), frame_from_json(m["b"]["frame"]))
                            for m in mates}
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        for m in mates:
            r = self._relations_in((), lambda: self._residuals([m], Ts, frames, L))
            if len(r) and float(np.linalg.norm(r)) > 1e-4 * max(L, 1):
                self.errors[m["id"]] = "Can't be solved together with the other mates (shown red)"
        # relations: one that can't be used names its mate; one that is used must hold
        live_rels = []
        for rel in self.relations:
            if rel.get("suppressed"):
                continue
            why = self._relation_left_out(rel, mates)
            if why is not None:
                self.errors[rel["id"]] = why
            elif rel.get("phase") is not None:
                r = self._relations_in({rel["id"]}, lambda: self._relation_residuals(mates, Ts, frames, L))
                if rel["id"] in self._conflicts or (len(r) and float(np.linalg.norm(r)) > 1e-4 * max(L, 1)):
                    self.errors[rel["id"]] = "Can't be solved together with the mates and relations (shown red)"
                else:
                    live_rels.append(rel["id"])
        # degrees of freedom of the free instances
        # a group is one body: one set of six variables, and one fixed member fixes the whole group
        gkey = {iid: g["id"] for g in self.groups for iid in g["instances"]}
        body = lambda iid: gkey.get(iid, iid)
        fixed = {body(i["id"]) for i in self.instances if i.get("fixed") and not i.get("suppressed")}
        members: dict = {}
        for i in self.instances:
            if i["id"] in self.parts and not i.get("suppressed") and body(i["id"]) not in fixed:
                members.setdefault(body(i["id"]), []).append(i["id"])
        free = list(members)
        n = 6 * len(free)
        if n == 0:
            self.dof = 0
            return
        ok = [m for m in mates if m["id"] not in self.errors]
        if not ok:
            self.dof = n
            return
        idx = {iid: k for k, iid in enumerate(free)}
        pivots = {}
        for key in free:
            boxes = [K.bbox(s) if s is not None else (np.zeros(3), np.zeros(3))
                     for s in (self.world_shape(iid) for iid in members[key])]
            pivots[key] = (np.min([b[0] for b in boxes], axis=0) + np.max([b[1] for b in boxes], axis=0)) / 2
        resid = lambda T: self._relations_in(live_rels, lambda: self._residuals(ok, T, frames, L))
        r0 = resid(Ts)
        J = np.zeros((len(r0), n))
        h = 1e-6 * L
        for key, k in idx.items():
            for j in range(6):
                T2 = dict(Ts)
                if j < 3:
                    w = np.zeros(3)
                    w[j] = h / L
                    R = rodrigues(w)
                    D = np.eye(4)
                    D[:3, :3] = R
                    D[:3, 3] = pivots[key] - R @ pivots[key]
                else:
                    D = np.eye(4)
                    D[j - 3, 3] = h
                for iid in members[key]:
                    T2[iid] = D @ Ts[iid]
                J[:, 6 * k + j] = (resid(T2) - r0) / h
        sv = np.linalg.svd(J, compute_uv=False) if J.size else np.zeros(0)
        rank = int(np.sum(sv > 1e-6 * (sv[0] if len(sv) else 1)))
        self.dof = n - rank

    def instance_dof(self, iid):
        """Degrees of freedom left for one instance, holding every other instance still."""
        inst = self.instance(iid)
        if inst is None or inst.get("fixed") or inst.get("suppressed"):
            return 0
        g = self.group_of(iid)
        ids = list(g["instances"]) if g is not None else [iid]     # a group moves as one body
        ids = [i for i in ids if not self.instance_suppressed(i)]  # a suppressed member is not part of the body
        if any((self.instance(i) or {}).get("fixed") for i in ids):
            return 0
        ids = [i for i in ids if i in self.parts] or [iid]
        allok = [m for m in self._active_mates() if m["id"] not in self.errors]
        mates = [m for m in allok if (m["a"]["inst"] in ids or m["b"]["inst"] in ids)]
        if not mates:
            return 6
        L = self.length_scale()
        frames = {m["id"]: (frame_from_json(m["a"]["frame"]), frame_from_json(m["b"]["frame"])) for m in allok}
        # active relations on this instance's mates; the other end's mate is measured with its part held still
        rels = [r["id"] for r in self.relations
                if r["id"] not in self.errors and r["id"] not in self._conflicts
                and {r["a"]["mate"], r["b"]["mate"]} & {m["id"] for m in mates}]
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        boxes = [K.bbox(self.world_shape(i)) for i in ids]
        c = (np.min([b[0] for b in boxes], axis=0) + np.max([b[1] for b in boxes], axis=0)) / 2
        resid = lambda T: np.concatenate([
            self._relations_in((), lambda: self._residuals(mates, T, frames, L)),
            self._relations_in(rels, lambda: self._relation_residuals(allok, T, frames, L))])
        r0 = resid(Ts)
        J = np.zeros((len(r0), 6))
        h = 1e-6 * L
        for j in range(6):
            T2 = dict(Ts)
            if j < 3:
                w = np.zeros(3)
                w[j] = h / L
                R = rodrigues(w)
                D = np.eye(4)
                D[:3, :3] = R
                D[:3, 3] = c - R @ c
            else:
                D = np.eye(4)
                D[j - 3, 3] = h
            for i in ids:
                T2[i] = D @ Ts[i]
            J[:, j] = (resid(T2) - r0) / h
        sv = np.linalg.svd(J, compute_uv=False)
        return 6 - int(np.sum(sv > 1e-6 * (sv[0] if len(sv) else 1)))

    # ------------------------------------------------------------------ analysis
    def interferences(self, progress=None):
        """Pairs of visible instances whose solids overlap: [(iid_a, iid_b, volume, overlap shape)]."""
        vis = [i for i in self.visible_instances() if not is_sketch_source(i["source"])]
        shapes = {i["id"]: self.world_shape(i["id"]) for i in vis}
        boxes = {k: K.bbox(s) for k, s in shapes.items()}
        out = []
        ids = [i["id"] for i in vis]
        pairs = [(a, b) for k, a in enumerate(ids) for b in ids[k + 1:]]
        # a Standard part overlaps what it is fastened to by design (a screw in a tap-drill hole)
        std = {i["id"] for i in self.instances if i["source"].get("kind") == "standard"}
        joined = {frozenset((m["a"]["inst"], m["b"]["inst"])) for m in self.mates
                  if m.get("type") == "fastened" and not m.get("suppressed") and m.get("a") and m.get("b")}
        for n, (a, b) in enumerate(pairs):
            if (a in std or b in std) and frozenset((a, b)) in joined:
                continue
            if progress:
                progress(n, len(pairs))
            (la, ha), (lb, hb) = boxes[a], boxes[b]
            if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
                continue
            try:
                ov = K.common(shapes[a], shapes[b])
                v = K.volume(ov) if not ov.IsNull() else 0.0
            except Exception:
                continue
            if v > 1e-3:
                out.append((a, b, v, ov))
        return out

    def bom(self):
        """Bill of materials: one row per distinct part, with how many times it is used."""
        rows = {}
        for inst in self.instances:
            if inst.get("suppressed"):
                continue
            src = inst["source"]
            if is_sketch_source(src):
                continue                                    # a Sketch instance is not a part
            key = self._source_key(src)
            if src.get("kind") == "standard":
                # model_thread only changes the geometry detail, so it does not split a BOM row
                key = ("standard", src.get("type"), json.dumps(
                    {k: v for k, v in (src.get("params") or {}).items() if k != "model_thread"},
                    sort_keys=True, default=str))
            if src.get("kind") == "step":
                where = src.get("filename", "STEP")
            elif src.get("kind") == "standard":
                where = "Standard part"
            else:
                where = src.get("studio_name") or os.path.basename(src.get("path") or "") or "Part Studio"
            p = self.parts.get(inst["id"])
            r = rows.setdefault(key, {"name": inst.get("part_name") or inst["name"], "source": where, "qty": 0,
                                      "volume": None, "instances": [],
                                      "material": MS.material_name(inst.get("material")), "mass": None})
            r["qty"] += 1
            r["instances"].append(inst["name"])
            if r["volume"] is None and p is not None:
                try:
                    r["volume"] = K.volume(p.shape)
                except Exception:
                    pass
        for r in rows.values():
            # mass of one part; a part with no material (or none known) is weighed as PLA, as before
            r["mass"] = None if r["volume"] is None else MS.mass_g(r["volume"], r["material"])
        return list(rows.values())

    @staticmethod
    def bom_total(rows):
        """The total row of a bill of materials: {"qty", "mass"} (mass in g, parts with no volume left out)."""
        return {"qty": sum(r["qty"] for r in rows),
                "mass": sum(r["qty"] * r["mass"] for r in rows if r["mass"] is not None)}

    def mass_summary(self):
        """Mass of the visible instances: {"mass", "by_material": {name: g}, "com": (x, y, z) or None}.
        The centre of mass is the mass-weighted mean of each instance's centroid in the assembly frame."""
        by_mat, tot, moment = {}, 0.0, np.zeros(3)
        for inst in self.visible_instances():
            try:
                mp = K.mass_props(self.world_shape(inst["id"]))
            except Exception:
                continue
            mat = MS.material_name(inst.get("material"))
            m = MS.mass_g(mp["volume"], mat)
            key = mat or f"{MS.DEFAULT_MATERIAL} (default)"
            by_mat[key] = by_mat.get(key, 0.0) + m
            tot += m
            moment += m * np.asarray(mp["centroid"], float)
        return {"mass": tot, "by_material": by_mat, "com": tuple(moment / tot) if tot > 0 else None}

    def export(self, path, ids=None, options=None):
        insts = [i for i in self.instances if i["id"] in self.parts and (ids is None or i["id"] in ids)
                 and not i.get("hidden") and not i.get("suppressed") and not is_sketch_source(i.get("source"))]
        if not insts:
            raise K.KernelError("Nothing to export")
        if str(path).lower().endswith(".stl"):
            K.export_stl([self.world_shape(i["id"]) for i in insts], path, **(options or {}))
        elif str(path).lower().endswith((".3mf", ".obj")):
            from . import meshexport as ME
            first = {}      # copies of one source share a shape, so they are meshed once
            ME.export_instances([(first.setdefault(self._source_key(i["source"]), self.parts[i["id"]].shape),
                                  np.array(i["T"], float), i["name"], self.instance_color(i)) for i in insts],
                                path, **(options or {}))
        else:
            K.export_step_named([(self.parts[i["id"]].shape, np.array(i["T"], float), i["name"],
                                  self.instance_color(i)) for i in insts], path, self.name)

    # ------------------------------------------------------------------ files
    def _part_brep(self, shape, prior=None):
        """(key, bytes) of the saved copy of a part shape (K.shape_to_brep, the bytes the base64 `_brep` held
        before I4.9), or (None, None) when it cannot be written. Kept per shape until the shape is gone.
        `prior` is the copy the instance names now (`_brep_blob` as loaded): when it holds the same shape
        (blobstore.same_copy), its key and bytes are kept. A shape that reached this Assembly another way (an
        engine worker parses the BREP its Part Studio sent; meshing for display adds triangles) writes other
        bytes for the same shape, so without this every save of an unchanged Assembly would add a blob."""
        hit = self._breps.get(id(shape))
        if hit is not None and hit[0] is shape:
            return hit[1:]
        from . import blobstore
        raw = None
        if prior:
            try:
                old = self._blob_get(prior)
            except blobstore.BlobError:
                old = None
            if old is not None and blobstore.same_copy(shape, old, self._copy_geometry):
                raw = old
        try:
            raw = raw if raw is not None else K.shape_to_brep(shape)
        except Exception:
            return None, None
        self._breps[id(shape)] = (shape, blobstore.key_of(raw), raw)
        return self._breps[id(shape)][1:]

    def _saved_blobs(self, embed=True):
        """{key: bytes} of every blob `to_json(embed)` names that can be read now: the STEP files of `step`
        instances, with `embed` a fresh copy of each resolved part, and the copy an instance already names
        (a blob that is missing stays missing; its instance is red)."""
        from . import blobstore
        out = {}
        for inst in self.instances:
            kind, key = inst["source"].get("kind"), None
            if kind == "standard":
                continue
            if kind == "step":
                key = inst["source"].get("blob")
            elif embed and inst["id"] in self.parts:
                key, raw = self._part_brep(self.parts[inst["id"]].shape, inst.get("_brep_blob"))
                if key is not None:
                    out[key] = raw
                continue
            else:
                key = inst.get("_brep_blob")
            if key:
                try:
                    out[key] = self._blob_get(key)
                except blobstore.BlobError:
                    pass
        return out

    def write_blobs(self, path=None, store=None, embed=True):
        """Put every blob the file names into `store` (an object with `put`; by default the blob store beside
        `path`, the tab file about to be written), before the JSON that names the keys. A loose Assembly
        writes them beside itself; in a Document, doc_folder hands in the store's blobs for this tab. With no
        store and no path there is nowhere to write, and the blobs stay held."""
        from . import blobstore
        target = path or self.path
        if store is None and not target:
            return
        store = store if store is not None else blobstore.store_for(target)
        for raw in self._saved_blobs(embed).values():
            store.put(raw)

    def to_json(self, embed=True):
        insts = copy.deepcopy(self.instances)
        if embed:
            # a copy of each linked part goes to the blobs, so the file still opens if the Part Studio is missing
            live = {id(p.shape) for p in self.parts.values()}
            self._breps = {k: v for k, v in self._breps.items() if k in live}
            for inst in insts:
                if inst["source"].get("kind") == "step":
                    inst.pop("_brep_blob", None)
                    continue
                if inst["source"].get("kind") == "standard":
                    inst.pop("_brep_blob", None)  # regenerated from the Plugin, never stored
                    continue
                p = self.parts.get(inst["id"])
                if p is None:
                    continue
                key, _ = self._part_brep(p.shape, inst.get("_brep_blob"))
                if key:
                    inst["_brep_blob"] = key
                if self.path:
                    sp = inst["source"].get("path")
                    if sp:
                        try:
                            inst["source"]["relpath"] = os.path.relpath(sp, os.path.dirname(os.path.abspath(self.path)))
                        except ValueError:
                            pass
        return json.dumps({"format": FILE_FORMAT, "version": M.latest_version("assembly"), "name": self.name,
                           "uid": self.uid, "instances": insts, "mates": self.mates, "groups": self.groups,
                           "relations": self.relations,
                           **({"connectors": self.connectors} if self.connectors else {})}, indent=1)

    def to_data(self) -> bytes:
        """The tab's data as a Document store holds it (the bytes `save` writes). Set `path` first: relative
        source paths are written against it. Binary data is named by key only: call `write_blobs` first."""
        return self.to_json().encode("utf-8")

    def save(self, path=None):
        self.path = path or self.path
        self.write_blobs(self.path)
        txt = self.to_data().decode("utf-8")
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(txt)
        os.replace(tmp, self.path)
        self.dirty = False

    @staticmethod
    def load(path, resolver=None):
        with open(path, "rb") as fh:
            data = fh.read()
        return AssemblyDoc.from_data(data, path=path, resolver=resolver)

    @staticmethod
    def from_data(data, path=None, resolver=None, blobs=None):
        """An Assembly from its bytes. `path` is the file they came from, if any: it names the file in errors,
        becomes the Assembly's `path`, and a source's `relpath` is found against its folder. `resolver` resolves
        sources. `blobs` is this tab's blob lookup in a Document store (docstore.DocumentStore.blobs, or any object
        with `get(key)` returning the bytes and raising blobstore.BlobError when it has none): STEP files and saved
        part copies are read from it, then from the blobs folder beside `path`. Bytes an old file carried inline
        are held in memory until the next save (I4.9)."""
        from . import blobstore
        d = json.loads(bytes(data).decode("utf-8"))
        if d.get("format") != FILE_FORMAT:
            raise K.KernelError("Not a FreeShape assembly")
        d = M.migrate("assembly", d, path if path is not None else "This Assembly")
        held = blobstore.take_held(d)
        a = AssemblyDoc()
        a.blobs = held
        a.blob_view = blobs
        a.name = d.get("name", "Assembly 1")
        a.uid = d.get("uid") or a.uid
        a.instances = d.get("instances", [])
        a.mates = d.get("mates", [])
        a.groups = d.get("groups", [])
        a.relations = d.get("relations", [])
        a.connectors = d.get("connectors", [])
        a.path = path
        base = os.path.dirname(os.path.abspath(path)) if path is not None else None
        for inst in a.instances:
            src = inst["source"]
            if base is not None and src.get("relpath") and not (src.get("path") and os.path.exists(src["path"])):
                cand = os.path.normpath(os.path.join(base, src["relpath"]))
                if os.path.exists(cand):
                    src["path"] = cand
        nums = [int("".join(c for c in x["id"] if c.isdigit()) or 0)
                for x in a.instances + a.mates + a.groups + a.relations + a.connectors]
        a._counter = max(nums + [0])
        a.resolver = resolver
        a.refresh(solve=False, notify=False)
        a._update_mate_values(a.mates, fill_only=True)   # files from before Mate values were stored
        return a
