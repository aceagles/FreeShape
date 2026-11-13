"""Sheet metal: flat panels joined by bends, folded up in 3D and unfolded into an exact flat pattern.

A sheet metal part keeps a small model next to its solid:

* panels — flat pieces of sheet. Each has its outline as a planar face in its own 2D coordinates (material
  from z = 0 to z = thickness), and two placements: folded (where it is in the 3D part) and flat (where it
  lands in the flat pattern, which lies in the base panel's plane);
* bends — each joins a panel's straight edge to a new panel, with an angle, an inside radius, and a direction
  (up = towards the material side, down = away from it).

Bend allowance (the length of sheet a bend uses up) = angle × (radius + K × thickness), with the K-factor
saying where the neutral line sits in the thickness (0.5 = the middle; ~0.42–0.45 suits most steel and
aluminium bent in a press brake).

A flange is added to the existing solid (a bend + a new panel), so holes cut earlier stay. The flat pattern is
built from the finished solid: each panel's face on its z = 0 side is moved into the flat, and each bend becomes
a strip as long as its bend allowance. So holes and cut-outs made with normal features (extrude cut, hole …)
appear in the flat pattern too, as long as they are in the flat panels and not across a bend.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from . import kernel as K
from . import naming as N


# --------------------------------------------------------------------------- small geometry helpers
def frame(o, x, z):
    """4x4 matrix mapping local (x, y, z) to world, from an origin and x / z axes."""
    z = np.asarray(z, float)
    z = z / np.linalg.norm(z)
    x = np.asarray(x, float)
    x = x - z * (x @ z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = x, y, z, np.asarray(o, float)
    return M


def rot_about(point, axis, ang):
    """4x4 rotation by ang (radians) about the line through point along axis."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    Kx = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) + math.sin(ang) * Kx + (1 - math.cos(ang)) * (Kx @ Kx)
    M = np.eye(4)
    M[:3, :3] = R
    p = np.asarray(point, float)
    M[:3, 3] = p - R @ p
    return M


def apply(M, p):
    return (np.asarray(M, float) @ np.append(np.asarray(p, float), 1.0))[:3]


def _polygon_face(pts3):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    mp = BRepBuilderAPI_MakePolygon()
    for q in pts3:
        mp.Add(K._pnt(q))
    mp.Close()
    return BRepBuilderAPI_MakeFace(mp.Wire(), True).Face()


def _prism(face, vec, with_history=False):
    """The prism of a face; with_history also returns its history (the caps are recorded)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    mk = BRepPrimAPI_MakePrism(face, K._vec(vec))
    if with_history:
        return mk.Shape(), K._maker_history(mk, [face], caps=True)
    return mk.Shape()


def _moved(shape, M, with_history=False):
    """A moved copy of a shape; with_history also returns the history that maps each element to its image."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    mk = BRepBuilderAPI_Transform(shape, K.trsf_from_matrix(M), True)
    if with_history:
        return mk.Shape(), K._maker_history(mk, [shape])
    return mk.Shape()


def _unify(shape, with_history=False):
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    u.Build()
    if with_history:
        return u.Shape(), K._maker_history(u, [shape])
    return u.Shape()


def _inside_point(face):
    """A point certainly on a face (the middle of one of its mesh triangles) — its centroid can be in a hole."""
    m = K.tessellate(face)
    if len(m.tris):
        return np.asarray(m.verts, float)[np.asarray(m.tris[0])].mean(axis=0)
    return np.asarray(K.face_info(face)["c"], float)


def _face_contains(face, pt2, tol=1e-6):
    """Is the 2D point inside (or on) a planar face lying in z = 0?"""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    from OCP.gp import gp_Pnt2d
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    surf = BRep_Tool.Surface_s(face)
    pr = GeomAPI_ProjectPointOnSurf(K._pnt((pt2[0], pt2[1], 0.0)), surf)
    if pr.NbPoints() < 1:
        return False
    u, v = pr.LowerDistanceParameters()
    c = BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tol)
    return c.State() in (TopAbs_IN, TopAbs_ON)


