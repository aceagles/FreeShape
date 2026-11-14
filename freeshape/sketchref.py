"""Sketch entity frames and signatures (ADR 0011).

A mate end on Sketch instance geometry stores a sketch entity id and a role. This
module turns that pair into a frame, from a published sketch alone (the plane and
the solved sketch dict that engine.contract.SketchOut carries).

Pure: no Qt, no freeshape.assembly. Callers pass plain dicts.

Shapes used throughout
  plane   {"origin": [x, y, z], "xdir": [x, y, z], "normal": [x, y, z]} in studio
          coordinates. The plane's y axis is normal x xdir.
  sketch  {"points": {pid: [x, y]}, "entities": {eid: {...}}} in plane-local 2D.
          Entity types: line {p1, p2}, circle {c, r}, arc {c, p1, p2}, point {p}.
          The sketch origin point has id "origin".
  eid     an entity id (l7, c3, a2, a point entity) or a bare point id (p4, origin).
  role    one of "point", "end1", "end2", "mid", "centre".
  frame   {"o": [x, y, z], "z": [x, y, z], "x": [x, y, z]} in studio coordinates.

FreeCAD (Assembly/UtilsAssembly.py findPlacement) puts a vertex joint at the vertex,
a line joint at the line middle or an end vertex, and a circle joint at the circle
location (centre). We follow that choice of points. FreeCAD orients a line joint with
z along the line; ADR 0011 and the card fix z to the sketch plane normal instead, so
every sketch frame lies flat on its plane and x carries the line direction.
"""
from __future__ import annotations

import numpy as np

from . import kernel as K

ROLES = ("point", "end1", "end2", "mid", "centre")
_EPS = 1e-9
_ND = 4   # decimals kept in a signature


def _vec(v):
    return np.asarray(v, dtype=float)


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > _EPS else v


def _resolve(sketch, eid):
    """Return (etype, entity dict or None) for an entity id or a bare point id.
    Raises K.KernelError naming eid when it is in neither table."""
    ents = sketch.get("entities", {}) or {}
    if eid in ents:
        e = ents[eid]
        return e.get("type"), e
    if eid in (sketch.get("points", {}) or {}):
        return "point", None
    raise K.KernelError(f"Sketch entity {eid} no longer exists in the sketch")


def _pt(sketch, pid, eid):
    try:
        p = sketch["points"][pid]
    except KeyError:
        raise K.KernelError(f"Sketch entity {eid} refers to missing point {pid}") from None
    return _vec(p[:2])


def _role_points(sketch, eid, etype, e):
    """{role: 2D position} for the entity."""
    if etype == "point":
        pid = e["p"] if e is not None else eid
        return {"point": _pt(sketch, pid, eid)}
    if etype == "line":
        a, b = _pt(sketch, e["p1"], eid), _pt(sketch, e["p2"], eid)
        return {"end1": a, "end2": b, "mid": (a + b) / 2.0}
    if etype == "circle":
        return {"centre": _pt(sketch, e["c"], eid)}
    if etype == "arc":
        return {"centre": _pt(sketch, e["c"], eid), "end1": _pt(sketch, e["p1"], eid),
                "end2": _pt(sketch, e["p2"], eid)}
    # the curve types of upstream 2.0: an ellipse offers its centre, a spline the first and last point of its
    # point list (a closed one too: end2 is its last listed point, not the start it loops back to), a text its
    # anchor point
    if etype == "ellipse":
        return {"centre": _pt(sketch, e["c"], eid)}
    if etype == "spline" and e.get("pts"):
        return {"end1": _pt(sketch, e["pts"][0], eid), "end2": _pt(sketch, e["pts"][-1], eid)}
    if etype == "text":
        return {"point": _pt(sketch, e["p"], eid)}
    raise K.KernelError(f"Sketch entity {eid} has unsupported type {etype}")


