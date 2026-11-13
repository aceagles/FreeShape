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
import re

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


def sizes_for(type_id, targets):
    """Group hole `targets` (as hole_targets returns them) by the size of Standard part `type_id` that fits.

    Returns ``{"groups": [{"size": str, "targets": [target, ...]}, ...], "unmatched": [target, ...]}``.
    Groups come in order of first appearance of their size in `targets`; targets keep their input order within
    a group and in "unmatched". Targets are returned as the same objects, not copies. Raises KeyError for an
    unknown `type_id`."""
    from .pyfeature import standard_part
    cls = standard_part(type_id)
    if cls is None:
        raise KeyError(type_id)
    part = cls()
    groups, by_size, unmatched = [], {}, []
    for t in targets or []:
        size = part.match_hole(t["diameter"])
        if size is None:
            unmatched.append(t)
            continue
        if size not in by_size:
            by_size[size] = {"size": size, "targets": []}
            groups.append(by_size[size])
        by_size[size]["targets"].append(t)
    return {"groups": groups, "unmatched": unmatched}


# --------------------------------------------------------------------------------------------- plan / apply
DEFAULT_LENGTH_FACTOR = 1.5


def _nominal_diameter(part, size):
    """Nominal diameter in mm of `size`, for the default-length rule. Uses the part's own
    ``nominal_diameter(size)`` when it has one; otherwise the first number in the size name ("M3" -> 3.0,
    "M3.5" -> 3.5). None when neither works (the default length is then the shortest listed)."""
    fn = getattr(part, "nominal_diameter", None)
    if callable(fn):
        try:
            return float(fn(size))
        except Exception:
            pass
    m = re.search(r"\d+(?:\.\d+)?", str(size))
    return float(m.group()) if m else None


def _lengths(part, size, base_params):
    """The type's lengths for `size`, passing the keywords (e.g. standard, point) the method accepts."""
    import inspect
    params = inspect.signature(part.lengths).parameters
    kw = {k: v for k, v in (base_params or {}).items() if k in params and k != "size"}
    try:
        return [float(x) if not isinstance(x, int) else x for x in part.lengths(size, **kw)]
    except Exception:
        return []


def _default_length(lengths, diameter):
    """Shortest of `lengths` at least 1.5 x `diameter`; the longest when none is; None when no lengths."""
    if not lengths:
        return None
    ls = sorted(lengths)
    if diameter is not None:
        for x in ls:
            if x >= DEFAULT_LENGTH_FACTOR * diameter - 1e-9:
                return x
        return ls[-1]
    return ls[0]


def plan_insert(doc, type_id, picks, base_params=None):
    """Plan inserting Standard part `type_id` into the holes `picks` point at (see hole_targets).

    Returns ``{"type": type_id,
               "groups": [{"size": str, "targets": [target, ...], "lengths": [mm, ...],
                           "default_length": mm or None}, ...],
               "unmatched": [target, ...]}``
    Groups and targets are as sizes_for returns them (targets are hole_targets dicts, same objects).
    "lengths" is the type's ordered lengths for the size (empty for a nut or washer) and "default_length" the
    shortest of them at least 1.5 x the nominal diameter (the longest when none is; None when no lengths).
    The nominal diameter is the part's ``nominal_diameter(size)`` when it has one, else the number in the size
    name. `base_params` (e.g. standard, point) selects which lengths apply. Raises KeyError for an unknown
    type id. With no matching holes both lists are empty: the caller then inserts one free instance."""
    from .pyfeature import standard_part
    res = sizes_for(type_id, hole_targets(doc, picks))
    part = standard_part(type_id)()
    groups = []
    for g in res["groups"]:
        ls = _lengths(part, g["size"], base_params)
        groups.append({"size": g["size"], "targets": g["targets"], "lengths": ls,
                       "default_length": _default_length(ls, _nominal_diameter(part, g["size"]))})
    return {"type": type_id, "groups": groups, "unmatched": res["unmatched"]}


def apply_insert(doc, plan, lengths_by_size=None, base_params=None):
    """Insert one Standard part per target of `plan` (from plan_insert), seated on its hole, in ONE undo step.

    Each instance gets ``{**base_params, "size": group size, "length": chosen}`` (no length for a group
    without lengths). The length is ``lengths_by_size[size]`` or, when absent, the group's default length.
    Each instance's insert_connector is mated with an unflipped fastened mate to the target's circular-edge
    connector; the instance is placed with place_for_mate and the mates are solved once at the end.
    Returns ``{"instances": [iid, ...], "mates": [mid, ...]}`` in target order, group by group. Instances are
    plain and independent. Every group's params are validated first: a bad size or length raises KernelError
    before anything changes, and a failure part way undoes the step. An empty plan (no targets) changes
    nothing, makes no undo step, and returns empty lists; the caller inserts a free instance instead."""
    import copy
    from .pyfeature import registry
    type_id = plan["type"]
    cls = registry.standard_part(type_id)
    if cls is None:
        raise K.KernelError(f"Standard part {type_id} is not installed")
    part = cls()
    lengths_by_size = lengths_by_size or {}
    jobs = []                                   # (target, params)
    for g in plan["groups"]:
        if not g["targets"]:
            continue
        p = dict(base_params or {})
        p["size"] = g["size"]
        p.pop("length", None)
        if g.get("lengths"):
            p["length"] = lengths_by_size.get(g["size"], g.get("default_length"))
        try:
            clean = part.validate(p)
        except Exception as e:
            raise K.KernelError(f"Standard part {type_id} {g['size']}: {e}")
        jobs.extend((t, clean) for t in g["targets"])
    if not jobs:
        return {"instances": [], "mates": []}
    pid = type_id.split("/")[0][len(registry.STANDARD_PREFIX):]
    info = registry.plugin(pid)
    conn = part.insert_connector
    doc.checkpoint()
    iids, mids = [], []
    try:
        for t, clean in jobs:
            src = {"kind": "standard", "type": type_id, "plugin": pid,
                   "plugin_version": info.version if info else "", "params": copy.deepcopy(clean)}
            iid = doc.insert(src, checkpoint=False)
            m = doc.make_mate("fastened")
            m["a"] = doc.connector(t["inst"], "edge", t["edge"])
            m["b"] = doc.named_connector(iid, conn)
            doc.mates.append(m)
            doc.place_for_mate(m)
            iids.append(iid)
            mids.append(m["id"])
        doc.solve()
        doc.notify()
    except Exception:
        doc.undo()
        raise
    return {"instances": iids, "mates": mids}