# --------------------------------------------------------------------------- the model
class SheetModel:
    """Panels and bends of one sheet metal part (see the module docstring)."""

    def __init__(self, thickness, radius, k, relief="rect", relief_scale=1.5):
        self.thickness = float(thickness)
        self.radius = float(radius)
        self.k = float(k)
        self.relief = relief                  # corner relief where two bends meet: "rect" or "none"
        self.relief_scale = float(relief_scale)
        self.reliefs = []         # dicts: panel, c (local 2D corner), o1, o2 (the bends' outward dirs), e1, e2 (how
        #                                 far the notch reaches past each bend line, i.e. its bend allowance), s (size)
        self.panels = []          # dicts: id, face (local, z=0), fold (4x4), flat (4x4), parent, bend
        self.bends = []           # dicts: id, panel (parent id), child, a, b (local 2D of the parent), o (outward),
        #                                 angle (deg), radius, up, ba

    def copy(self):
        m = SheetModel(self.thickness, self.radius, self.k, self.relief, self.relief_scale)
        m.panels = [dict(p) for p in self.panels]
        m.bends = [dict(b) for b in self.bends]
        m.reliefs = [dict(r) for r in self.reliefs]
        return m

    def panel(self, pid):
        return next((p for p in self.panels if p["id"] == pid), None)

    def bend_allowance(self, angle_deg, radius):
        return math.radians(angle_deg) * (radius + self.k * self.thickness)

    # ------------------------------------------------------------------ edges a flange can go on
    def panel_edges(self, panel):
        """Straight outline edges of a panel that aren't bent yet: [(a2, b2, outward2)] in its local 2D."""
        out = []
        face = panel["face"]
        used = [(np.asarray(b["a"]), np.asarray(b["b"])) for b in self.bends if b["panel"] == panel["id"]]
        # the edge this panel hangs from (its bend) isn't free either
        for e in K.edges(face):
            info = K.edge_info(e)
            if "p0" not in info:
                continue
            a, b = np.array(info["p0"][:2]), np.array(info["p1"][:2])
            if np.linalg.norm(b - a) < 1e-6:
                continue
            if panel.get("bend") is not None and abs(a[1]) < 1e-7 and abs(b[1]) < 1e-7:
                continue                       # its root edge (v = 0), where its own bend joins
            if any(_same_seg(a, b, ua, ub) for ua, ub in used):
                continue
            d = (b - a) / np.linalg.norm(b - a)
            n = np.array([-d[1], d[0]])
            mid = (a + b) / 2
            eps = max(1e-4, 1e-4 * np.linalg.norm(b - a))
            o = n if not _face_contains(face, mid + n * eps) else -n
            out.append((a, b, o))
        return out

    def find_edge(self, ref, scale):
        """The panel edge best matching a picked 3D edge (its centre / direction / length)."""
        c = np.asarray(ref.get("c"), float)
        d = np.asarray(ref.get("d") or [1, 0, 0], float)
        L = float(ref.get("len", 0))
        t = self.thickness
        best, best_s = None, 1e9
        for p in self.panels:
            for a, b, o in self.panel_edges(p):
                for z in (0.0, t):
                    A, B = apply(p["fold"], (a[0], a[1], z)), apply(p["fold"], (b[0], b[1], z))
                    m = (A + B) / 2
                    dd = (B - A) / max(np.linalg.norm(B - A), 1e-12)
                    s = np.linalg.norm(c - m) / scale + (1 - abs(float(dd @ d))) * 5
                    s += abs(np.linalg.norm(B - A) - L) / max(L, np.linalg.norm(B - A), 1e-9)
                    if s < best_s:
                        best, best_s = (p, a, b, o), s
        if best is None or best_s > 0.4:
            raise K.KernelError("Pick a straight edge along the side of a sheet metal panel")
        return best


def _same_seg(a, b, c, d, tol=1e-6):
    return (np.linalg.norm(a - c) < tol and np.linalg.norm(b - d) < tol) or \
        (np.linalg.norm(a - d) < tol and np.linalg.norm(b - c) < tol)


# --------------------------------------------------------------------------- building
def make_base(region_face, plane: K.Plane, thickness, radius, k, flip=False, relief="rect", relief_scale=1.5,
              with_history=False):
    """A flat sheet from a sketch region: returns (solid, model); with_history returns (solid, model,
    history) where the history starts at the region face (docs/reference/element-naming.md, I1.11)."""
    z = -plane.normal if flip else plane.normal
    M = frame(plane.origin, plane.xdir, z)
    Minv = np.linalg.inv(M)
    local, h_local = _moved(region_face, Minv, True)        # the region in the panel's own 2D coordinates
    local = K.faces(local)[0]
    prism, h_prism = _prism(local, (0, 0, thickness), True)
    solid, h_fold = _moved(prism, M, True)
    m = SheetModel(thickness, radius, k, relief, relief_scale)
    m.panels.append({"id": "P0", "face": local, "fold": M, "flat": M, "parent": None, "bend": None})
    out = K.solids(solid)[0] if K.solids(solid) else solid
    if with_history:
        return out, m, K._chain(h_local, h_prism, h_fold)
    return out, m


