"""Inserting Standard parts into holes: which holes a selection points at (headless, no UI imports).

hole_targets() turns viewport-style picks into hole targets. It is the geometry half of FreeCAD's
FSGetAttachableSelections (Fasteners workbench, FastenerBase.py), working on the instance's resolved shape
whatever kind of instance it is, so a washer's rim is as valid a target as a plate's hole.

A pick is a dict ``{"inst": id, "kind": "face"|"edge", "index": int, "click": (x, y, z) or None}``, with
``click`` a world point. A viewport ``Pick`` (``kind``, ``key=(inst, index)``) and a tuple
``(inst, kind, index[, click])`` are accepted too.

A target is a dict ``{"inst": id, "edge": int, "diameter": float, "centre": (x, y, z)}``: ``edge`` is an index
into ``kernel.edges()`` of the instance's part, the same index ``AssemblyDoc.connector(inst, "edge", edge)``
takes, and ``centre`` is in world coordinates.
"""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K

TOL = 1e-6


def normalize_pick(p):
    """(inst, kind, index, click or None) from any accepted pick shape, or None when it is not one."""
    if isinstance(p, dict):
        inst, kind, index, click = p.get("inst"), p.get("kind"), p.get("index"), p.get("click")
    elif hasattr(p, "kind") and hasattr(p, "key"):
        key = tuple(p.key)
        if len(key) < 2:
            return None
        inst, kind, index, click = key[0], p.kind, key[1], None
    elif isinstance(p, (tuple, list)) and len(p) >= 3:
        inst, kind, index = p[0], p[1], p[2]
        click = p[3] if len(p) > 3 else None
    else:
        return None
    if kind not in ("face", "edge") or inst is None or index is None:
        return None
    return inst, kind, int(index), (None if click is None else np.asarray(click, float))


def _full_circle(edge):
    """Edge info of a complete circular edge, else None (arcs, lines and curves are not holes)."""
    info = K.edge_info(edge)
    if "center" not in info or "r" not in info:
        return None
    if abs(info["len"] - 2 * math.pi * info["r"]) > 1e-6 * max(1.0, info["r"]):
        return None
    return info


def _surface_type(face):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    return BRepAdaptor_Surface(face).GetType()


def _outer_wire_edges(face):
    from OCP.BRepTools import BRepTools
    ow = BRepTools.OuterWire_s(face)
    return [e for e in K.edges(ow)]


def hole_targets(doc, picks):
    """Ordered, de-duplicated hole targets for `picks` (see the module doc for both shapes).

    A full circular edge is one target. A planar face gives every full circle on it that is not in its outer
    wire. A cylindrical face gives the rim nearest the click, or, with no click, the rim farther along the
    connector Z of the face. Anything else gives nothing."""
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
    from OCP.TopExp import TopExp
    from OCP.TopAbs import TopAbs_EDGE
    from .assembly import connector_frame

    out, seen = [], []
    for raw in picks or []:
        pk = normalize_pick(raw)
        if pk is None:
            continue
        iid, kind, index, click = pk
        part = doc.parts.get(iid)
        if part is None:
            continue
        shape = part.shape
        T = doc.T(iid)
        emap = K.TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, emap)

        def edge_index(e):
            k = emap.FindIndex(e)
            return k - 1 if k > 0 else None

        cands = []          # (edge, info)
        if kind == "edge":
            es = K.edges(shape)
            if 0 <= index < len(es):
                info = _full_circle(es[index])
                if info is not None:
                    cands.append((es[index], info))
        else:
            fs = K.faces(shape)
            if not 0 <= index < len(fs):
                continue
            f = fs[index]
            st = _surface_type(f)
            if st == GeomAbs_Plane:
                outer = _outer_wire_edges(f)
                for e in K.edges(f):
                    info = _full_circle(e)
                    if info is None or any(o.IsSame(e) for o in outer):
                        continue
                    cands.append((e, info))
            elif st == GeomAbs_Cylinder:
                rims = [(e, i) for e in K.edges(f) for i in [_full_circle(e)] if i is not None]
                if rims:
                    if click is not None:
                        Ti = np.linalg.inv(T)
                        c_local = Ti[:3, :3] @ click + Ti[:3, 3]
                        best = min(rims, key=lambda r: float(np.linalg.norm(np.asarray(r[1]["center"]) - c_local)))
                    else:
                        z = np.asarray(connector_frame(shape, "face", index, None)["z"], float)
                        best = max(rims, key=lambda r: float(np.asarray(r[1]["center"]) @ z))
                    cands.append(best)

        for e, info in cands:
            ei = edge_index(e)
            if ei is None:
                continue
            centre = T[:3, :3] @ np.asarray(info["center"], float) + T[:3, 3]
            dia = 2.0 * float(info["r"])
            if any(s[0] == iid and abs(s[2] - dia) <= TOL and np.max(np.abs(s[1] - centre)) <= TOL for s in seen):
                continue
            seen.append((iid, centre, dia))
            out.append({"inst": iid, "edge": ei, "diameter": dia, "centre": tuple(float(a) for a in centre)})
    return out