def roles(sketch, eid):
    """Roles that sketch entity `eid` offers, as a list of strings.

    Inputs: sketch dict (plane-local 2D), eid an entity id or point id.
    Output: point -> ["point"]; line -> ["end1", "end2", "mid"]; circle -> ["centre"];
    arc -> ["centre", "end1", "end2"]; ellipse -> ["centre"]; spline -> ["end1", "end2"]; text -> ["point"].
    Raises K.KernelError if eid is not in the sketch.
    """
    etype, e = _resolve(sketch, eid)
    pts = _role_points(sketch, eid, etype, e)
    order = {"point": 0, "centre": 0, "end1": 1, "end2": 2, "mid": 3}
    return sorted(pts, key=order.__getitem__)


def frame(plane, sketch, eid, role):
    """Frame {o, z, x} of `role` on sketch entity `eid`, in studio coordinates.

    Inputs: plane {origin, xdir, normal} (studio), sketch dict (plane-local 2D), eid,
    role. Output: o = the role's position on the plane; z = the unit plane normal;
    x = unit vector from end1 to end2 for a line role (point, end1, end2, mid on a
    line), else the unit plane xdir. All values are plain lists of 3 floats.
    A zero-length line falls back to the plane xdir. Raises K.KernelError if the
    entity is missing or does not offer the role.
    """
    etype, e = _resolve(sketch, eid)
    pts = _role_points(sketch, eid, etype, e)
    if role not in pts:
        raise K.KernelError(f"Sketch entity {eid} ({etype}) has no role {role}")
    origin, xdir, nrm = _vec(plane["origin"]), _unit(_vec(plane["xdir"])), _unit(_vec(plane["normal"]))
    ydir = np.cross(nrm, xdir)
    u, v = pts[role]

    def to3(p):
        return origin + p[0] * xdir + p[1] * ydir

    x = xdir
    if etype == "line":
        d = to3(pts["end2"]) - to3(pts["end1"])
        if np.linalg.norm(d) > _EPS:
            x = _unit(d)
    return {"o": [float(c) for c in to3(pts[role])], "z": [float(c) for c in nrm],
            "x": [float(c) for c in x]}


def describe(sketch, eid, role):
    """Entity type and geometric signature of `role` on `eid`, to store in a mate end.

    Inputs: sketch dict (plane-local 2D), eid, role. Output: {"etype": str, "sig": dict}
    with sig {"at": [u, v]} the role's plane-local 2D position, plus "len" for a line
    or "r" for a circle or arc; values rounded to 4 decimals. Plane-local, so moving
    the sketch plane does not change it. Raises K.KernelError if missing or no such role.
    """
    etype, e = _resolve(sketch, eid)
    pts = _role_points(sketch, eid, etype, e)
    if role not in pts:
        raise K.KernelError(f"Sketch entity {eid} ({etype}) has no role {role}")
    sig = {"at": [round(float(c), _ND) for c in pts[role]]}
    if etype == "line":
        sig["len"] = round(float(np.linalg.norm(pts["end2"] - pts["end1"])), _ND)
    elif etype == "circle":
        sig["r"] = round(float(e["r"]), _ND)
    elif etype == "arc":
        sig["r"] = round(float(np.linalg.norm(pts["end1"] - pts["centre"])), _ND)
    return {"etype": etype, "sig": sig}


def check(sketch, ref):
    """Verify a stored sketch-geometry reference still resolves; returns None.

    Inputs: sketch dict, ref {"entity", "etype", ...} as stored on a mate end.
    Raises K.KernelError naming the entity when the id is gone or its type differs
    from ref["etype"]. A changed signature is not an error: the sketch was edited
    (ADR 0011). Callers that want to warn compare describe() against ref["sig"].
    """
    eid = ref["entity"]
    etype, _ = _resolve(sketch, eid)
    want = ref.get("etype")
    if want and etype != want:
        raise K.KernelError(f"Sketch entity {eid} is now a {etype}, not a {want}")