def add_flange(shape, model: SheetModel, panel, a, b, o, length, angle, radius=None, up=True,
               position="outside", fid="F", with_history=False):
    """Bend a new flange off panel edge a→b (local 2D, o = outward). length is measured from the outside of
    the bend (the virtual sharp). position: 'outside' = the bend starts at the edge (the part grows);
    'inside' = the flange's outer face lines up with the old edge (the outline stays the same size).
    Returns (new shape, new model). With with_history it returns (new shape, new model, history, seeds):
    the history runs from the old shape and the seeds (the bend's and the new panel's NamedShape, named by
    role and panel number, which a rebuild keeps) to the new shape (docs/reference/element-naming.md, I1.11)."""
    m = model.copy()
    steps = []
    panel = m.panel(panel["id"])
    t = m.thickness
    R = float(radius if radius is not None else m.radius)
    th = math.radians(float(angle))
    if not (0 < angle <= 180):
        raise K.KernelError("The bend angle must be between 0 and 180°")
    a, b, o = (np.asarray(x, float) for x in (a, b, o))
    setback = (R + t) * math.tan(th / 2) if angle < 179.999 else (R + t)
    flat_len = float(length) - setback
    if flat_len <= 1e-6:
        raise K.KernelError(f"The flange is too short for this bend — make it longer than {setback:.2f} mm")
    Mf = panel["fold"]
    if position == "inside":
        # trim the panel back so the flange's outer face sits where the edge was
        strip2 = [a, b, b - o * setback, a - o * setback]
        panel["face"] = _trim_face(panel["face"], strip2)
        box = _prism(_polygon_face([(q[0], q[1], -1e-3) for q in strip2]), (0, 0, t + 2e-3))
        shape, h = K.cut(shape, _moved(box, Mf), with_history=True)
        steps.append(h)
        a, b = a - o * setback, b - o * setback
    # run along the edge so that "left" of it is outward: then the new panel's local y points away from the bend
    dd = (b - a) / np.linalg.norm(b - a)
    if float(np.array([-dd[1], dd[0]]) @ o) < 0:
        a, b = b, a
    d = (b - a) / np.linalg.norm(b - a)
    w = float(np.linalg.norm(b - a))
    zv = np.array([0.0, 0.0, 1.0])
    o3 = np.array([o[0], o[1], 0.0])
    if up:
        k_ax = np.cross(o3, zv)
        Q = np.array([a[0], a[1], t + R])
    else:
        k_ax = np.cross(zv, o3)
        Q = np.array([a[0], a[1], -R])
    Rot = rot_about(Q, k_ax, th)
    a3 = np.array([a[0], a[1], 0.0])
    d3 = np.array([d[0], d[1], 0.0])
    # bend: the edge's cross-section swept about the bend axis (in the panel's local coordinates)
    sect = _polygon_face([a3, a3 + d3 * w, a3 + d3 * w + zv * t, a3 + zv * t])
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1
    bend_solid = BRepPrimAPI_MakeRevol(sect, gp_Ax1(K._pnt(Q), K._dir(k_ax)), th).Shape()
    # the new panel: local u along the edge, v away from the bend, same z side as its parent
    L_fold = frame(apply(Rot, a3), Rot[:3, :3] @ d3, Rot[:3, :3] @ zv)      # local y = the rotated outward
    ba = m.bend_allowance(angle, R)
    L_flat = frame(a3 + o3 * ba, d3, zv)
    child_face = _polygon_face([(0, 0, 0), (w, 0, 0), (w, flat_len, 0), (0, flat_len, 0)])
    slab = _moved(_prism(child_face, (0, 0, t)), L_fold)
    bend_tool, slab_tool = _moved(bend_solid, Mf), _moved(slab, Mf)
    seeds = [_seeded(bend_tool, fid, f"b{len(m.panels)}"), _seeded(slab_tool, fid, f"s{len(m.panels)}")]
    new, h = K.fuse(shape, bend_tool, with_history=True)
    steps.append(h)
    new, h = K.fuse(new, slab_tool, with_history=True)
    steps.append(h)
    cid = f"{fid}:{len(m.panels)}"
    m.panels.append({"id": cid, "face": child_face, "fold": Mf @ L_fold, "flat": panel["flat"] @ L_flat,
                     "parent": panel["id"], "bend": len(m.bends)})
    bend = {"id": f"B{len(m.bends) + 1}", "panel": panel["id"], "child": cid, "a": a.tolist(),
            "b": b.tolist(), "o": o.tolist(), "angle": float(angle), "radius": R, "up": bool(up),
            "ba": ba, "Q": Q.tolist(), "axis": k_ax.tolist(), "theta": th}
    m.bends.append(bend)
    # corner reliefs where this bend meets another one on the same panel
    if m.relief != "none":
        for other in m.bends[:-1]:
            if other["panel"] != panel["id"]:
                continue
            rel = _corner(m, bend, other, setback)
            if rel is not None:
                new = _cut_relief(new, m, panel, rel, (bend, other), steps)
                m.reliefs.append(rel)
    sols = K.solids(new)
    if len(sols) == 1:
        new, h = _unify(sols[0], True)
        steps.append(h)
    if with_history:
        return new, m, K._chain(*steps), seeds
    return new, m


def _seeded(tool, fid, role):
    """The starting map of a flange tool (its bend or its new panel): each element is named by the role of
    the tool and its place in it, which is the same on every rebuild because the tool is built the same way."""
    keys = N.NamedShape(tool, {}).el.keys()
    try:
        return N.seed(tool, fid, N.OP_SKETCH, {k: f"{role}{k[0].lower()}{k[1]}" for k in keys})
    except N.NamingError:
        return N.NamedShape(tool, {})


def add_hem(shape, model, panel, a, b, o, length, radius=None, down=False, fid="F", with_history=False):
    """A hem: the edge folded right back (180°) over the sheet. `length` is measured from the outside of the
    fold; a small radius (default half the thickness) gives a tight, rounded hem. with_history: as add_flange."""
    R = float(radius) if radius is not None else max(model.thickness * 0.5, 0.1)
    return add_flange(shape, model, panel, a, b, o, length, 180.0, R, not down, "outside", fid, with_history)


def add_jog(shape, model, panel, a, b, o, offset, length, radius=None, down=False, fid="F", with_history=False):
    """A jog (joggle): from an edge the sheet steps up (or down) by `offset` and carries on parallel to where it
    was for `length` — two opposite 90° bends. `offset` is the step between the two parallel panels' matching
    faces; `length` runs from the outside of the second bend.

    Returns (new shape, new model). With with_history it returns (new shape, new model, steps): one
    (shape, history, seeds) per flange, in order, each as add_flange gives them, for naming."""
    t = model.thickness
    R = float(radius) if radius is not None else model.radius
    first = float(offset) - t                                  # the step's flange, to the outside of its bends
    if first - 2 * (R + t) <= 1e-6:
        raise K.KernelError(f"The jog is too small for this bend radius — make the offset more than "
                            f"{2 * (R + t) + t:.2f} mm, or use a smaller radius")
    shape, m, h1, seeds1 = add_flange(shape, model, panel, a, b, o, first, 90.0, R, not down, "outside", fid, True)
    step1 = (shape, h1, seeds1)
    child = m.panels[-1]
    far = [(p, q, oo) for p, q, oo in m.panel_edges(child)
           if abs(p[1] - _panel_len(child)) < 1e-6 and abs(q[1] - _panel_len(child)) < 1e-6]
    if not far:
        raise K.KernelError("Couldn't find the edge for the second bend of the jog")
    p2, q2, o2 = far[0]
    shape, m, h2, seeds2 = add_flange(shape, m, child, p2, q2, o2, length, 90.0, R, down, "outside", fid + "j", True)
    return (shape, m, [step1, (shape, h2, seeds2)]) if with_history else (shape, m)


def _panel_len(panel):
    lo, hi = K.bbox(panel["face"])
    return float(hi[1])


def add_tab(shape, model, region_world, fid="F", with_history=False):
    """A tab: more sheet in the plane of an existing panel (from a sketch region on its face, touching it).
    Flanges can then go on the tab's edges, and it unfolds with its panel."""
    m = model.copy()
    t = m.thickness
    pl = K.planar_face_plane(region_world)
    if pl is None:
        raise K.KernelError("A tab comes from a flat sketch region")
    target = None
    for p in m.panels:
        Minv = np.linalg.inv(p["fold"])
        n_loc = Minv[:3, :3] @ pl.normal
        z = apply(Minv, pl.origin)[2]
        if abs(abs(n_loc[2]) - 1) < 1e-7 and (abs(z) < 1e-5 or abs(z - t) < 1e-5):
            local = _moved(region_world, Minv)
            if abs(z) > 1e-5:
                local = K.translate(local, (0, 0, -t))
            grown = _merge_faces(p["face"], K.faces(local)[0])
            if grown is not None:
                target = (p, local, grown)
                break
    if target is None:
        raise K.KernelError("Draw the tab on the face of a sheet metal panel, touching its edge")
    p, local, grown = target
    before = K.face_info(p["face"])["area"]
    if K.face_info(grown)["area"] - before <= 1e-6 * max(before, 1.0):
        raise K.KernelError("The tab is inside the sheet, so it adds nothing — a tab sticks out past the panel's "
                            "edge: draw it touching the edge and reaching outside the sheet")
    p["face"] = grown
    tool = _moved(_prism(K.faces(local)[0], (0, 0, t)), p["fold"])
    new, h = K.fuse(shape, tool, with_history=True)
    steps = [h]
    sols = K.solids(new)
    if len(sols) == 1:
        new, h = _unify(sols[0], True)
        steps.append(h)
    if with_history:        # (shape, model, history, seeds) as add_flange: the tab's prism is the one seeded tool
        return new, m, K._chain(*steps), [_seeded(tool, fid, f"t{p['id']}")]
    return new, m


def _merge_faces(f1, f2):
    """Two touching flat faces in z = 0 as one face (None if they don't touch)."""
    a, b = _prism(f1, (0, 0, 1.0)), _prism(f2, (0, 0, 1.0))
    if not K.touches_or_intersects(a, b):
        return None
    u = _unify(K.fuse(a, b))
    if len(K.solids(u)) != 1:
        return None
    for f in K.faces(u):
        pl = K.planar_face_plane(f)
        if pl is not None and abs(abs(pl.normal[2]) - 1) < 1e-9 and abs(pl.origin[2]) < 1e-9:
            return f
    return None


def convert_plate(shape, face, radius, k, relief="rect", relief_scale=1.5, with_history=False):
    """A flat plate (any outline, with holes) made into a sheet metal part, so flanges can be added and it can be
    unfolded and nested. `face` is one of its two big flat faces. Returns (solid, model); with_history returns
    (solid, model, history) as make_base does, the history starting at `face`."""
    pl = K.planar_face_plane(face)
    if pl is None:
        raise K.KernelError("Pick one of the plate's big flat faces")
    n = np.asarray(pl.normal, float)
    lo, hi = K.bbox(shape)
    c = K.mass_props(shape)["centroid"]
    into = n if float((c - pl.origin) @ n) > 0 else -n            # from the picked face into the material
    pts = np.array(K._vertices_np(shape))
    t = float(np.max((pts - pl.origin) @ into))
    area = K.face_info(face)["area"]
    if t <= 1e-6 or abs(K.volume(shape) - area * t) > 1e-3 * K.volume(shape) + 1e-6:
        raise K.KernelError("This part isn't a flat plate of even thickness (its volume isn't the face area × "
                            "the thickness), so it can't be made sheet metal")
    plane = K.Plane(np.asarray(pl.origin, float), np.asarray(pl.xdir, float), into)
    return make_base(face, plane, t, radius, k, False, relief, relief_scale, with_history)


def _line_hit(a1, d1, a2, d2):
    """Where two 2D lines meet (None if parallel)."""
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / den
    return a1 + d1 * t


def _corner(m, b1, b2, setback):
    """If bends b1 and b2 meet at a corner of their panel, the corner relief for them (else None)."""
    a1, e1 = np.array(b1["a"]), np.array(b1["b"])
    a2, e2 = np.array(b2["a"]), np.array(b2["b"])
    d1 = (e1 - a1) / np.linalg.norm(e1 - a1)
    d2 = (e2 - a2) / np.linalg.norm(e2 - a2)
    c = _line_hit(a1, d1, a2, d2)
    if c is None:
        return None
    s = m.relief_scale * m.thickness
    near = setback + max(b1["ba"], b2["ba"]) + s + 1e-6
    if min(np.linalg.norm(c - a1), np.linalg.norm(c - e1)) > near or \
            min(np.linalg.norm(c - a2), np.linalg.norm(c - e2)) > near:
        return None
    return {"panel": b1["panel"], "c": c.tolist(), "o1": list(b1["o"]), "o2": list(b2["o"]),
            "e1": float(b1["ba"]), "e2": float(b2["ba"]), "s": float(s), "bends": [b1["id"], b2["id"]]}


def _relief_square(rel, z0, z1):
    """The notch in its panel's 2D coordinates: from s inside each bend line out to the far side of each bend."""
    c, o1, o2 = np.array(rel["c"]), np.array(rel["o1"]), np.array(rel["o2"])
    s = rel["s"]
    pts = [c + o1 * x + o2 * y for x, y in ((-s, -s), (rel["e1"], -s), (rel["e1"], rel["e2"]), (-s, rel["e2"]))]
    return _prism(_polygon_face([(q[0], q[1], z0) for q in pts]), (0, 0, z1 - z0))


def _cut_relief(shape, m, panel, rel, bends, steps=None):
    """Cut the notch out of the folded part: the panel's corner, and the end of each bend next to it.
    Each cut's history is appended to `steps` when a list is given."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1
    t = m.thickness
    eps = 1e-3 * max(t, 1.0)
    tools = [_relief_square(rel, -eps, t + eps)]
    c = np.array(rel["c"])
    for bd in bends:
        other_o = np.array(rel["o2"] if bd["id"] == rel["bends"][0] else rel["o1"])
        a, b = np.array(bd["a"]), np.array(bd["b"])
        L = float(np.linalg.norm(b - a))
        d = (b - a) / L
        # the part of this bend's edge within s of the other bend's line (on the panel side) and beyond it
        k0 = float(d @ other_o)
        if abs(k0) < 1e-9:
            continue
        t_at = float((c - a) @ other_o) / k0 - rel["s"] / k0       # where (p - c)·o_other = -s
        lo_, hi_ = (t_at, L + 1.0) if k0 > 0 else (-1.0, t_at)
        lo_, hi_ = max(lo_, -1.0), min(hi_, L + 1.0)
        if hi_ - lo_ <= 1e-9:
            continue
        p0, p1 = a + d * lo_, a + d * hi_
        sect = _polygon_face([(p0[0], p0[1], -eps), (p1[0], p1[1], -eps), (p1[0], p1[1], t + eps),
                              (p0[0], p0[1], t + eps)])
        piece = BRepPrimAPI_MakeRevol(sect, gp_Ax1(K._pnt(bd["Q"]), K._dir(bd["axis"])),
                                      bd["theta"] + 1e-6).Shape()
        tools.append(piece)
    for tool in tools:
        if steps is None:
            shape = K.cut(shape, _moved(tool, panel["fold"]))
        else:
            shape, h = K.cut(shape, _moved(tool, panel["fold"]), with_history=True)
            steps.append(h)
    return shape


def _trim_face(face, strip2):
    """A panel outline with a strip cut off along one edge."""
    tool = _prism(_polygon_face([(q[0], q[1], -1.0) for q in strip2]), (0, 0, 2.0))
    return _single_face(K.cut(_prism(face, (0, 0, 0.5)), tool), bottom=True)


def _single_face(shape, bottom=False):
    """The z = 0 face of a thin slab (or the only face of a face-shaped result)."""
    fs = K.faces(shape)
    if bottom:
        for f in fs:
            pl = K.planar_face_plane(f)
            if pl is not None and abs(abs(pl.normal[2]) - 1) < 1e-9 and abs(pl.origin[2]) < 1e-9:
                return f
    return fs[0]


# --------------------------------------------------------------------------- flat pattern
FLAT_TAG = "FLAT"      # the tag of every flat pattern name: an unfold belongs to no feature of the document


class FlatPattern:
    def __init__(self, face, bend_lines, M_base, thickness):
        self.face = face                  # the flat sheet (in world space, in the base panel's plane)
        self.bend_lines = bend_lines      # [(p0_world, p1_world, label)]
        self.M_base = M_base              # base panel frame: world -> 2D via its inverse
        self.thickness = thickness
        # naming.NamedShape of solid(): each face takes its name from its folded counterpart (None when
        # flat_pattern was not given the part's map)
        self.named = None

    def solid(self):
        if getattr(self, "_solid", None) is not None:
            return self._solid
        z = self.M_base[:3, 2] * self.thickness
        return _prism(self.face, z)

    def to2d(self, p):
        q = apply(np.linalg.inv(self.M_base), p)
        return float(q[0]), float(q[1])


def flat_pattern(shape, m: SheetModel, named=None):
    """Unfold: each panel's face on its z = 0 side goes to its flat place; each bend becomes a strip of its
    bend allowance. Cuts made in panels come along.

    `named` is the NamedShape of `shape` (`state.named(part_id)`). With it, the flat pattern's faces are named
    from the folded faces they came from and `FlatPattern.named` is the map of `solid()`
    (docs/reference/element-naming.md, I1.11). A bend strip has no folded counterpart, so it stays unnamed."""
    want = named is not None and named.shape is not None and named.shape.IsSame(shape)
    steps = []
    faces = []
    tol = 1e-5 * (1 + float(np.linalg.norm(K.bbox(shape)[1] - K.bbox(shape)[0])))
    all_faces = K.faces(shape)
    for p in m.panels:
        Minv = np.linalg.inv(p["fold"])
        to_flat = p["flat"] @ Minv
        for f in all_faces:
            pl = K.planar_face_plane(f)
            if pl is None:
                continue
            # on this panel's z = 0 plane?
            o_loc = apply(Minv, pl.origin)
            n_loc = Minv[:3, :3] @ pl.normal
            if abs(abs(n_loc[2]) - 1) > 1e-7 or abs(o_loc[2]) > tol:
                continue
            c = apply(Minv, _inside_point(f))
            if not _face_contains(p["face"], (c[0], c[1]), tol=max(tol, 1e-4)):
                continue
            if want:
                g, h = _moved(f, to_flat, True)
                steps.append(h)
                faces.append(g)
            else:
                faces.append(_moved(f, to_flat))
    bend_lines = []
    for bd in m.bends:
        parent = m.panel(bd["panel"])
        a, b, o = np.array(bd["a"]), np.array(bd["b"]), np.array(bd["o"])
        ba = bd["ba"]
        strip = [(a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0] + o[0] * ba, b[1] + o[1] * ba, 0.0),
                 (a[0] + o[0] * ba, a[1] + o[1] * ba, 0.0)]
        faces.append(_moved(_polygon_face(strip), parent["flat"]))
        mid_a = (a + o * ba / 2)
        mid_b = (b + o * ba / 2)
        label = f"{'UP' if bd['up'] else 'DOWN'} {bd['angle']:g}° R{bd['radius']:g}"
        bend_lines.append((apply(parent["flat"], (mid_a[0], mid_a[1], 0)),
                           apply(parent["flat"], (mid_b[0], mid_b[1], 0)), label))
    if not faces:
        raise K.KernelError("Couldn't unfold this part")
    # join everything as thin slabs (solids merge far more reliably than loose faces), then keep the bottom
    base = m.panels[0]
    zdir = base["flat"][:3, 2] * m.thickness
    prisms = []
    for f in faces:
        if want:
            pr, h = _prism(f, zdir, True)
            steps.append(h)
            prisms.append(pr)
        else:
            prisms.append(_prism(f, zdir))
    if want:
        slab, h = K.fuse_all(prisms, with_history=True)
        steps.append(h)
    else:
        slab = K.fuse_all(prisms)
    for rel in m.reliefs:
        parent = m.panel(rel["panel"])
        tool = _relief_square(rel, -1.0, 1.0 + m.thickness)
        if want:
            slab, h = K.cut(slab, _moved(tool, parent["flat"]), with_history=True)
            steps.append(h)
        else:
            slab = K.cut(slab, _moved(tool, parent["flat"]))
    if want:
        slab, h = _unify(slab, True)
        steps.append(h)
    else:
        slab = _unify(slab)
    Binv = np.linalg.inv(base["flat"])
    bottom = []
    for f in K.faces(slab):
        pl = K.planar_face_plane(f)
        if pl is None:
            continue
        o_loc = apply(Binv, pl.origin)
        n_loc = Binv[:3, :3] @ pl.normal
        if abs(abs(n_loc[2]) - 1) < 1e-7 and abs(o_loc[2]) < 1e-6:
            bottom.append(f)
    if not bottom:
        raise K.KernelError("Couldn't unfold this part")
    sheet = bottom[0] if len(bottom) == 1 else K.compound(bottom)
    fp = FlatPattern(sheet, bend_lines, base["flat"], m.thickness)
    # a clean solid straight from the outline (no seams left over from joining the strips)
    if want:
        outs = [_prism(f, zdir, True) for f in bottom]
        steps.extend(o[1] for o in outs)
        fp._solid = outs[0][0]
        if len(outs) > 1:
            fp._solid, h = K.fuse_all([o[0] for o in outs], with_history=True)
            steps.append(h)
        try:
            fp.named = N.name_result(fp._solid, K._chain(*steps), [named], FLAT_TAG, N.OP_FOR["flat_pattern"])
        except N.NamingError:
            fp.named = N.NamedShape(fp._solid, {})
    else:
        fp._solid = K.fuse_all([_prism(f, zdir) for f in bottom]) if len(bottom) > 1 else _prism(bottom[0], zdir)
    return fp


def flat_entities(fp: FlatPattern, face=None):
    """The flat pattern's cut outline as 2D drawing entities: (lines, circles, arcs, polylines, bends).
    `face` replaces the outline (e.g. one grown for kerf); bend lines stay where they are."""
    lines, circles, arcs, polys = [], [], [], []
    for e in K.edges(face if face is not None else fp.face):
        info = K.edge_info(e)
        if "p0" in info:
            lines.append((fp.to2d(info["p0"]), fp.to2d(info["p1"])))
            continue
        pts = K.discretize_edge(e, 0.005)
        if "r" in info and len(pts) >= 2:
            c = fp.to2d(info["center"])
            p0, p1 = fp.to2d(pts[0]), fp.to2d(pts[-1])
            if np.linalg.norm(np.subtract(p0, p1)) < 1e-6:
                circles.append((c, info["r"]))
                continue
            mid = fp.to2d(pts[len(pts) // 2])
            a0 = math.degrees(math.atan2(p0[1] - c[1], p0[0] - c[0]))
            a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0]))
            am = math.degrees(math.atan2(mid[1] - c[1], mid[0] - c[0]))
            # counter-clockwise from a0 to a1 must pass through the middle point
            if not ((am - a0) % 360 < (a1 - a0) % 360):
                a0, a1 = a1, a0
            arcs.append((c, info["r"], a0, a1))
            continue
        polys.append([fp.to2d(q) for q in pts])
    bends = [(fp.to2d(p0), fp.to2d(p1), lab) for p0, p1, lab in fp.bend_lines]
    return lines, circles, arcs, polys, bends


def flat_dxf(fp: FlatPattern, path, name=""):
    """The flat pattern as a DXF for laser / plasma / waterjet cutting: the outline (layer CUT), bend lines
    (layer BEND, dashed) with their direction, angle and radius."""
    lines, circles, arcs, polys, bends = flat_entities(fp)
    try:
        import ezdxf
        doc = ezdxf.new("R2010", setup=False)
        doc.units = ezdxf.units.MM
        doc.header["$MEASUREMENT"] = 1
        doc.linetypes.add("DASHED", pattern=[4.5, 3.0, -1.5], description="Dashed __ __ __")
        doc.styles.get("Standard").dxf.font = "arial.ttf"
        doc.layers.add("CUT", color=7)
        doc.layers.add("BEND", color=1, linetype="DASHED")
        doc.layers.add("NOTES", color=3)
        msp = doc.modelspace()
        for p, q in lines:
            msp.add_line(p, q, dxfattribs={"layer": "CUT"})
        for c, r in circles:
            msp.add_circle(c, r, dxfattribs={"layer": "CUT"})
        for c, r, a0, a1 in arcs:
            msp.add_arc(c, r, a0, a1, dxfattribs={"layer": "CUT"})
        for pts in polys:
            msp.add_lwpolyline(pts, dxfattribs={"layer": "CUT"})
        from ezdxf.enums import TextEntityAlignment
        for p, q, lab in bends:
            msp.add_line(p, q, dxfattribs={"layer": "BEND"})
            ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            if ang > 90 or ang <= -90:
                ang -= 180 if ang > 0 else -180
            t = msp.add_text(lab, height=2.5, rotation=ang, dxfattribs={"layer": "NOTES"})
            t.set_placement(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), align=TextEntityAlignment.BOTTOM_CENTER)
        doc.saveas(path)
    except ImportError:
        from .dxf import DxfWriter
        w = DxfWriter()
        w.layer("CUT", 7)
        w.layer("BEND", 1, "DASHED")
        w.layer("NOTES", 3)
        for p, q in lines:
            w.line("CUT", p, q)
        for c, r in circles:
            w.circle("CUT", c, r)
        for c, r, a0, a1 in arcs:
            w.arc("CUT", c, r, a0, a1)
        for pts in polys:
            w.polyline("CUT", pts)
        for p, q, lab in bends:
            w.line("BEND", p, q)
            ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            w.text("NOTES", ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), lab, 2.5, ang, "center", "bottom")
        lo, hi = K.bbox(fp.face)
        w.save(path, fp.to2d(lo), fp.to2d(hi))
    return len(lines) + len(circles) + len(arcs) + len(polys), len(bends)
