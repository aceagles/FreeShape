"""Geometry kernel: thin, robust wrappers around OpenCascade (OCP).

Everything that touches B-rep geometry lives here so the rest of the app
works with plain Python/numpy data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from OCP.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax1, gp_Ax2, gp_Ax3, gp_Pln, gp_Circ, gp_Trsf
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common, BRepAlgoAPI_Splitter
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet, BRepFilletAPI_MakeChamfer
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GCPnts import GCPnts_QuasiUniformDeflection, GCPnts_TangentialDeflection
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Line, GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_C1, GeomAbs_Cone, GeomAbs_Torus
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID, TopAbs_REVERSED, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shell
try:
    from OCP.TopTools import TopTools_ListOfShape, TopTools_IndexedMapOfShape
except ImportError:  # OCP >= 8 exposes NCollection templates directly
    from OCP.OCP.collections import (List_TopoDS_Shape as TopTools_ListOfShape,
                                     IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape)
from OCP.TopLoc import TopLoc_Location
from OCP.BRep import BRep_Builder
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRepTools import BRepTools_History
from OCP.STEPControl import STEPControl_Writer, STEPControl_Reader, STEPControl_AsIs
from OCP.StlAPI import StlAPI_Writer
from OCP.IFSelect import IFSelect_RetDone
from OCP.BRepCheck import BRepCheck_Analyzer


# OCP renamed the static casts between releases (TopoDS.Face_s -> TopoDS.Face); support both.
def _caster(name):
    fn = getattr(TopoDS, name + "_s", None) or getattr(TopoDS, name)
    return fn


_as_face = _caster("Face")
_as_edge = _caster("Edge")
_as_solid = _caster("Solid")
_as_vertex = _caster("Vertex")
_as_wire = _caster("Wire")
_as_shell = _caster("Shell")


class KernelError(Exception):
    """A geometry operation failed (shown to the user as a red feature)."""


# --------------------------------------------------------------------------- planes

@dataclass
class Plane:
    """A sketch / datum plane: origin plus orthonormal x, y, normal axes."""
    origin: np.ndarray
    xdir: np.ndarray
    normal: np.ndarray

    @property
    def ydir(self) -> np.ndarray:
        return np.cross(self.normal, self.xdir)

    def to_world(self, u, v) -> np.ndarray:
        return self.origin + u * self.xdir + v * self.ydir

    def to_local(self, p) -> tuple[float, float]:
        d = np.asarray(p, float) - self.origin
        return float(d @ self.xdir), float(d @ self.ydir)

    def gp_ax3(self) -> gp_Ax3:
        return gp_Ax3(_pnt(self.origin), _dir(self.normal), _dir(self.xdir))

    def offset(self, dist: float, flip: bool = False) -> "Plane":
        n = -self.normal if flip else self.normal
        xd = self.xdir if not flip else self.xdir
        return Plane(self.origin + dist * self.normal, xd.copy(), n.copy())

    def to_dict(self):
        return {"o": self.origin.tolist(), "x": self.xdir.tolist(), "n": self.normal.tolist()}

    @staticmethod
    def from_dict(d):
        return Plane(np.array(d["o"], float), np.array(d["x"], float), np.array(d["n"], float))


def std_plane(name: str) -> Plane:
    """Onshape's default planes. Top = XY, Front = XZ, Right = YZ (Z up)."""
    o = np.zeros(3)
    if name == "Top":
        return Plane(o, np.array([1.0, 0, 0]), np.array([0, 0, 1.0]))
    if name == "Front":
        return Plane(o, np.array([1.0, 0, 0]), np.array([0, -1.0, 0]))
    if name == "Right":
        return Plane(o, np.array([0, 1.0, 0]), np.array([1.0, 0, 0]))
    raise KeyError(name)


def _pnt(p) -> gp_Pnt:
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def _dir(v) -> gp_Dir:
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _vec(v) -> gp_Vec:
    return gp_Vec(float(v[0]), float(v[1]), float(v[2]))


def _np(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()])


# --------------------------------------------------------------------------- topology helpers

def faces(shape):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return [_as_face(m.FindKey(i)) for i in range(1, m.Size() + 1)]


def edges(shape):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    return [_as_edge(m.FindKey(i)) for i in range(1, m.Size() + 1)]


def solids(shape):
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        out.append(_as_solid(exp.Current()))
        exp.Next()
    return out


def _loose(shape, what, inside):
    """Sub-shapes of kind `what` that lie outside every shape of kind `inside`."""
    out = []
    exp = TopExp_Explorer(shape, what, inside)
    while exp.More():
        out.append(exp.Current())
        exp.Next()
    return out


def bodies(shape):
    """The bodies of a shape: its solids, then each shell outside a solid, then each face outside a shell
    wrapped in a shell of its own. A body with no solid is a surface body (`is_surface`)."""
    from OCP.TopAbs import TopAbs_SHELL
    out = list(solids(shape))
    out += [_as_shell(s) for s in _loose(shape, TopAbs_SHELL, TopAbs_SOLID)]
    for f in _loose(shape, TopAbs_FACE, TopAbs_SHELL):
        b = BRep_Builder()
        sh = TopoDS_Shell()
        b.MakeShell(sh)
        b.Add(sh, f)
        out.append(sh)
    return out


def is_surface(shape) -> bool:
    """True for a shape that has faces and no solid: a surface body."""
    return not solids(shape) and bool(faces(shape))


def as_surface(shapes, tol=1e-6):
    """Sew faces / shells into surface bodies: one shell per connected set of faces (a list of shells)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    sew = BRepBuilderAPI_Sewing(tol)
    for s in shapes:
        sew.Add(s)
    sew.Perform()
    return bodies(sew.SewedShape())


def _solids_only(shape):
    """`shape` itself, or a compound of just its solids when a shell or face outside them would add to its
    volume integrals."""
    from OCP.TopAbs import TopAbs_SHELL
    if _loose(shape, TopAbs_SHELL, TopAbs_SOLID) or _loose(shape, TopAbs_FACE, TopAbs_SHELL):
        return compound(solids(shape))
    return shape


def compound(shapes):
    b = BRep_Builder()
    c = TopoDS_Compound()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


def _list(shapes) -> TopTools_ListOfShape:
    lst = TopTools_ListOfShape()
    for s in shapes:
        lst.Append(s)
    return lst


def bbox(shape):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, False)
    if b.IsVoid():
        return np.zeros(3), np.zeros(3)
    return _np(b.CornerMin()), _np(b.CornerMax())


def is_valid(shape) -> bool:
    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False


def _count(shape, kind) -> int:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return m.Size()


def _topology(shape):
    """(faces, edges) — enough to tell whether a unify merged anything."""
    return _count(shape, TopAbs_FACE), _count(shape, TopAbs_EDGE)


def _unify(shape, pins=()):
    """ShapeUpgrade_UnifySameDomain (faces, edges, concatenated b-splines). A pinned edge forbids merging the
    faces either side of it; a pinned vertex forbids merging the edges that meet there."""
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    for p in pins:
        u.KeepShape(p)
    u.Build()
    return u


def _invalid_faces(shape):
    an = BRepCheck_Analyzer(shape)
    if an.IsValid():
        return []
    return [f for f in faces(shape) if not an.IsValid(f)]


def _unify_pins(u, shape, bad):
    """Every edge and vertex of the input faces that unify turned into one of the `bad` output faces."""
    pins = []
    for f in faces(shape):
        if any(any(m.IsSame(b) for b in bad) for m in u.History().Modified(f)):
            pins += edges(f)
            pins += [_as_vertex(v) for v in _explore(f, TopAbs_VERTEX)]
    return pins


def _explore(shape, kind):
    exp = TopExp_Explorer(shape, kind)
    while exp.More():
        yield exp.Current()
        exp.Next()


# --------------------------------------------------------------------------- operation history
#
# Element names are built from the history of the operation that made the shape, so the helpers
# below can hand that history back next to their result (docs/reference/element-naming.md § 3).
# It is always an OCCT BRepTools_History and consecutive steps are chained with that class's own
# Merge(): composing histories by hand is where the split and merge cases go wrong.

_HISTORY_KINDS = (TopAbs_SOLID, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX)


def _sub_shapes(shape, kind):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, m)
    return [m.FindKey(i) for i in range(1, m.Size() + 1)]


def _maker_images(hist, maker, s, caps):
    """What `maker` did to the one sub-shape `s`, recorded in `hist`."""
    try:
        if maker.IsDeleted(s):
            hist.Remove(s)
            return
        for g in maker.Generated(s):
            hist.AddGenerated(s, g)
        for m in maker.Modified(s):
            hist.AddModified(s, m)
        if caps:
            for cap in (maker.FirstShape(s), maker.LastShape(s)):
                if not cap.IsNull() and not cap.IsSame(s):
                    hist.AddGenerated(s, cap)
    except Exception:
        pass            # an OCCT history failure degrades the names; it never fails the operation


def _maker_history(maker, inputs, caps=False) -> BRepTools_History:
    """One operation's history, as OCCT's own class.

    A maker that keeps a BRepTools_History of its own (the boolean builders, UnifySameDomain, a
    ReShape) is asked for it. The others answer per sub-shape only, so their history is filled from
    Generated(), Modified() and IsDeleted() over every sub-shape of the `inputs` — the same two
    queries FreeCAD's MapperMaker reads, with MapperHistory's class as the carrier. caps=True also
    records a sweep's start and end shapes (FirstShape/LastShape), without which an extrusion's top
    face and top edges would come from nothing.
    """
    own = getattr(maker, "History", None)
    if own is not None:
        try:
            h = own()
            if h is not None:
                return h
        except Exception:
            pass
    hist = BRepTools_History()
    for src in inputs:
        for kind in _HISTORY_KINDS:
            for s in _sub_shapes(src, kind):
                _maker_images(hist, maker, s, caps)
    return hist


def _chain(*histories) -> BRepTools_History:
    """The given histories composed in order: each one maps the shapes the step before it made.

    Merging a history into an empty one copies it, and a step that knows nothing about a shape
    leaves it alone, so this also unions histories of independent steps (one per extruded profile).
    """
    out = BRepTools_History()
    for h in histories:
        if h is not None:
            out.Merge(h)
    return out


def clean(shape, with_history=False):
    """Merge coplanar faces / collinear edges left behind by booleans.

    UnifySameDomain rebuilds the wires of every face it touches from vertex connectivity. A face whose hole wire
    shares a vertex with its outer wire (a hole tangent to a cylinder's seam, as in FreeCAD's cylinder7_V2 and
    cylinderSeam3 refine tests) comes back as a single figure-of-eight wire and BRepCheck calls the face
    unorientable, so the merge is checked whenever it changed the topology. Faces that broke are redone with
    their source faces pinned (nothing else changes); if that still fails the un-merged input is returned. A
    valid input never comes out invalid, and a result that is valid today is returned as before.

    with_history=True also returns the unify step's history (empty when the input is returned
    un-merged, which is the identity)."""
    def done(out, u):
        if not with_history:
            return out
        return out, (_maker_history(u, [shape]) if u is not None else BRepTools_History())
    try:
        u = _unify(shape)
        out = u.Shape()
        if _topology(out) == _topology(shape):
            return done(out, u)            # nothing merged, so no face was rebuilt
        bad = _invalid_faces(out)
        if not bad or not is_valid(shape):
            return done(out, u)            # valid, or no worse than what came in
        u = _unify(shape, _unify_pins(u, shape, bad))
        out = u.Shape()
        return done(out, u) if is_valid(out) else done(shape, None)
    except Exception:
        return done(shape, None)


# --------------------------------------------------------------------------- measurements / signatures

_VOLUME_CACHE = {}


def _has_freeform(shape) -> bool:
    from OCP.GeomAbs import GeomAbs_BSplineSurface, GeomAbs_BezierSurface, GeomAbs_SurfaceOfExtrusion
    for f in faces(shape):
        if BRepAdaptor_Surface(f).GetType() in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
            return True
    return False


def _gk_shape(shape):
    """`shape` as the adaptive volume integrator should get it: itself, or a compound of it and one free vertex.

    OpenCascade sums each face's volume about one reference point, the mean of the shape's vertex positions
    (roughBaryCenter in BRepGProp.cxx: every occurrence counts). When that point sits where a face's
    contribution is exactly zero or the shape is symmetric about it, the integrator computes rounding noise
    for that face and its stopping test, relative to that value, is never met, so it refines for seconds to
    minutes. Planes through the point (30 flat strips of a bevel gear) are one case; a hex recess in a screw
    head, whose vertex mean lies on the plane y = 0, is another (ISO 10642 M4x12: 5.2 s, ISO 4762: 2.1 s, both
    0.00 s once the point is off the symmetry planes). The extra vertex moves the mean by a fixed generic
    offset; a closed solid's volume and centre of mass do not depend on the reference point. A polyhedron with
    no plane through the point is left alone: the move adds rounding noise (1e-16) to its centre of mass."""
    pts = []
    exp = TopExp_Explorer(shape, TopAbs_VERTEX)
    while exp.More():
        pts.append(_np(BRep_Tool.Pnt_s(_as_vertex(exp.Current()))))
        exp.Next()
    if not pts:
        return shape
    pts = np.array(pts)
    ref, size = pts.mean(axis=0), float(np.linalg.norm(np.ptp(pts, axis=0)))
    surfaces = [BRepAdaptor_Surface(f) for f in faces(shape)]
    planes = [ad.Plane() for ad in surfaces if ad.GetType() == GeomAbs_Plane]

    def degenerate(p):
        p = gp_Pnt(*p)
        return any(pl.Distance(p) < 1e-6 * size for pl in planes)

    # A polyhedron is moved only when a plane passes through the point; any curved face is always moved.
    if size <= 0 or (len(planes) == len(surfaces) and not degenerate(ref)):
        return shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    for k in range(1, 9):
        shift = k * size * np.array([0.0371, 0.0263, 0.0197])
        if not degenerate(ref + shift):
            far = ref + shift * (len(pts) + 1)          # one more point in the mean moves it by `shift`
            return compound([shape, BRepBuilderAPI_MakeVertex(gp_Pnt(*far)).Vertex()])
    return shape


def volume(shape) -> float:
    """Exact volume. Uses the adaptive (Gauss-Kronrod) integrator, which is right on swept and ruled
    surfaces such as threads where the quick method can be badly wrong. Free-form (e.g. thread)
    surfaces use a relative accuracy of 1e-4 — still 4+ significant figures — because the full
    accuracy is slow there. Results are cached per shape."""
    if not solids(shape):
        return 0.0                  # a surface body (or any shape with no solid) encloses no volume
    shape = _solids_only(shape)
    key = hash(shape)
    hit = _VOLUME_CACHE.get(key)
    if hit is not None and hit[0].IsSame(shape) and hit[0].IsEqual(shape):
        return hit[1]
    g = GProp_GProps()
    BRepGProp.VolumePropertiesGK_s(_gk_shape(shape), g, 1e-4 if _has_freeform(shape) else 1e-6)
    v = g.Mass()
    if len(_VOLUME_CACHE) > 2000:
        _VOLUME_CACHE.clear()
    _VOLUME_CACHE[key] = (shape, v)
    return v


def rough_volume(shape) -> float:
    """Volume to about 1% — fast even on thread surfaces. For sanity checks only."""
    g = GProp_GProps()
    BRepGProp.VolumePropertiesGK_s(shape, g, 0.1)
    return g.Mass()


def mass_props(shape):
    if not solids(shape):           # a surface body: no volume; the centroid is the area centroid
        sa = GProp_GProps()
        BRepGProp.SurfaceProperties_s(shape, sa)
        return {"volume": 0.0, "area": sa.Mass(), "centroid": _np(sa.CentreOfMass())}
    shape = _solids_only(shape)
    g = GProp_GProps()
    # CGFlag: the adaptive integrator only computes the centre of mass when asked to
    BRepGProp.VolumePropertiesGK_s(_gk_shape(shape), g, 1e-4 if _has_freeform(shape) else 1e-6, False, False, True)
    sa = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sa)
    return {"volume": g.Mass(), "area": sa.Mass(), "centroid": _np(g.CentreOfMass())}


def face_info(face) -> dict:
    """A geometric signature of a face, used to re-find it after a rebuild."""
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    ad = BRepAdaptor_Surface(face)
    st = ad.GetType()
    info = {"kind": "face", "area": g.Mass(), "c": _np(g.CentreOfMass()).tolist(), "type": int(st)}
    if st == GeomAbs_Plane:
        pl = ad.Plane()
        n = _np(pl.Axis().Direction())
        if face.Orientation() == TopAbs_REVERSED:
            n = -n
        info["n"] = n.tolist()
    elif st == GeomAbs_Cylinder:
        cy = ad.Cylinder()
        info["r"] = cy.Radius()
        info["axis"] = _np(cy.Axis().Direction()).tolist()
        info["axis_p"] = _np(cy.Axis().Location()).tolist()
    elif st == GeomAbs_Cone:
        ax = ad.Cone().Axis()
        info["axis"] = _np(ax.Direction()).tolist()
        info["axis_p"] = _np(ax.Location()).tolist()
    elif st == GeomAbs_Torus:
        ax = ad.Torus().Axis()
        info["axis"] = _np(ax.Direction()).tolist()
        info["axis_p"] = _np(ax.Location()).tolist()
    return info


def face_axis(face):
    """(point, unit direction, (t0, t1)) of a cylindrical, conical or toroidal face: the surface's own axis
    and the stretch of it, along the direction from the point, that the face covers. None for any other face."""
    ad = BRepAdaptor_Surface(face)
    st = ad.GetType()
    if st not in (GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Torus):
        return None
    ax = {GeomAbs_Cylinder: lambda: ad.Cylinder().Axis(), GeomAbs_Cone: lambda: ad.Cone().Axis(),
          GeomAbs_Torus: lambda: ad.Torus().Axis()}[st]()
    o, d = _np(ax.Location()), _np(ax.Direction())
    if st == GeomAbs_Torus:
        pts = _bbox_corners(face)
    else:
        from OCP.BRepTools import BRepTools
        _, _, v0, v1 = BRepTools.UVBounds_s(face)
        k = math.cos(ad.Cone().SemiAngle()) if st == GeomAbs_Cone else 1.0   # v runs along the generatrix
        t = sorted((v0 * k, v1 * k))
        return o, d, (float(t[0]), float(t[1]))
    ts = [float((c - o) @ d) for c in pts]
    return o, d, (min(ts), max(ts))


def _bbox_corners(shape):
    lo, hi = bbox(shape)
    return [np.array([x, y, z]) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def face_outward_normal(face):
    """The unit normal of a face at its middle, pointing out of the material.

    `face_info(face)["n"]` is the plane axis flipped for a reversed face. That is the outward normal only when
    the plane's coordinate system is right-handed; on a left-handed plane (some STEP-imported and revolved
    faces) it points into the part. This takes the surface's own normal and flips it for a reversed face."""
    from OCP.BRepLProp import BRepLProp_SLProps
    ad = BRepAdaptor_Surface(face)
    u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
    v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
    n = _np(BRepLProp_SLProps(ad, u, v, 1, 1e-7).Normal())
    n = n / np.linalg.norm(n)
    return -n if face.Orientation() == TopAbs_REVERSED else n


def face_loops(face) -> int:
    from OCP.TopAbs import TopAbs_WIRE
    n = 0
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        n += 1
        exp.Next()
    return n


def edge_info(edge) -> dict:
    g = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, g)
    ad = BRepAdaptor_Curve(edge)
    ct = ad.GetType()
    info = {"kind": "edge", "len": g.Mass(), "c": _np(g.CentreOfMass()).tolist(), "type": int(ct)}
    if ct == GeomAbs_Line:
        info["d"] = _np(ad.Line().Direction()).tolist()
        info["p0"] = _np(ad.Value(ad.FirstParameter())).tolist()
        info["p1"] = _np(ad.Value(ad.LastParameter())).tolist()
    elif ct == GeomAbs_Circle:
        ci = ad.Circle()
        info["r"] = ci.Radius()
        info["center"] = _np(ci.Location()).tolist()
        info["axis"] = _np(ci.Axis().Direction()).tolist()
    return info


def edge_closest(edge, pt):
    """(point on the edge nearest `pt`, unit tangent of the edge there)."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Vec
    ext = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(_pnt(pt)).Vertex(), edge)
    if not ext.IsDone() or ext.NbSolution() < 1:
        raise KernelError("Couldn't find that point on the edge")
    from OCP.BRepExtrema import BRepExtrema_IsOnEdge
    from OCP.BRep import BRep_Tool as _BT
    on = _np(ext.PointOnShape2(1))
    ad = BRepAdaptor_Curve(edge)
    if ext.SupportTypeShape2(1) == BRepExtrema_IsOnEdge:
        t = ext.ParOnEdgeS2(1)[0]
    else:                                   # the nearest point is one of the edge's end vertices
        t = _BT.Parameter_s(_as_vertex(ext.SupportOnShape2(1)), edge)
    p, v = gp_Pnt(), gp_Vec()
    ad.D1(t, p, v)
    d = np.array([v.X(), v.Y(), v.Z()])
    if np.linalg.norm(d) < 1e-12:
        raise KernelError("The edge has no direction at that point")
    return on, d / np.linalg.norm(d)


def surface_point_normal(face, pt=None):
    """(point, outward unit normal) on a face: the point of the face nearest `pt`, or its middle when pt is None."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Vec
    if pt is None:
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        u, v = (u0 + u1) / 2, (v0 + v1) / 2
    else:
        proj = GeomAPI_ProjectPointOnSurf(_pnt(pt), BRep_Tool.Surface_s(face))
        if proj.NbPoints() < 1:
            raise KernelError("Couldn't find that point on the surface")
        u, v = proj.LowerDistanceParameters()
    p, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(u, v, p, n)
    nv = np.array([n.X(), n.Y(), n.Z()])
    if np.linalg.norm(nv) < 1e-12:
        raise KernelError("The surface has no direction there")
    return _np(p), nv / np.linalg.norm(nv)


def vertex_points(shape):
    """Positions of a shape's vertices, in the same order the viewport numbers them."""
    return _vertices_np(shape)


def make_vertex(pos):
    """A vertex shape at `pos` (a point profile of a loft)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    return BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(x) for x in pos])).Vertex()


def is_vertex(shape):
    return shape.ShapeType() == TopAbs_VERTEX


def planar_face_plane(face) -> Plane | None:
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_Plane:
        return None
    pl = ad.Plane()
    pos = pl.Position()
    n = _np(pos.Direction())
    x = _np(pos.XDirection())
    if face.Orientation() == TopAbs_REVERSED:
        n = -n
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    c = _np(g.CentreOfMass())
    # Sketch frame sits on the face centroid; pick an x-axis aligned with the world if possible
    for cand in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        proj = cand - (cand @ n) * n
        if np.linalg.norm(proj) > 0.3:
            x = proj / np.linalg.norm(proj)
            break
    origin = c - ((c - _np(pos.Location())) @ n) * n
    return Plane(origin, x, n)


def edge_line(edge):
    """(point, direction) if the edge is straight, else None."""
    ad = BRepAdaptor_Curve(edge)
    if ad.GetType() != GeomAbs_Line:
        return None
    ln = ad.Line()
    return _np(ln.Location()), _np(ln.Direction())


# --------------------------------------------------------------------------- sketch → regions

# --------------------------------------------------------------------------- sketch splines, text

def _clean_pts2d(pts, closed):
    """Spline points with repeats removed (an interpolating spline can't pass through one spot twice)."""
    out = []
    for q in pts:
        q = (float(q[0]), float(q[1]))
        if not out or math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > 1e-7:
            out.append(q)
    if closed and len(out) > 2 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < 1e-7:
        out.pop()
    return out


def spline_curve_2d(pts, closed=False):
    """A smooth curve passing through the points (closed: a loop), as an OpenCascade 2D B-spline.
    None if there aren't enough points."""
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    try:
        from OCP.TColgp import TColgp_HArray1OfPnt2d
    except ImportError:  # OCP >= 8 exposes NCollection templates directly
        from OCP.OCP.collections import HArray1_gp_Pnt2d as TColgp_HArray1OfPnt2d
    from OCP.gp import gp_Pnt2d
    q = _clean_pts2d(pts, closed)
    if len(q) < (3 if closed else 2):
        return None
    arr = TColgp_HArray1OfPnt2d(1, len(q))
    for i, (x, y) in enumerate(q, 1):
        arr.SetValue(i, gp_Pnt2d(x, y))
    it = Geom2dAPI_Interpolate(arr, bool(closed), 1e-7)
    it.Perform()
    if not it.IsDone():
        return None
    return it.Curve()


def spline_sample(pts, closed=False, n=64) -> np.ndarray:
    """Points along the spline through `pts`, for drawing (n per span, so it stays smooth however long)."""
    c = spline_curve_2d(pts, closed)
    if c is None:
        return np.array([[float(x), float(y)] for x, y in pts]).reshape(-1, 2)
    spans = max(1, len(pts) - (0 if closed else 1))
    k = max(n // 2, 16) * spans
    a, b = c.FirstParameter(), c.LastParameter()
    out = np.empty((k + 1, 2))
    for i, t in enumerate(np.linspace(a, b, k + 1)):
        p = c.Value(t)
        out[i] = (p.X(), p.Y())
    return out


_FONT_CACHE: dict = {}


def text_shape(text, font="Arial", size=10.0, bold=False, italic=False):
    """Text as flat faces (one per glyph, letters like "o" with their holes), bottom-left at the origin in XY.
    Uses the computer's fonts; an unknown font falls back to one that exists."""
    key = (text, font, round(float(size), 6), bool(bold), bool(italic))
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    from OCP.StdPrs import StdPrs_BRepFont, StdPrs_BRepTextBuilder
    from OCP.Font import (Font_FA_Regular, Font_FA_Bold, Font_FA_Italic, Font_FA_BoldItalic,
                          Font_StrictLevel_Any)
    from OCP.TCollection import TCollection_AsciiString
    try:
        from OCP.NCollection import NCollection_String
    except ImportError:  # OCP < 8 binds the typedef under its template name only
        from OCP.NCollection import NCollection_Utf8String as NCollection_String
    if not str(text).strip() or size <= 0:
        shp = TopoDS_Compound()
        BRep_Builder().MakeCompound(shp)
    else:
        aspect = (Font_FA_BoldItalic if bold and italic else Font_FA_Bold if bold else
                  Font_FA_Italic if italic else Font_FA_Regular)
        f = StdPrs_BRepFont()
        if not f.FindAndInit(TCollection_AsciiString(str(font or "Arial")), aspect, float(size),
                             Font_StrictLevel_Any):
            raise KernelError(f"No font available for the text (looked for {font})")
        shp = StdPrs_BRepTextBuilder().Perform(f, NCollection_String(str(text)), gp_Ax3())
    if len(_FONT_CACHE) > 200:
        _FONT_CACHE.clear()
    _FONT_CACHE[key] = shp
    return shp


def text_outlines(text, font="Arial", size=10.0, bold=False, italic=False) -> list:
    """The text's outlines as 2D polylines (bottom-left at the origin), for drawing in the sketch."""
    key = ("outl", text, font, round(float(size), 6), bool(bold), bool(italic))
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    shp = text_shape(text, font, size, bold, italic)
    out = [discretize_edge(e, max(size, 0.1) * 0.002)[:, :2].astype(float) for e in edges(shp)]
    out = [p for p in out if len(p) >= 2]
    _FONT_CACHE[key] = out
    return out


def text_extent(text, font="Arial", size=10.0, bold=False, italic=False):
    """(lo, hi) corners of the box around the text, in its own coordinates (bottom-left of the first letter
    at the origin). Empty text gets a small box so it can still be found and clicked."""
    shp = text_shape(text, font, size, bold, italic)
    if not edges(shp):
        return np.array([0.0, 0.0]), np.array([size * 0.6, size * 0.7])
    lo, hi = bbox(shp)
    return np.array([min(lo[0], 0.0), min(lo[1], 0.0)]), np.array([hi[0], max(hi[1], size * 0.7)])


def make_sketch_edges(plane: Plane, curves, with_ids=False) -> list:
    """curves: list of dicts {'type': 'line'|'circle'|'arc', ...} in sketch (u, v) coordinates.

    Splines, ellipses and text are curves too; a text curve gives one edge per glyph outline.

    with_ids=True returns (curve id, edge) pairs instead, the id being the curve's "id" (the sketch
    entity id, see Sketch.curves) or None. A text curve's edges are numbered "<id>_<n>" in the order the
    text shape lists them, so each glyph outline has its own id. The separator is "_" because "." is
    reserved in an element name (naming._RESERVED), so a "." label would leave the whole sketch unnamed. A degenerate curve is skipped as a whole,
    so it never shifts the pairing of the curves after it."""
    out = []
    ax3 = plane.gp_ax3()
    for c in curves:
        edge = None
        try:
            if c["type"] == "line":
                a = plane.to_world(*c["p1"])
                b = plane.to_world(*c["p2"])
                if np.linalg.norm(a - b) < 1e-7:
                    continue
                edge = BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge()
            elif c["type"] == "circle":
                if c["r"] < 1e-7:
                    continue
                ax2 = gp_Ax2(_pnt(plane.to_world(*c["c"])), ax3.Direction(), ax3.XDirection())
                edge = BRepBuilderAPI_MakeEdge(gp_Circ(ax2, float(c["r"]))).Edge()
            elif c["type"] == "arc":
                p1 = plane.to_world(*c["p1"])
                pm = plane.to_world(*c["pm"])
                p2 = plane.to_world(*c["p2"])
                if min(np.linalg.norm(p1 - pm), np.linalg.norm(p2 - pm), np.linalg.norm(p1 - p2)) < 1e-7:
                    continue
                arc = GC_MakeArcOfCircle(_pnt(p1), _pnt(pm), _pnt(p2)).Value()
                edge = BRepBuilderAPI_MakeEdge(arc).Edge()
            elif c["type"] == "spline":
                from OCP.GeomAPI import GeomAPI
                c2 = spline_curve_2d(c["pts"], c.get("closed", False))
                if c2 is None:
                    continue
                c3 = GeomAPI.To3d_s(c2, gp_Pln(ax3))
                edge = BRepBuilderAPI_MakeEdge(c3).Edge()
            elif c["type"] == "ellipse":
                from OCP.gp import gp_Elips
                ax = np.asarray(c["major"], float)
                A, B = float(np.linalg.norm(ax)), abs(float(c["r"]))
                if A < 1e-7 or B < 1e-7:
                    continue
                u = ax / A
                if B > A:                       # OpenCascade wants the longer axis first
                    A, B, u = B, A, np.array([-u[1], u[0]])
                ctr = plane.to_world(*c["c"])
                xd = plane.to_world(*(np.asarray(c["c"], float) + u)) - ctr
                ax2 = gp_Ax2(_pnt(ctr), ax3.Direction(), _dir(xd))
                if abs(A - B) < 1e-9:
                    edge = BRepBuilderAPI_MakeEdge(gp_Circ(ax2, A)).Edge()
                else:
                    edge = BRepBuilderAPI_MakeEdge(gp_Elips(ax2, A, B)).Edge()
            elif c["type"] == "text":
                cid = c.get("id")
                for i, te in enumerate(_ordered_glyph_edges(_place_text(plane, c))):
                    out.append((None if cid is None else f"{cid}_{i}", te) if with_ids else te)
                continue
        except Exception:
            continue
        if edge is not None:
            out.append((c.get("id"), edge) if with_ids else edge)
    return out


def _ordered_glyph_edges(shape) -> list:
    """The edges of a placed text shape in an order that depends only on the text and its font. The order
    the font builder lists a glyph's contours in changes from one build to the next (the stem and the dot of an
    "i" swap), and the glyph labels "<id>_<n>" are numbered in this order, so the edges are sorted by where they
    sit (lowest corner, then highest corner, to 3 decimals) instead."""
    def key(e):
        lo, hi = bbox(e)
        return tuple(round(float(v), 3) + 0.0 for v in (*lo, *hi))
    return sorted(edges(shape), key=key)


def sketch_geometry(plane: Plane, sketch):
    """A compound of the edges and vertices of every entity of `sketch` (a sketch.Sketch) on `plane`:
    lines, circles and arcs as edges, point entities as free vertices. Construction geometry is included.
    sketch_regions is the other reader: closed, non-construction curves only, made into faces. This is what
    a Sketch instance in an Assembly shows and mates to (ADR 0011). Entities with no extent (a zero-length
    line, a zero-radius circle) are skipped. The shape is in the plane's own (world) coordinates."""
    import copy
    flat = copy.copy(sketch)                    # Sketch.curves() skips construction entities: read a copy without that flag
    flat.entities = {eid: dict(e, construction=False) for eid, e in sketch.entities.items()}
    shapes = list(make_sketch_edges(plane, flat.curves()))
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    for e in sketch.entities.values():
        if e["type"] == "point" and e["p"] in sketch.points:
            shapes.append(BRepBuilderAPI_MakeVertex(_pnt(plane.to_world(*sketch.points[e["p"]]))).Vertex())
    return compound(shapes)


def sketch_regions(plane: Plane, curves, with_history=False) -> list:
    """Split the sketch plane by all sketch curves and return every bounded region as a face.

    This mirrors Onshape: overlapping profiles give separate, individually selectable regions.

    with_history=True returns (regions, history, edges): the Splitter's BRepTools_History and the
    (curve id, edge) pairs it was given, which is what the history starts at. naming.name_sketch
    names the regions from those (docs/reference/element-naming.md § 4).
    """
    pairs = make_sketch_edges(plane, curves, with_ids=True)
    edges_ = [e for _, e in pairs]

    def done(regions, sp=None, big=None):
        if not with_history:
            return regions
        hist = _maker_history(sp, [big] + edges_) if sp is not None else BRepTools_History()
        return regions, hist, pairs

    if not edges_:
        return done([])
    lo, hi = bbox(compound(edges_))
    pts = [plane.to_local(p) for p in (lo, hi, [lo[0], hi[1], lo[2]], [hi[0], lo[1], hi[2]],
                                       [lo[0], lo[1], hi[2]], [hi[0], hi[1], lo[2]])]
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    margin = max(max(us) - min(us), max(vs) - min(vs), 1.0) * 0.5 + 10
    u0, u1, v0, v1 = min(us) - margin, max(us) + margin, min(vs) - margin, max(vs) + margin
    big = BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0, u1, v0, v1).Face()
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_list([big]))
    sp.SetTools(_list(edges_))
    sp.SetFuzzyValue(1e-6)
    sp.Build()
    if not sp.IsDone():
        return done([])
    regions = []
    tol = 1e-4 * margin
    for f in faces(sp.Shape()):
        flo, fhi = bbox(f)
        corners = [plane.to_local(p) for p in (flo, fhi)]
        fu = [c[0] for c in corners]
        fv = [c[1] for c in corners]
        # also project the other bbox corners: plane may be skewed in world space
        if (min(fu) <= u0 + tol or max(fu) >= u1 - tol or min(fv) <= v0 + tol or max(fv) >= v1 - tol):
            # verify against actual vertices to be safe
            touches = False
            for v in _vertices_np(f):
                u, w = plane.to_local(v)
                if abs(u - u0) < tol or abs(u - u1) < tol or abs(w - v0) < tol or abs(w - v1) < tol:
                    touches = True
                    break
            if touches:
                continue
        regions.append(f)
    glyphs = _text_glyph_faces(plane, curves)
    if glyphs:
        regions = [f for f in regions if not _is_letter_hole(f, glyphs)]
    return done(regions, sp, big)


def _place_text(plane: Plane, c):
    """The text shape of a sketch text curve, moved and turned to where it sits on the sketch plane."""
    shp = text_shape(*c["key"])
    t_rot = gp_Trsf()
    t_rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(c.get("angle", 0.0)))
    t_mov = gp_Trsf()
    t_mov.SetTranslation(gp_Vec(float(c["p"][0]), float(c["p"][1]), 0.0))
    t_pl = gp_Trsf()
    t_pl.SetDisplacement(gp_Ax3(), plane.gp_ax3())        # sketch (u, v) -> the sketch plane in 3D
    return BRepBuilderAPI_Transform(shp, t_pl.Multiplied(t_mov.Multiplied(t_rot)), True).Shape()


def _text_glyph_faces(plane: Plane, curves) -> list:
    out = []
    for c in curves:
        if c.get("type") == "text":
            try:
                out.extend(faces(_place_text(plane, c)))
            except Exception:
                pass
    return out


def _is_letter_hole(region, glyphs) -> bool:
    """True for the empty middle of a letter (the inside of an "o", "e", "A" …): a sketch region that sits
    inside a letter's outline but isn't part of the letter. Extruding text then gives letters, not discs."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    m = tessellate(region)
    if not len(m.tris):
        return False
    tri = m.verts[m.tris]
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    q = tri[int(np.argmax(area))].mean(0)                    # a point well inside the region
    p = _pnt(q)
    for g in glyphs:
        lo, hi = bbox(g)
        if np.any(q < lo - 1e-6) or np.any(q > hi + 1e-6):
            continue
        if BRepClass_FaceClassifier(g, p, 1e-7).State() in (TopAbs_IN, TopAbs_ON):
            return False                                     # part of a letter
        try:
            outer = BRepBuilderAPI_MakeFace(BRepTools_OuterWire(g)).Face()
        except Exception:
            continue
        if BRepClass_FaceClassifier(outer, p, 1e-7).State() == TopAbs_IN:
            return True                                      # inside the letter's outline, not the letter
    return False


def BRepTools_OuterWire(face):
    from OCP.BRepTools import BRepTools
    return BRepTools.OuterWire_s(face)


def _vertices_np(shape):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, m)
    return [_np(BRep_Tool.Pnt_s(_as_vertex(m.FindKey(i)))) for i in range(1, m.Size() + 1)]


# --------------------------------------------------------------------------- solid features

def _swept(sweeps, with_history, surface=False):
    """The fused sweeps, one per profile, and (with_history) their history, starting at the profile
    faces the caller passed in: the placement that moved a profile, then its sweep, then fuse_all.

    `sweeps` is one (maker, swept shape, profile face, placement maker or None) per profile.
    With `surface` the profiles are wires and each sweep is a shell of its own: the shells are returned as
    they are (one shell, or a compound of them), not fused and not sewn, so each keeps its history."""
    results = [mk.Shape() for mk, _, _, _ in sweeps]
    if surface:
        shape = results[0] if len(results) == 1 else compound(results)
        if not with_history:
            return shape
        return shape, _chain(*[_chain(None if place is None else _maker_history(place, [f]),
                                      _maker_history(mk, [start], caps=True))
                               for mk, start, f, place in sweeps])
    if not with_history:
        return fuse_all(results)
    per_profile = [_chain(None if place is None else _maker_history(place, [f]),
                          _maker_history(mk, [start], caps=True))
                   for mk, start, f, place in sweeps]
    shape, fused = fuse_all(results, with_history=True)
    return shape, _chain(_chain(*per_profile), fused)


def extrude(profile_faces, direction: np.ndarray, depth: float, symmetric=False, depth2: float = 0.0,
            with_history=False):
    """Extrude profile faces into a solid, or profile wires (open or closed) into a surface body: a prism of
    a wire is a shell with one face per edge. Faces and wires are not mixed."""
    if not profile_faces:
        raise KernelError("Select sketch regions or a planar face to extrude")
    surface = any(f.ShapeType() == TopAbs_WIRE for f in profile_faces)
    if surface and not all(f.ShapeType() == TopAbs_WIRE for f in profile_faces):
        raise KernelError("Extrude takes faces or curves, not both")
    if abs(depth) < 1e-9 and abs(depth2) < 1e-9:
        raise KernelError("Depth must be non-zero")
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    sweeps = []
    for f in profile_faces:
        start, place = f, None
        if symmetric:
            total = depth
            start, place = _placed(f, _trsf_translate(-d * total / 2))
            vec = d * total
        elif depth2:
            start, place = _placed(f, _trsf_translate(-d * depth2))
            vec = d * (depth + depth2)
        else:
            vec = d * depth
        mk = BRepPrimAPI_MakePrism(start, _vec(vec))
        mk.Build()
        if not mk.IsDone():
            raise KernelError("Extrude failed")
        sweeps.append((mk, start, f, place))
    return _swept(sweeps, with_history, surface)


def revolve(profile_faces, axis_p, axis_d, angle_deg: float, symmetric=False, with_history=False):
    """Revolve profile faces into a solid, or profile wires (open or closed) into a surface body: a revolved
    wire is a shell with one face per edge. Faces and wires are not mixed."""
    if not profile_faces:
        raise KernelError("Select sketch regions to revolve")
    surface = any(f.ShapeType() == TopAbs_WIRE for f in profile_faces)
    if surface and not all(f.ShapeType() == TopAbs_WIRE for f in profile_faces):
        raise KernelError("Revolve takes faces or curves, not both")
    ax = gp_Ax1(_pnt(axis_p), _dir(axis_d))
    sweeps = []
    ang = math.radians(angle_deg)
    for f in profile_faces:
        start, place = f, None
        if symmetric and angle_deg < 360:
            start, place = _placed(f, _trsf_rotate(axis_p, axis_d, -angle_deg / 2))
        if abs(angle_deg) >= 360 - 1e-9:
            mk = BRepPrimAPI_MakeRevol(start, ax)
        else:
            mk = BRepPrimAPI_MakeRevol(start, ax, ang)
        mk.Build()
        if not mk.IsDone():
            raise KernelError("Revolve failed (does the profile cross the axis?)")
        sweeps.append((mk, start, f, place))
    return _swept(sweeps, with_history, surface)


def _placed(shape, t: gp_Trsf):
    """The moved shape and the maker that moved it (a rigid move still has a history: every
    sub-shape is modified into its image, so a name survives the move)."""
    mk = BRepBuilderAPI_Transform(shape, t, True)
    return mk.Shape(), mk


def _trsf_translate(v) -> gp_Trsf:
    t = gp_Trsf()
    t.SetTranslation(_vec(v))
    return t


def _trsf_rotate(p, d, angle_deg) -> gp_Trsf:
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(_pnt(p), _dir(d)), math.radians(angle_deg))
    return t


# --------------------------------------------------------------------------- "up to" end conditions

def interior_point(face) -> np.ndarray:
    """A point well inside a face (the middle of its largest mesh triangle — safe for C or O shapes)."""
    m = tessellate(face)
    if not len(m.tris):
        lo, hi = bbox(face)
        return (lo + hi) / 2
    tri = m.verts[m.tris].astype(float)
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    return tri[int(np.argmax(area))].mean(0)


def termination_face(face, offset=0.0, start_point=None, size=1e4):
    """A face to stop an extrude at: the whole (extended) surface of `face`, moved `offset` towards
    `start_point` (a positive offset stops short of the face). Planes become a big square."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.Geom import Geom_OffsetSurface
    pl = planar_face_plane(face)
    if pl is not None:
        return termination_plane(pl, offset, start_point, size)
    surf = BRep_Tool.Surface_s(face)
    if abs(offset) > 1e-12:
        cands = [Geom_OffsetSurface(surf, float(offset)), Geom_OffsetSurface(surf, -float(offset))]
        if start_point is not None:
            def dist(sf):
                from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
                pr = GeomAPI_ProjectPointOnSurf(_pnt(start_point), sf)
                return pr.LowerDistance() if pr.NbPoints() else 1e18
            cands.sort(key=dist)
        surf = cands[0]
    u0, u1, v0, v1 = surf.Bounds()
    clamp = lambda x: max(-size, min(size, x))             # noqa: E731
    mk = BRepBuilderAPI_MakeFace(surf, clamp(u0), clamp(u1), clamp(v0), clamp(v1), 1e-6)
    if not mk.IsDone():
        raise KernelError("Can't extend that face to stop at")
    return mk.Face()


def termination_plane(pl: Plane, offset=0.0, start_point=None, size=1e4):
    n = np.asarray(pl.normal, float)
    o = np.asarray(pl.origin, float)
    if abs(offset) > 1e-12:
        side = 1.0
        if start_point is not None and float((np.asarray(start_point, float) - o) @ n) < 0:
            side = -1.0
        o = o + n * side * float(offset)
    p2 = Plane(o, pl.xdir, n)
    return BRepBuilderAPI_MakeFace(gp_Pln(p2.gp_ax3()), -size, size, -size, size).Face()


def keep_piece_from(long_body, tool, seed, history=None):
    """Cut `long_body` (a profile swept too far) with `tool` and keep the piece that contains `seed` (a point
    just past the profile): the sweep up to where it first meets the tool.

    Given `history` (long_body's own, from the sweep's `with_history=True`) it returns (piece, history, ends):
    the sweep's history chained with the Splitter's, so the piece's faces carry the profile's names, and the
    piece's end cap -- the faces that lie on the tool, which nothing in the profile's history reaches."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_list([long_body]))
    sp.SetTools(_list([tool]))
    sp.SetFuzzyValue(1e-7)
    sp.Build()
    if not sp.IsDone():
        raise KernelError("Couldn't find where the extrude meets that")
    pieces = solids(sp.Shape())
    if len(pieces) < 2:
        raise KernelError("It never reaches that — check the direction (Opposite direction) or pick another")
    for sol in pieces:
        cl = BRepClass3d_SolidClassifier(sol, _pnt(seed), 1e-7)
        if cl.State() == TopAbs_IN:
            if history is None:
                return sol
            split = _maker_history(sp, [long_body, tool])
            return sol, _chain(history, split), _faces_not_from(sol, long_body, split)
    raise KernelError("Couldn't find where the extrude meets that")


def _faces_not_from(shape, source, history):
    """The faces of `shape` that are neither faces of `source` nor what `history` modified them into."""
    known = TopTools_IndexedMapOfShape()
    for f in _sub_shapes(source, TopAbs_FACE):
        known.Add(f)
        try:
            for img in history.Modified(f):
                known.Add(img)
        except Exception:
            pass
    return [f for f in _sub_shapes(shape, TopAbs_FACE) if not known.Contains(f)]


def rotate_point(q, axis_p, axis_d, angle_deg):
    """A point turned about an axis (right-hand rule)."""
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(_pnt(axis_p), _dir(axis_d)), math.radians(angle_deg))
    r = _pnt(q).Transformed(t)
    return np.array([r.X(), r.Y(), r.Z()])


def angle_about(a, b, axis_p, axis_d) -> float:
    """How far (degrees, 0..360, right-hand rule) point a must turn about the axis to line up with point b."""
    d = np.asarray(axis_d, float) / np.linalg.norm(axis_d)
    o = np.asarray(axis_p, float)
    va, vb = np.asarray(a, float) - o, np.asarray(b, float) - o
    va, vb = va - (va @ d) * d, vb - (vb @ d) * d
    ang = math.degrees(math.atan2(float(np.cross(va, vb) @ d), float(va @ vb)))
    return ang % 360.0


def translate(shape, v):
    return _placed(shape, _trsf_translate(v))[0]


def rotate(shape, p, d, angle_deg):
    return _placed(shape, _trsf_rotate(p, d, angle_deg))[0]


def mirror(shape, plane: Plane, with_history=False):
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(_pnt(plane.origin), _dir(plane.normal)))
    out, mk = _placed(shape, t)
    return (out, _maker_history(mk, [shape])) if with_history else out


def pattern_translate(shape, v, with_history=False):
    """translate(), for a pattern instance: with_history=True also returns the move's history."""
    out, mk = _placed(shape, _trsf_translate(v))
    return (out, _maker_history(mk, [shape])) if with_history else out


def pattern_rotate(shape, p, d, angle_deg, with_history=False):
    """rotate(), for a pattern instance: with_history=True also returns the move's history."""
    out, mk = _placed(shape, _trsf_rotate(p, d, angle_deg))
    return (out, _maker_history(mk, [shape])) if with_history else out


def _solid_volume(shape):
    try:
        return rough_volume(shape) if solids(shape) else None
    except Exception:
        return None


def _plausible(cls, a, b, res):
    """Rough volume sanity check on a boolean result (catches gross failures such as a cut that
    returned nothing or swelled the part)."""
    va, vb = _solid_volume(a), _solid_volume(b)
    if va is None or vb is None:
        return True                      # not solids: nothing to check against
    vr = _solid_volume(res)
    if vr is None:
        vr = 0.0                         # an empty result is only right if the volume really vanished
    tol = 0.03 * (va + vb) + 1e-9
    if cls is BRepAlgoAPI_Cut:
        return va - vb - tol <= vr <= va + tol
    if cls is BRepAlgoAPI_Fuse:
        return max(va, vb) - tol <= vr <= va + vb + tol
    return vr <= min(va, vb) + tol


def _repair(shape, with_history=False):
    """OpenCascade's shape healer; returns the fixed shape only if it is now valid. with_history=True
    returns (shape, history of the healer's ReShape) instead, still None when the fix did not take."""
    from OCP.ShapeFix import ShapeFix_Shape
    try:
        fx = ShapeFix_Shape(shape)
        fx.SetPrecision(1e-7)
        fx.SetMaxTolerance(1e-4)
        fx.Perform()
        out = fx.Shape()
        if not is_valid(out):
            return None
        return (out, _maker_history(fx.Context(), [shape])) if with_history else out
    except Exception:
        return None


def _run_boolean(cls, a, b, fuzzy, preset):
    """(result, builder) — the builder too, because it carries the operation's history."""
    if preset:                     # settings applied before the operation runs
        op = cls()
        op.SetArguments(_list([a]))
        op.SetTools(_list([b]))
        op.SetFuzzyValue(fuzzy)
        op.Build()
    else:                          # the original one-step form (runs with its own defaults)
        op = cls(a, b)
        op.SetFuzzyValue(fuzzy)
        op.Build()
    return (op.Shape() if op.IsDone() else None), op


def _boolean(cls, a, b, check=False, single=False, volume_check=False, with_history=False):
    """check=True: careful mode for delicate geometry such as threads — the result must be a valid
    shape (OpenCascade's healer is tried on tolerance blemishes) and not empty, and exact tolerances
    are tried first. single=True: the result must be one solid (the pieces were meant to merge).
    volume_check=True: also a rough volume sanity check.

    with_history=True returns (shape, history) — the boolean's own history, then the refine step's,
    then the healer's, in the order the steps ran."""
    def done(res, *steps):
        return (res, _chain(*steps)) if with_history else res
    if not check:
        res, op = _run_boolean(cls, a, b, 1e-6, False)
        if res is None:
            raise KernelError("Boolean operation failed")
        if not with_history:
            return clean(res)
        res, refine = clean(res, with_history=True)
        return done(res, _maker_history(op, [a, b]), refine)
    want_solid = bool(solids(a)) and cls is not BRepAlgoAPI_Common
    for fuzzy, preset in ((0.0, True), (1e-7, True), (1e-5, True), (1e-6, False)):
        r, op = _run_boolean(cls, a, b, fuzzy, preset)
        if r is None:
            continue
        fixed = None
        if not is_valid(r):
            fix = _repair(r, with_history)  # usually just edge tolerances at the new cut edges
            if fix is None:
                continue
            r, fixed = fix if with_history else (fix, None)
        n = len(solids(r))
        if (want_solid and n == 0) or (single and n != 1):
            continue
        if volume_check and not _plausible(cls, a, b, r):
            continue
        return done(r, _maker_history(op, [a, b]), fixed)
    raise KernelError("Boolean operation failed")


def fuse(a, b, check=False, single=False, volume_check=False, with_history=False):
    return _boolean(BRepAlgoAPI_Fuse, a, b, check, single, volume_check, with_history)


def cut(a, b, check=False, volume_check=False, with_history=False):
    return _boolean(BRepAlgoAPI_Cut, a, b, check, False, volume_check, with_history)


def common(a, b, check=False, volume_check=False, with_history=False):
    return _boolean(BRepAlgoAPI_Common, a, b, check, False, volume_check, with_history)


def fuse_all(shapes, with_history=False):
    shapes = [s for s in shapes if s is not None]
    if not shapes:
        return (None, BRepTools_History()) if with_history else None
    acc = shapes[0]
    hist = BRepTools_History()
    for s in shapes[1:]:
        if not with_history:
            acc = fuse(acc, s)
            continue
        acc, step = fuse(acc, s, with_history=True)
        hist = _chain(hist, step)      # each fuse maps what the one before it made
    return (acc, hist) if with_history else acc


def intersects(a, b) -> bool:
    la, ha = bbox(a)
    lb, hb = bbox(b)
    if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
        return False
    try:
        c = common(a, b)
        return volume(c) > 1e-9
    except KernelError:
        return False


def touches_or_intersects(a, b) -> bool:
    la, ha = bbox(a)
    lb, hb = bbox(b)
    if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
        return False
    try:
        f = fuse(a, b)
        return len(solids(f)) < len(solids(a)) + len(solids(b))
    except KernelError:
        return False


def _blend_ok(before, after):
    """A fillet / chamfer result we can trust: a valid solid with no more pieces than before. OpenCascade sometimes
    'succeeds' with a too-big radius and returns overlapping, self-intersecting rounds instead of failing."""
    try:
        if after is None or after.IsNull() or not is_valid(after):
            return False
        if len(solids(after)) > max(1, len(solids(before))):
            return False
        v = volume(after)
        return v > 1e-9 and abs(v - volume(before)) < volume(before)
    except Exception:
        return False


try:
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
except ImportError:  # OCP >= 8
    from OCP.OCP.collections import (IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher
                                     as TopTools_IndexedDataMapOfShapeListOfShape)


def edge_faces(shape, edge):
    """The distinct faces of `shape` that share `edge` (a seam shows one face twice and counts once)."""
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    if not m.Contains(edge):
        return []
    out = []
    for f in m.FindFromKey(edge):
        if not any(f.IsSame(o) for o in out):
            out.append(f)
    return out


def _try_blend(make, shape, edge_list, size, with_history=False, two=None, why=None):
    """The blended shape, or None. with_history=True returns (shape, history of this maker) or None.
    `why`, a dict, is filled on a failure: "cause" is "exception" (Build raised; "exc" holds it), "not_done"
    (IsDone() false) or "rejected" (_blend_ok refused the result), and "maker" is the maker, for _diagnose_blend.
    `two` is (kind, second, faces) for a chamfer with two distances ("two": second is the other
    distance) or a distance and an angle ("angle": second is the angle in radians); faces[i] is the
    face of edge_list[i] on which `size` is measured."""
    mk = make(shape)
    for i, e in enumerate(edge_list):
        if two is None:
            mk.Add(float(size), e)
        elif two[0] == "two":
            mk.Add(float(size), float(two[1]), e, _as_face(two[2][i]))
        else:
            mk.AddDA(float(size), float(two[1]), e, _as_face(two[2][i]))
    if why is not None:
        why.clear()
        why["maker"] = mk
    try:
        mk.Build()
    except Exception as exc:
        if why is not None:
            why.update(cause="exception", exc=exc)
        return None
    if not mk.IsDone():
        if why is not None:
            why["cause"] = "not_done"
        return None
    out = mk.Shape()
    if not _blend_ok(shape, out):
        if why is not None:
            why["cause"] = "rejected"
        return None
    return (out, _maker_history(mk, [shape])) if with_history else out


def _largest_blend(make, shape, edge_list, size, two=None):
    """The largest size below `size` that works (binary search), or None — for a helpful error message."""
    if len(edge_list) > 24:
        return None
    lo, hi = 0.0, float(size)
    for _ in range(9):
        mid = (lo + hi) / 2
        t = two
        if two is not None and two[0] == "two":     # keep the ratio of the two distances while searching
            t = ("two", two[1] * mid / float(size), two[2])
        if _try_blend(make, shape, edge_list, mid, two=t) is not None:
            lo = mid
        else:
            hi = mid
    return lo if lo > 1e-6 else None


def _nice_down(x):
    """Round down to a tidy number for the message (e.g. 39.06 -> 39, 2.47 -> 2.4)."""
    step = 1.0 if x >= 10 else 0.1 if x >= 1 else 0.01
    return math.floor(x / step) * step


def fillet(shape, edge_list, radius: float, with_history=False):
    """with_history=True returns (shape, history of the maker whose result is used)."""
    if not edge_list:
        raise KernelError("Select edges to fillet")
    if radius <= 0:
        raise KernelError("Radius must be positive")
    why = {}
    out = _try_blend(BRepFilletAPI_MakeFillet, shape, edge_list, radius, with_history, why=why)
    if out is None:
        best = _largest_blend(BRepFilletAPI_MakeFillet, shape, edge_list, radius)
        hint = f" — the largest that works here is about {_nice_down(best):g} mm" if best else ""
        raise _fillet_failure(shape, edge_list, why, best is not None, hint)
    return out


def fillet_prechecks(shape, edge_list):
    """Checks before the kernel runs. Returns (edges to fillet, [warning text]); raises KernelError (with
    `failed_edges`) when nothing is left to fillet. An edge with fewer than two distinct faces (a seam, where a
    surface closes on itself, or an open edge) and an edge that is already smooth (continuity above C0) are
    dropped with a warning when other edges remain: OpenCascade ignores a seam among other edges today, and a face
    pick selects its seam too. A corner where four or more edges meet is only a warning here; the build decides
    (and says so if it fails). A check never rejects a selection that builds today."""
    from OCP.GeomAbs import GeomAbs_C0
    one_face, smooth = [], []
    for e in edge_list:
        fs = edge_faces(shape, e)
        if len(fs) < 2:
            one_face.append(e)
            continue
        try:
            if BRep_Tool.Continuity_s(e, _as_face(fs[0]), _as_face(fs[1])) != GeomAbs_C0:
                smooth.append(e)
        except Exception:
            pass
    keep = [e for e in edge_list if not any(e.IsSame(o) for o in one_face + smooth)]
    warnings = []
    for e in one_face:
        warnings.append(f"{_edge_text(e)} is a seam or an open edge with only one face; it was left out")
    for e in smooth:
        warnings.append(f"{_edge_text(e)} is already smooth (it joins its faces tangentially); it was left out")
    if not keep:
        if one_face:
            others = []
            for e in one_face:
                for f in edge_faces(shape, e):
                    for o in edges(f):
                        if len(edge_faces(shape, o)) >= 2 and not any(o.IsSame(x) for x in others):
                            others.append(o)
            msg = (f"{_edge_text(one_face[0])} is a seam or an open edge: it has only one face, so there is no "
                   f"corner to round")
            if others:
                msg += ". The edges next to it that can be rounded are " + "; ".join(_edge_text(o) for o in others[:4])
        else:
            msg = f"{_edge_text(smooth[0])} is already smooth: there is no sharp edge to round"
        err = KernelError(msg)
        err.failed_edges, err.failed_vertices = list(one_face + smooth), []
        raise err
    try:
        for corner in _crowded_corners(shape, keep):
            warnings.append(_corner_text(corner))
    except Exception:
        pass
    return keep, warnings


def _edge_text(e):
    """'the edge from (x, y, z) to (x, y, z)', or 'the curved edge near (x, y, z)' for one that is not a line."""
    try:
        info = edge_info(e)
        if info["type"] == int(GeomAbs_Line):
            a, b = _vertex_xyz(TopExp.FirstVertex_s(e)), _vertex_xyz(TopExp.LastVertex_s(e))
            return "the edge from ({:.3f}, {:.3f}, {:.3f}) to ({:.3f}, {:.3f}, {:.3f})".format(*a, *b)
        return "the curved edge near ({:.3f}, {:.3f}, {:.3f})".format(*info["c"])
    except Exception:
        return "an edge"


# what OpenCascade's per-contour status means for a person (ChFiDS_ErrorStatus)
def _status_text(status):
    from OCP.ChFiDS import ChFiDS_StartsolFailure, ChFiDS_TwistedSurface, ChFiDS_WalkingFailure
    if status == ChFiDS_StartsolFailure:
        return "the radius does not fit at the start of this edge"
    if status == ChFiDS_TwistedSurface:
        return "the round would fold over itself here - the radius is larger than the curve of the neighbouring face"
    if status == ChFiDS_WalkingFailure:
        return "the round could not be followed along this edge"
    return "OpenCascade could not build the round along this edge"


def _diagnose_blend(mk):
    """What a failed MakeFillet says about where and why: {"reasons": [text], "edges": [edge shapes],
    "vertices": [xyz], "has_result": bool or None}. Reads only the inspection methods the I8.1 spike found callable
    on both OCP builds. Runs after a failure only, and never raises: whatever cannot be read is left out."""
    out = {"reasons": [], "edges": [], "vertices": [], "has_result": None}
    try:
        out["has_result"] = bool(mk.HasResult())
    except Exception:
        pass
    try:
        nc = mk.NbContours()
        for i in range(1, mk.NbFaultyContours() + 1):
            ic = mk.FaultyContour(i)
            if not 1 <= ic <= nc:                  # OpenCascade would dereference a missing spine
                continue
            es = [mk.Edge(ic, j) for j in range(1, mk.NbEdges(ic) + 1)]
            out["edges"] += es
            where = " and ".join(_edge_text(e) for e in es[:3]) + (" ..." if len(es) > 3 else "")
            out["reasons"].append(f"{where}: {_status_text(mk.StripeStatus(ic))}")
    except Exception:
        pass
    try:
        for i in range(1, mk.NbFaultyVertices() + 1):
            xyz = tuple(float(x) for x in _vertex_xyz(mk.FaultyVertex(i)))
            out["vertices"].append(xyz)
            out["reasons"].append("the rounds could not be joined at this corner ({:.3f}, {:.3f}, {:.3f})".format(*xyz))
    except Exception:
        pass
    return out


def _crowded_corners(shape, edge_list):
    """[(xyz, edges at it, selected edges at it)] for every vertex where four or more edges meet and a selected
    edge ends or branches (not exactly two selected edges passing through). OpenCascade does not handle a round
    contour that ends at such a corner (the header of BRepFilletAPI_MakeFillet.Build)."""
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_VERTEX, TopAbs_EDGE, m)
    out, seen = [], []
    for e in edge_list:
        for v in (TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)):
            if v.IsNull() or any(v.IsSame(o) for o in seen) or not m.Contains(v):
                continue
            seen.append(v)
            at = []
            for x in m.FindFromKey(v):
                if not any(x.IsSame(o) for o in at) and not BRep_Tool.Degenerated_s(_as_edge(x)):
                    at.append(x)
            sel = [x for x in at if any(x.IsSame(o) for o in edge_list)]
            if len(at) >= 4 and sel and len(sel) != 2:
                out.append((tuple(float(c) for c in _vertex_xyz(v)), [_as_edge(x) for x in at], [_as_edge(x) for x in sel]))
    return out


def _corner_text(corner):
    xyz, at, sel = corner
    return ("{} edges meet at the vertex ({:.3f}, {:.3f}, {:.3f}); OpenCascade cannot end a round where four or "
            "more edges meet".format(len(at), *xyz))


def _fillet_failure(shape, edge_list, why, smaller_works, hint):
    """The KernelError for a failed fillet. "Radius too large" is said only when `smaller_works` (the search found
    a size that builds, `hint` says which); otherwise the reasons the maker gave are. The error carries the failing
    edges (`failed_edges`, shapes of the shape that was filleted) and vertices (`failed_vertices`, xyz)."""
    diag = {"reasons": [], "edges": [], "vertices": []}
    try:
        diag = _diagnose_blend(why["maker"]) if why.get("maker") is not None else diag
    except Exception:
        pass
    reasons, fe, fv = list(diag["reasons"]), list(diag["edges"]), list(diag["vertices"])
    try:
        for corner in _crowded_corners(shape, edge_list):
            reasons.append(_corner_text(corner))
            fv.append(corner[0])
            fe += [e for e in corner[2] if not any(e.IsSame(o) for o in fe)]
    except Exception:
        pass
    if smaller_works:
        msg = "Fillet radius too large for these edges (the rounds would overlap or not fit)" + hint
    else:
        msg = "Fillet failed on these edges, and no smaller radius works either"
        if not reasons:
            cause = why.get("cause")
            if cause == "exception":
                reasons.append(f"OpenCascade reported: {why.get('exc')}")
            elif cause == "rejected":
                reasons.append("the rounds overlap or fold over each other, so the result is not a valid solid")
            else:
                reasons.append("OpenCascade could not build the rounds")
    if reasons:
        msg += (". Where: " if smaller_works else ". ") + "; ".join(reasons)
    err = KernelError(msg)
    err.failed_edges, err.failed_vertices = fe, fv
    return err


def _contour_vertices(mk, ic):
    """The distinct vertices of the edges of contour `ic` of a MakeFillet, in contour order."""
    out = []
    for j in range(1, mk.NbEdges(ic) + 1):
        ex = TopExp_Explorer(mk.Edge(ic, j), TopAbs_VERTEX)
        while ex.More():
            v = _as_vertex(ex.Current())
            if not any(v.IsSame(o) for o in out):
                out.append(v)
            ex.Next()
    return out


def _vertex_xyz(v):
    return _np(BRep_Tool.Pnt_s(_as_vertex(v)))


def _variable_build(shape, edge_list, default_radius, vertex_radii, scale):
    """One variable-radius build with every radius times `scale`. Returns (maker, result or None, used)
    where `used` flags the vertex_radii entries that sit on an end of a contour edge. The route is the
    one scripts/spikes/op2_fillet.result.txt names: Add(E) per edge, then SetRadius(r, contour, vertex) once
    per vertex of each contour (ResetContour is not needed). Radii are keyed by vertex, so edge direction
    does not matter and the vertices of unselected edges a contour pulls in take the default radius."""
    mk = BRepFilletAPI_MakeFillet(shape)
    used = [False] * len(vertex_radii)
    try:
        for e in edge_list:
            mk.Add(e)
        for ic in range(1, mk.NbContours() + 1):
            for v in _contour_vertices(mk, ic):
                r = float(default_radius)
                p = _vertex_xyz(v)
                for i, (q, rq) in enumerate(vertex_radii):
                    if float(np.linalg.norm(p - np.asarray(q, float))) < 1e-6:
                        r, used[i] = float(rq), True
                mk.SetRadius(r * scale, ic, v)
        mk.Build()
    except Exception:
        return mk, None, used
    if not mk.IsDone():
        return mk, None, used
    out = mk.Shape()
    return mk, (out if _blend_ok(shape, out) else None), used


def _faulty_corner(mk):
    """A phrase naming the vertex a failed fillet maker blames, or ''."""
    try:
        if mk.NbFaultyVertices() > 0:
            x, y, z = _vertex_xyz(mk.FaultyVertex(1))
            return f" (it failed at the vertex ({x:.3f}, {y:.3f}, {z:.3f}))"
    except Exception:
        pass
    return ""


def fillet_variable(shape, edge_list, default_radius: float, vertex_radii, with_history=False):
    """A fillet whose radius changes along the edges. `vertex_radii` is a list of (vertex, radius): the
    vertex is a vertex shape or an xyz position, and the radius applies at that vertex. Every other
    vertex of a contour (which can include edges that were not selected: tangent edges join in) takes
    `default_radius`. OpenCascade eases the radius between vertices; there is no linear option.
    with_history=True returns (shape, history of the maker whose result is used)."""
    if not edge_list:
        raise KernelError("Select edges to fillet")
    if default_radius <= 0:
        raise KernelError("Radius must be positive")
    radii = []
    for v, r in vertex_radii:
        if r <= 0:
            raise KernelError("Vertex radii must be positive")
        radii.append((v if isinstance(v, (tuple, list, np.ndarray)) else _vertex_xyz(v), float(r)))
    mk, out, used = _variable_build(shape, edge_list, default_radius, radii, 1.0)
    if out is None:
        lo, hi = 0.0, 1.0
        if len(edge_list) <= 24:
            for _ in range(9):
                mid = (lo + hi) / 2
                if _variable_build(shape, edge_list, default_radius, radii, mid)[1] is not None:
                    lo = mid
                else:
                    hi = mid
        pct = math.floor(lo * 100)
        hint = f" — the largest that works here is about {pct} percent of these radii" if pct >= 1 else ""
        why = {"maker": mk, "cause": "not_done" if not mk.IsDone() else "rejected"}
        raise _fillet_failure(shape, edge_list, why, pct >= 1, hint)
    for (q, _), ok in zip(radii, used):
        if not ok:
            import logging
            logging.getLogger(__name__).warning(
                "fillet: the vertex at (%.3f, %.3f, %.3f) is not an end of any filleted edge; its radius is ignored",
                *[float(x) for x in q])
    return (out, _maker_history(mk, [shape])) if with_history else out


def chamfer(shape, edge_list, dist: float, with_history=False, kind="equal", second=None, faces=None):
    """with_history=True returns (shape, history of the maker whose result is used).
    kind "two": `second` is the other distance; "angle": `second` is the angle in degrees. Both measure
    `dist` on faces[i], the face chosen for edge_list[i]. "equal" is the plain one-distance chamfer."""
    if not edge_list:
        raise KernelError("Select edges to chamfer")
    if dist <= 0:
        raise KernelError("Distance must be positive")
    two = None
    if kind == "two":
        if second is None or second <= 0:
            raise KernelError("Distance 2 must be positive")
        two = ("two", float(second), faces)
    elif kind == "angle":
        if second is None or not 0 < second < 90:
            raise KernelError("Angle must be between 0 and 90 degrees")
        two = ("angle", math.radians(second), faces)
    elif kind != "equal":
        raise KernelError(f"Unknown chamfer type '{kind}'")
    out = _try_blend(BRepFilletAPI_MakeChamfer, shape, edge_list, dist, with_history, two)
    if out is None:
        best = _largest_blend(BRepFilletAPI_MakeChamfer, shape, edge_list, dist, two)
        hint = f" — the largest that works here is about {_nice_down(best):g} mm" if best else ""
        raise KernelError(f"Chamfer too large for these edges (the cuts would overlap or not fit){hint}")
    return out


def shell(shape, remove_faces, thickness: float, outward=False, with_history=False):
    if thickness <= 0:
        raise KernelError("Thickness must be positive")
    mk = BRepOffsetAPI_MakeThickSolid()
    off = thickness if outward else -thickness
    try:
        mk.MakeThickSolidByJoin(shape, _list(remove_faces), off, 1e-4)
        mk.Build()
    except Exception as exc:
        raise KernelError("Shell failed — try a smaller thickness") from exc
    if not mk.IsDone():
        raise KernelError("Shell failed — try a smaller thickness")
    if with_history:
        return mk.Shape(), _maker_history(mk, [shape])
    return mk.Shape()


def delete_faces(shape, faces, mode="heal", with_history=False):
    """Remove faces from a body (for cleaning up imported parts): heal = the surrounding faces grow to close the
    gap (removes fillets, chamfers, bosses, holes), cap = fill the opening with a flat face, open = leave the
    void open (the result is an open shell).

    with_history=True returns (shape, history). The history is the defeaturing's and the refine's for heal;
    cap and open leave every remaining face as it was, so their history is empty and the names carry over."""
    if with_history:
        return _delete_faces(shape, faces, mode, True)
    return _delete_faces(shape, faces, mode, False)


def _delete_faces(shape, faces, mode, with_history):
    faces = list(faces)
    if not faces:
        raise KernelError("Select faces to delete")
    if mode == "heal":
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
        try:
            d = BRepAlgoAPI_Defeaturing()
            d.SetShape(shape)
            for f in faces:
                d.AddFaceToRemove(f)
            d.Build()
            ok = d.IsDone()
        except Exception:
            ok = False
        if not ok or not solids(d.Shape()) or not is_valid(d.Shape()):
            raise KernelError("Couldn't heal over those faces — select the whole fillet / chamfer / hole, "
                              "or try Cap")
        if not with_history:
            return clean(d.Shape())
        out, refine = clean(d.Shape(), with_history=True)
        return out, _chain(_maker_history(d, [shape]), refine)
    from OCP.BRepTools import BRepTools_ReShape
    rs = BRepTools_ReShape()
    for f in faces:
        rs.Remove(f)
    from OCP.TopExp import TopExp_Explorer as _E
    from OCP.TopAbs import TopAbs_SHELL as _SH
    ex0 = _E(rs.Apply(shape), _SH)
    shells = []
    while ex0.More():                         # the body without those faces is an open shell, not a solid
        shells.append(ex0.Current())
        ex0.Next()
    if not shells:
        raise KernelError("Those faces can't be deleted from this part")
    opened = compound(shells)
    if mode == "open":
        return (opened, BRepTools_History()) if with_history else opened
    if mode == "cap":
        from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
        from OCP.TopExp import TopExp_Explorer as _Exp
        from OCP.TopAbs import TopAbs_WIRE, TopAbs_SHELL
        fb = ShapeAnalysis_FreeBounds(opened, 1e-5, False, False)
        sew = BRepBuilderAPI_Sewing(1e-4)
        sew.Add(opened)
        n = 0
        ex = _Exp(fb.GetClosedWires(), TopAbs_WIRE)
        while ex.More():
            mk = BRepBuilderAPI_MakeFace(_as_wire(ex.Current()), True)     # a flat cap over each opening
            if not mk.IsDone():
                raise KernelError("Can't cap that opening with a flat face — try Heal")
            sew.Add(mk.Face())
            n += 1
            ex.Next()
        if not n:
            raise KernelError("The faces leave no opening to cap")
        sew.Perform()
        res = sew.SewedShape()
        sh = _Exp(res, TopAbs_SHELL)
        if sh.More():
            sol = BRepBuilderAPI_MakeSolid(_as_shell(sh.Current()))
            if sol.IsDone() and is_valid(sol.Shape()):
                return (sol.Shape(), BRepTools_History()) if with_history else sol.Shape()
        raise KernelError("Couldn't close the part after capping — try Heal or Leave open")
    raise KernelError("Unknown delete-face mode")


def move_face(shape, face, distance, with_history=False):
    """Push (distance > 0) or pull (distance < 0) a flat face along its normal, growing or cutting the body.

    with_history=True returns (shape, history, tool, tool_history): the body the face was extruded into
    (None when the distance is zero) and the extrusion's history, then the fuse or cut and the refine
    composed into `history`, which maps the part and the tool to the result."""
    info = face_info(face)
    if "n" not in info:
        raise KernelError("Move face works on flat faces")
    n = np.array(info["n"], float)
    if abs(distance) < 1e-9:
        return (shape, BRepTools_History(), None, None) if with_history else shape
    if not with_history:
        if distance > 0:
            return clean(fuse(shape, extrude([face], n, distance)))
        return clean(cut(shape, extrude([face], -n, -distance)))
    if distance > 0:
        tool, tool_hist = extrude([face], n, distance, with_history=True)
        res, h = fuse(shape, tool, with_history=True)
    else:
        tool, tool_hist = extrude([face], -n, -distance, with_history=True)
        res, h = cut(shape, tool, with_history=True)
    out, refine = clean(res, with_history=True)
    return out, _chain(h, refine), tool, tool_hist


def offset_surface(face, distance, with_history=False):
    """A new surface (one face) at `distance` from `face`, measured along the face's outward normal (negative =
    the other way). Works on flat and curved faces; the result is a surface body, not a solid.
    with_history=True returns (shape, history), the history of the offset maker over `face`."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.TopExp import TopExp_Explorer as _E
    from OCP.TopAbs import TopAbs_FACE
    if abs(distance) < 1e-9:
        raise KernelError("Offset distance is zero")
    try:
        mk = BRepOffsetAPI_MakeOffsetShape()
        mk.PerformByJoin(face, float(distance), 1e-4)
        ok = mk.IsDone()
    except Exception:
        ok = False
    if not ok:
        raise KernelError("Couldn't offset that surface by this distance")
    ex = _E(mk.Shape(), TopAbs_FACE)
    faces = []
    while ex.More():
        faces.append(ex.Current())
        ex.Next()
    if not faces:
        raise KernelError("Couldn't offset that surface by this distance")
    out = compound(faces)
    return (out, _maker_history(mk, [face])) if with_history else out


THICKEN_FAILED = "The thickness is larger than the surface's tightest curve allows"


def thicken(shape_or_faces, t1, t2=0.0, with_history=False):
    """A solid `t1` thick on the outward-normal side of a surface and `t2` thick on the other side (a
    negative value reverses a side). `shape_or_faces` is one shell/face/compound or a list of faces; several
    faces must make one connected shell. Returns the solid, or (solid, history) over the input faces.
    One face or coplanar faces make a prism; anything else goes through BRepOffset_MakeOffset with
    Thickening (spike I2.1: native, history from the maker)."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
    from OCP.GeomAbs import GeomAbs_Intersection
    shape = compound(list(shape_or_faces)) if isinstance(shape_or_faces, (list, tuple)) else shape_or_faces
    fs = faces(shape)
    if not fs:
        raise KernelError("Select a surface or a face to thicken")
    total = float(t1) + float(t2)
    if abs(total) < 1e-6:
        raise KernelError("The thickness must be more than zero")
    planes = [planar_face_plane(f) for f in fs]
    if len(fs) == 1 and planes[0] is not None:
        res, hist = extrude([fs[0]], planes[0].normal, float(t1), depth2=float(t2), with_history=True)
        sol = res
    else:
        h_move = None
        src = shape
        if abs(t2) > 1e-9:
            try:
                mv = BRepOffsetAPI_MakeOffsetShape()
                mv.PerformByJoin(shape, -float(t2), 1e-6)
                ok = mv.IsDone()
            except Exception:
                ok = False
            if not ok or not faces(mv.Shape()):
                raise KernelError(THICKEN_FAILED)
            src = mv.Shape()
            h_move = _maker_history(mv, [shape])
        try:
            mo = BRepOffset_MakeOffset()
            mo.Initialize(src, total, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection, True)
            mo.MakeOffsetShape()          # without this call the maker gives no valid solid
            ok = mo.IsDone()
            sol = mo.Shape() if ok else None
        except Exception:
            ok, sol = False, None
        if not ok or sol is None:
            raise KernelError(THICKEN_FAILED)
        sol, h_clean = clean(sol, with_history=True)       # walls of coplanar free edges become one face
        hist = _chain(h_move, _maker_history(mo, [src]), h_clean)
    ss = solids(sol)
    if len(ss) != 1 or not is_valid(ss[0]):
        raise KernelError(THICKEN_FAILED)
    out = ss[0]
    v = volume(out)
    if v < 0:
        out = out.Reversed()
        v = -v
    if v <= 1e-9:
        raise KernelError(THICKEN_FAILED)
    return (out, hist) if with_history else out


ENCLOSE_FAILED = "The selection does not close a volume"


def sew_closed(shapes, tol=1e-6):
    """Sew faces and shells at `tol` and return a solid for every shell that came out closed (a list, empty
    when none closes). Closure is checked before BRepLib.OrientClosedSolid, which turns an open shell into
    an invalid solid without complaint; a solid that comes out inside out is reversed."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.BRepLib import BRepLib
    from OCP.TopAbs import TopAbs_SHELL
    sew = BRepBuilderAPI_Sewing(float(tol))
    for s in shapes:
        sew.Add(s)
    sew.Perform()
    out = []
    ex = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    while ex.More():
        shell = _as_shell(ex.Current())
        ex.Next()
        if not BRep_Tool.IsClosed_s(shell):
            continue
        mk = BRepBuilderAPI_MakeSolid()
        mk.Add(shell)
        if not mk.IsDone():
            continue
        sol = _as_solid(mk.Shape())
        BRepLib.OrientClosedSolid_s(sol)
        if volume(sol) < 0:
            sol = _as_solid(sol.Reversed())
        if is_valid(sol) and volume(sol) > 1e-9:
            out.append(sol)
    return out


def enclose(shapes, with_history=False):
    """The solids that surfaces surround: a list of solids (one per closed volume), or (solids, history over
    the input faces). `shapes` are faces, shells and solids (a solid acts as a wall through its shells). They
    may overhang each other or cross: BOPAlgo_MakerVolume intersects them and keeps the closed cells (spike
    I2.1: makervolume, with the builder's History())."""
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    from OCP.TopAbs import TopAbs_SHELL
    args = []
    for s in shapes:
        if solids(s):
            ex = TopExp_Explorer(s, TopAbs_SHELL)
            while ex.More():
                args.append(ex.Current())
                ex.Next()
        elif faces(s):
            args.append(s)
    if not args:
        raise KernelError(ENCLOSE_FAILED)
    try:
        mv = BOPAlgo_MakerVolume()
        for a in args:
            mv.AddArgument(a)
        mv.SetRunParallel(False)
        mv.SetIntersect(True)
        mv.SetFuzzyValue(1e-6)
        mv.SetAvoidInternalShapes(True)
        mv.Perform()
        bad = mv.HasErrors()
        res = None if bad else mv.Shape()
    except Exception:
        bad, res = True, None
    if bad or res is None or res.IsNull() or not solids(res):
        raise KernelError(ENCLOSE_FAILED)
    res, h_clean = clean(res, with_history=True)       # the intersection leaves coplanar faces split
    hist = _chain(_maker_history(mv, args), h_clean)
    out = []
    for s in solids(res):
        if volume(s) < 0:
            s = _as_solid(s.Reversed())
        if is_valid(s) and volume(s) > 1e-9:
            out.append(s)
    if not out:
        raise KernelError(ENCLOSE_FAILED)
    return (out, hist) if with_history else out


FILL_GAP = "The boundary has a gap"
FILL_FAILED = "Could not fill this boundary smoothly - try Position on some edges"
FILL_TOL = 1e-3          # how far the fill's boundary may sit from an input edge
# BRepFill_Filling(Degree, NbPtsOnCur, NbIter, Anisotropie, Tol2d, Tol3d, TolAng, TolCurv, MaxDeg, MaxSegments)
FILL_SETTINGS = (3, 15, 2, False, 1e-5, 1e-4, 1e-2, 1e-1, 8, 9)      # the OCC defaults


def boundary_open_ends(edge_list):
    """The end points of a set of edges that should close into one loop and meet no other end (within the
    tolerance `make_wire` uses): empty when the loop closes, else usually two points, one at each side of the gap."""
    ends = []
    for e in edge_list:
        p = discretize_edge(e)
        ends += [p[0].astype(float), p[-1].astype(float)]
    scale = max([float(np.linalg.norm(a - b)) for a in ends for b in ends] + [1.0])
    tol = max(1e-4, 1e-6 * scale) * 100
    return [a for i, a in enumerate(ends)
            if not any(j != i and np.linalg.norm(a - b) < tol for j, b in enumerate(ends))]


def _boundary_gap(edge_list):
    """The position of a gap in a set of edges that should close into one loop, or None when every end meets
    another end."""
    loose = boundary_open_ends(edge_list)
    if not loose:
        return None
    return loose[0] if len(loose) == 1 else (loose[0] + loose[1]) / 2


def fill(edges, supports=None, points=(), with_history=False):
    """A face across a closed boundary. `edges` are the boundary edges in any order; `supports` has one
    entry per edge, the face the fill should leave the edge tangent to, or None for Position; `points` are
    guide points the face must pass through. Returns the face, or (face, history) (the face is new, so
    the history is empty).

    A planar boundary with every edge Position and no guide gets an exact plane (BRepBuilderAPI_MakeFace).
    Otherwise BRepFill_Filling builds a plate through the edges (spike I2.1: filling=g1). A Tangency edge asks
    BRepFill_Filling for G1 to its support; the filler treats that as a target, not a guarantee (the spike
    measured 0.08 to 0.5 rad), so the angle actually reached is `fill_tangent_error`."""
    from OCP.BRepFill import BRepFill_Filling
    from OCP.GeomAbs import GeomAbs_C0, GeomAbs_G1
    edges = list(edges)
    if not edges:
        raise KernelError("Select the edges of the boundary")
    supports = list(supports) if supports is not None else [None] * len(edges)
    if len(supports) != len(edges):
        raise KernelError("Each fill edge needs a continuity")
    gap = _boundary_gap(edges)
    if gap is not None:
        raise KernelError(f"{FILL_GAP} near ({gap[0]:.3f}, {gap[1]:.3f}, {gap[2]:.3f})")
    try:
        wire = make_wire(edges)
    except KernelError as exc:
        raise KernelError(f"The boundary is not one closed loop ({exc})") from exc
    if not BRep_Tool.IsClosed_s(wire):
        raise KernelError(FILL_GAP)
    pts = [np.asarray(p, float) for p in points]
    if all(s is None for s in supports) and not pts:
        mk = BRepBuilderAPI_MakeFace(wire, True)           # only a plane: fails for a bent boundary
        if mk.IsDone():
            face = _as_face(mk.Face())
            if is_valid(face):
                return (face, BRepTools_History()) if with_history else face
    mids = [discretize_edge(e)[len(discretize_edge(e)) // 2] for e in edges]
    ordered = []                  # (edge in wire order, its support)
    for w in _wire_edges(wire):
        pm = discretize_edge(w)
        pm = pm[len(pm) // 2]
        i = min(range(len(edges)), key=lambda k: float(np.linalg.norm(mids[k] - pm)))
        ordered.append((w, supports[i]))
    try:
        mk = BRepFill_Filling(*FILL_SETTINGS)
        for w, sup in ordered:
            if sup is None:
                mk.Add(w, GeomAbs_C0, True)
            else:
                mk.Add(w, sup, GeomAbs_G1, True)
        for p in pts:
            mk.Add(_pnt(p))
        mk.Build()
        done = mk.IsDone()
        face = _as_face(mk.Face()) if done else None
    except Exception:
        done, face = False, None
    if not done or face is None or not is_valid(face) or fill_deviation(edges, face) > FILL_TOL:
        raise KernelError(FILL_FAILED)
    return (face, BRepTools_History()) if with_history else face


def fill_deviation(edges, face):
    """The largest distance from the input `edges` to the boundary of `face` (sampled along each edge)."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepTools import BRepTools
    outer = BRepTools.OuterWire_s(face)
    worst = 0.0
    for e in edges:
        pts = discretize_edge(e)
        for p in pts[np.linspace(0, len(pts) - 1, min(len(pts), 12)).astype(int)]:
            d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(_pnt(p)).Vertex(), outer)
            d.Perform()
            worst = max(worst, d.Value())
    return worst


def fill_tangent_error(face, edges, support, samples=5):
    """The angle (rad) between the normal of `face` and of `support` at `samples` points along `edges`
    (normals compared as lines, so a reversed face counts as the same)."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf

    def normal(f, p):
        pr = GeomAPI_ProjectPointOnSurf(_pnt(p), BRep_Tool.Surface_s(f))
        u, v = pr.LowerDistanceParameters()
        pt, n = gp_Pnt(), gp_Vec()
        BRepGProp_Face(f).Normal(u, v, pt, n)
        n = _np(n)
        return n / max(float(np.linalg.norm(n)), 1e-12)
    pts = np.vstack([discretize_edge(e) for e in edges])
    worst = 0.0
    for p in pts[np.linspace(0, len(pts) - 1, samples).astype(int)]:
        worst = max(worst, math.acos(min(1.0, abs(float(normal(face, p) @ normal(support, p))))))
    return worst


# --------------------------------------------------------------------------- tessellation

@dataclass
class Mesh:
    verts: np.ndarray        # (n,3) float32
    normals: np.ndarray      # (n,3) float32
    tris: np.ndarray         # (m,3) uint32
    tri_face: np.ndarray     # (m,) face index for each triangle
    face_ranges: list = field(default_factory=list)   # per face (tri_start, tri_end)
    edge_polys: list = field(default_factory=list)    # per edge (k,3) polyline
    vertices: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.float32))   # shape vertices, in order
    face_edges: list = field(default_factory=list)    # per face: indices (into edge_polys) of its boundary edges


def tessellate(shape, lin_defl: float | None = None, ang_defl: float = 0.2, edges_too: bool = True) -> Mesh:
    """Triangulate every face. edges_too=False skips the edge polylines (mesh export does not need them)."""
    if lin_defl is None:
        lo, hi = bbox(shape)
        lin_defl = max(np.linalg.norm(hi - lo) * 0.001, 1e-3)
    BRepMesh_IncrementalMesh(shape, lin_defl, False, ang_defl, True)
    V, N, T, TF, ranges = [], [], [], [], []
    base = 0
    for fi, f in enumerate(faces(shape)):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        start = sum(len(t) for t in T)
        if tri is None:
            ranges.append((start, start))
            continue
        trsf = loc.Transformation()
        nb = tri.NbNodes()
        pts = np.empty((nb, 3))
        for i in range(1, nb + 1):
            p = tri.Node(i).Transformed(trsf)
            pts[i - 1] = (p.X(), p.Y(), p.Z())
        nt = tri.NbTriangles()
        idx = np.empty((nt, 3), dtype=np.int64)
        rev = f.Orientation() == TopAbs_REVERSED
        for i in range(1, nt + 1):
            a, b, c = tri.Triangle(i).Get()
            idx[i - 1] = (a - 1, c - 1, b - 1) if rev else (a - 1, b - 1, c - 1)
        # vertex normals from the surface if available, else from triangles
        nrm = np.zeros((nb, 3))
        if tri.HasNormals():
            for i in range(1, nb + 1):
                d = tri.Normal(i)
                nrm[i - 1] = (d.X(), d.Y(), d.Z())
            if not loc.IsIdentity():
                R = np.array([[trsf.Value(r, c) for c in range(1, 4)] for r in range(1, 4)])
                nrm = nrm @ R.T
            if rev:
                nrm = -nrm
        else:
            fn = np.cross(pts[idx[:, 1]] - pts[idx[:, 0]], pts[idx[:, 2]] - pts[idx[:, 0]])
            for k in range(3):
                np.add.at(nrm, idx[:, k], fn)
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        ln[ln == 0] = 1
        nrm /= ln
        V.append(pts)
        N.append(nrm)
        T.append(idx + base)
        TF.append(np.full(nt, fi, dtype=np.int32))
        ranges.append((start, start + nt))
        base += nb
    seams = seam_edge_indices(shape) if edges_too else set()
    polys = []
    for i, e in enumerate(edges(shape) if edges_too else []):
        # seam edges (where a cylinder closes on itself) aren't shown or pickable, as in Onshape
        polys.append(np.zeros((0, 3), np.float32) if i in seams else discretize_edge(e, lin_defl))
    vpts = np.array(_vertices_np(shape), np.float32).reshape(-1, 3)
    fe = face_edge_lists(shape)
    if V:
        mesh = Mesh(np.vstack(V).astype(np.float32), np.vstack(N).astype(np.float32),
                    np.vstack(T).astype(np.uint32), np.concatenate(TF), ranges, polys, vpts, fe)
    else:
        mesh = Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32),
                    np.zeros((0, 3), np.uint32), np.zeros(0, np.int32), ranges, polys, vpts, fe)
    return mesh


def face_edge_lists(shape) -> list:
    """For each face (in faces() order), the indices (in edges() order) of the edges around it — used to draw
    a hovered face as an outline."""
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    out = []
    for f in faces(shape):
        idx = []
        exp = TopExp_Explorer(f, TopAbs_EDGE)
        while exp.More():
            i = m.FindIndex(exp.Current()) - 1
            if i >= 0 and i not in idx:
                idx.append(i)
            exp.Next()
        out.append(idx)
    return out


def seam_edge_indices(shape) -> set:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    out = set()
    for f in faces(shape):
        exp = TopExp_Explorer(f, TopAbs_EDGE)
        while exp.More():
            e = _as_edge(exp.Current())
            try:
                if BRep_Tool.IsClosed_s(e, f):
                    out.add(m.FindIndex(e) - 1)
            except Exception:
                pass
            exp.Next()
    return out


EDGE_ANGLE = 0.1      # radians: the most an outline may turn between two points (~63 segments per circle)


def discretize_edge(e, defl=0.01, ang_defl=EDGE_ANGLE) -> np.ndarray:
    """Points along an edge. Both a distance limit (defl) and an angle limit apply, so a small hole on a big
    part is still drawn round rather than as a polygon (the distance limit alone scales with the part)."""
    try:
        ad = BRepAdaptor_Curve(e)
        if ad.GetType() == GeomAbs_Line:
            return np.array([_np(ad.Value(ad.FirstParameter())), _np(ad.Value(ad.LastParameter()))], np.float32)
        try:
            d = GCPnts_TangentialDeflection(ad, ang_defl, defl, 2)
            if d.NbPoints() >= 2:
                return np.array([_np(d.Value(i)) for i in range(1, d.NbPoints() + 1)], np.float32)
        except Exception:
            pass
        d = GCPnts_QuasiUniformDeflection(ad, defl)
        if d.IsDone() and d.NbPoints() >= 2:
            return np.array([_np(d.Value(i)) for i in range(1, d.NbPoints() + 1)], np.float32)
        a, b = ad.FirstParameter(), ad.LastParameter()
        return np.array([_np(ad.Value(a + (b - a) * t)) for t in np.linspace(0, 1, 32)], np.float32)
    except Exception:
        return np.zeros((0, 3), np.float32)


# --------------------------------------------------------------------------- import / export

def export_step(shapes, path):
    w = STEPControl_Writer()
    for s in shapes:
        w.Transfer(s, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise KernelError("STEP export failed")


def export_stl(shapes, path, defl=0.05, angle=0.3, ascii=False):
    shp = compound(shapes) if len(shapes) != 1 else shapes[0]
    BRepMesh_IncrementalMesh(shp, defl, False, angle, True)
    w = StlAPI_Writer()
    w.ASCIIMode = bool(ascii)   # binary by default: ~6x smaller than ASCII
    if not w.Write(shp, str(path)):
        raise KernelError("STL export failed")


_STEP_CACHE: dict = {}      # sha1 of a STEP file's bytes -> import result (filled by the background reader too)


def step_cache_key(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


def remember_step(key, result):
    if len(_STEP_CACHE) > 6:
        _STEP_CACHE.pop(next(iter(_STEP_CACHE)))
    _STEP_CACHE[key] = result


def import_step_with_colors(path, progress=None):
    """Read a STEP file keeping part names and colours.
    Returns a list of (solid, name or None, (r, g, b) or None, {face index: (r, g, b)}).
    progress(stage_text) is called between the slow stages, if given."""
    with open(path, "rb") as fh:
        key = step_cache_key(fh.read())
    hit = _STEP_CACHE.get(key)
    if hit is not None:
        return list(hit)
    out = _import_step_with_colors(path, progress)
    remember_step(key, out)
    return list(out)


def _import_step_with_colors(path, progress=None):
    progress = progress or (lambda _t: None)
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import (XCAFDoc_DocumentTool, XCAFDoc_ColorTool, XCAFDoc_ShapeTool,
                             XCAFDoc_ColorGen, XCAFDoc_ColorSurf)
    from OCP.TDF import TDF_Label
    from OCP.TDataStd import TDataStd_Name
    from OCP.Quantity import Quantity_Color
    from OCP.TopLoc import TopLoc_Location
    try:
        from OCP.TDF import TDF_LabelSequence
    except ImportError:
        from OCP.OCP.collections import Sequence_TDF_Label as TDF_LabelSequence

    doc = TDocStd_Document(TCollection_ExtendedString("freeshape"))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    progress("Reading the STEP file")
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise KernelError(f"Could not read STEP file {path}")
    progress("Converting it into solids")
    if not reader.Transfer(doc):
        raise KernelError("STEP transfer failed")
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def static(cls, name):
        return getattr(cls, name + "_s", None) or getattr(cls, name)

    is_ref = static(XCAFDoc_ShapeTool, "IsReference")
    is_asm = static(XCAFDoc_ShapeTool, "IsAssembly")
    get_ref = static(XCAFDoc_ShapeTool, "GetReferredShape")
    get_comps = static(XCAFDoc_ShapeTool, "GetComponents")
    get_shape = static(XCAFDoc_ShapeTool, "GetShape")
    get_loc = static(XCAFDoc_ShapeTool, "GetLocation")
    get_subs = static(XCAFDoc_ShapeTool, "GetSubShapes")
    label_color = static(XCAFDoc_ColorTool, "GetColor")

    def to_srgb(x):
        # OpenCascade keeps colours linear; the screen (and the original CAD app) shows sRGB
        x = min(max(float(x), 0.0), 1.0)
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055

    def color_of(label):
        for t in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
            c = Quantity_Color()
            try:
                if label_color(label, t, c):
                    return (to_srgb(c.Red()), to_srgb(c.Green()), to_srgb(c.Blue()))
            except Exception:
                pass
        return None

    def name_of(label):
        attr = TDataStd_Name()
        try:
            if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
                n = attr.Get().ToExtString().strip()
                # exporters often write placeholders like "NONE" or "SOLID" — treat those as no name
                if not n or n.upper() in ("NONE", "UNNAMED", "NO NAME", "SOLID", "BODY", "SHAPE", "COMPOUND") \
                        or n.upper().startswith(("=>[", "OPEN CASCADE")):
                    return None
                return n
        except Exception:
            return None
        return None

    out = []

    def dominant_sub_color(label, sub_shape):
        """Colour covering the most area among coloured faces/solids under a label."""
        seq = TDF_LabelSequence()
        get_subs(label, seq)
        weights = {}
        for i in range(1, seq.Length() + 1):
            sl = seq.Value(i)
            c = color_of(sl)
            if c is None:
                continue
            sh = get_shape(sl)
            for f in faces(sh):
                g = GProp_GProps()
                BRepGProp.SurfaceProperties_s(f, g)
                weights[c] = weights.get(c, 0.0) + g.Mass()
        return max(weights, key=weights.get) if weights else None

    def walk(label, loc, color, name, depth=0):
        if depth > 64:
            return
        if is_ref(label):
            ref = TDF_Label()
            get_ref(label, ref)
            l2 = loc.Multiplied(get_loc(label))
            walk(ref, l2, color_of(label) or color, name_of(label) or name, depth + 1)
            return
        own_name = name_of(label) or name
        own_color = color_of(label) or color
        if is_asm(label):
            comps = TDF_LabelSequence()
            get_comps(label, comps, False)
            for i in range(1, comps.Length() + 1):
                walk(comps.Value(i), loc, own_color, own_name, depth + 1)
            return
        shape = get_shape(label)
        if shape.IsNull():
            return
        sols = solids(shape) or [shape]
        sub_color = None if own_color else dominant_sub_color(label, shape)
        for k, sol in enumerate(sols):
            c = own_color
            if c is None:
                sl = TDF_Label()
                try:
                    if st.FindSubShape(label, sol, sl):
                        c = color_of(sl)
                except Exception:
                    pass
            nm = own_name if len(sols) == 1 or not own_name else f"{own_name} ({k + 1})"
            # per-face colours (only kept when they differ from the part's main colour)
            fcols = {}
            sol_faces = faces(sol)
            raw = {}
            for fi, fc in enumerate(sol_faces):
                col = None
                for t in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
                    q = Quantity_Color()
                    try:
                        if ct.GetColor(fc, t, q):
                            col = (to_srgb(q.Red()), to_srgb(q.Green()), to_srgb(q.Blue()))
                            break
                    except Exception:
                        pass
                if col is not None:
                    raw[fi] = col
            if c is None and raw and len(raw) == len(sol_faces) and len(set(raw.values())) == 1:
                c = next(iter(raw.values()))          # every face the same colour: that's the part colour
            elif c is None and not raw:
                c = sub_color
            # colour only on some faces: the rest keep the default grey, like in the original CAD app
            for fi, col in raw.items():
                if c is None or max(abs(a - b) for a, b in zip(col, c)) > 0.02:
                    fcols[fi] = col
            out.append((sol.Moved(loc) if not loc.IsIdentity() else sol, nm, c, fcols))

    progress("Collecting parts, names and colours")
    free = TDF_LabelSequence()
    st.GetFreeShapes(free)
    for i in range(1, free.Length() + 1):
        walk(free.Value(i), TopLoc_Location(), None, None)
    if not out:
        raise KernelError("No solids found in STEP file")
    return out


def export_brep(shapes, path):
    """OpenCascade's own BREP format: the exact shapes, written as the kernel holds them (no translation, so
    nothing is lost or healed on the way — the most faithful way to move a model between OCC programs such as
    FreeCAD). It keeps geometry only: no part names or colours. Several parts go in one compound."""
    from OCP.BRepTools import BRepTools
    shp = compound(shapes) if len(shapes) != 1 else shapes[0]
    if not BRepTools.Write_s(shp, str(path)):
        raise KernelError("BREP export failed")


def import_brep(path):
    """Read a BREP file: each solid in it becomes a part (or the whole shape if it holds no solids)."""
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    shp = TopoDS_Shape()
    if not BRepTools.Read_s(shp, str(path), BRep_Builder()) or shp.IsNull():
        raise KernelError(f"Could not read BREP file {path}")
    sols = solids(shp)
    return sols if sols else [shp]


def import_step(path):
    r = STEPControl_Reader()
    if r.ReadFile(str(path)) != IFSelect_RetDone:
        raise KernelError(f"Could not read STEP file {path}")
    r.TransferRoots()
    shp = r.OneShape()
    sols = solids(shp)
    return sols if sols else [shp]


# --------------------------------------------------------------------------- sweep / loft / draft / split / move

def make_wire(edge_list):
    """Join edges into one wire, ordering them end-to-end (they may be picked in any order)."""
    if not edge_list:
        raise KernelError("Select a path")
    def ends(e):
        p = discretize_edge(e)
        return p[0].astype(float), p[-1].astype(float)
    remaining = list(edge_list)
    chain = [remaining.pop(0)]
    a, b = ends(chain[0])
    head, tail = a, b
    tol = max(1e-4, 1e-6 * float(np.linalg.norm(b - a)))
    changed = True
    while remaining and changed:
        changed = False
        for e in list(remaining):
            p, q = ends(e)
            if np.linalg.norm(p - tail) < tol * 100 or np.linalg.norm(q - tail) < tol * 100:
                chain.append(e)
                tail = q if np.linalg.norm(p - tail) < np.linalg.norm(q - tail) else p
            elif np.linalg.norm(p - head) < tol * 100 or np.linalg.norm(q - head) < tol * 100:
                chain.insert(0, e)
                head = q if np.linalg.norm(p - head) < np.linalg.norm(q - head) else p
            else:
                continue
            remaining.remove(e)
            changed = True
    if remaining:
        raise KernelError("Path edges must connect end to end")
    mk = BRepBuilderAPI_MakeWire()
    for e in chain:
        mk.Add(e)
    if not mk.IsDone():
        raise KernelError("Path edges must connect end to end")
    return mk.Wire()


def chain_wires(edge_list):
    """One wire per connected chain of `edge_list` (edges that meet end to end), in the order the chains first
    appear. A chain that branches raises, as `make_wire` does."""
    ends = []
    for e in edge_list:
        p = discretize_edge(e)
        ends.append((p[0].astype(float), p[-1].astype(float)))
    scale = max([float(np.linalg.norm(b - a)) for a, b in ends] + [1.0])
    tol = max(1e-4, 1e-6 * scale) * 100
    group = list(range(len(edge_list)))

    def root(i):
        while group[i] != i:
            i = group[i]
        return i
    for i in range(len(edge_list)):
        for j in range(i):
            if any(np.linalg.norm(a - b) < tol for a in ends[i] for b in ends[j]):
                group[root(i)] = root(j)
    chains = {}
    for i, e in enumerate(edge_list):
        chains.setdefault(root(i), []).append(e)
    return [make_wire(c) for c in chains.values()]


THIN_TOO_THICK = "The thickness is larger than this shape allows"


def _travel_chain(wire):
    """[[edge, points]] of `wire` in travel order, each edge's points running along the travel. The travel
    starts at the free end of an open wire; which way a closed wire runs is the first edge's."""
    from OCP.BRepTools import BRepTools_WireExplorer
    items = []
    ex = BRepTools_WireExplorer(wire)
    while ex.More():
        e = ex.Current()
        items.append([e, discretize_edge(e).astype(float)])
        ex.Next()
    if not items or any(len(pts) < 2 for _, pts in items):
        raise KernelError("The curve has no edges to make thin")
    if len(items) == 1:
        if items[0][0].Orientation() == TopAbs_REVERSED:
            items[0][1] = items[0][1][::-1]
        return items
    a, b = items[0][1], items[1][1]
    near = lambda p, q: min(np.linalg.norm(p - q[0]), np.linalg.norm(p - q[-1]))
    if near(a[0], b) < near(a[-1], b):
        items[0][1] = a[::-1]
    for i in range(1, len(items)):
        prev, pts = items[i - 1][1][-1], items[i][1]
        if np.linalg.norm(prev - pts[0]) > np.linalg.norm(prev - pts[-1]):
            items[i][1] = pts[::-1]
    return items


def _flat(plane, pts):
    d = np.asarray(pts, float) - plane.origin
    return np.stack([d @ plane.xdir, d @ plane.ydir], axis=1)


def _signed_area(uv):
    x, y = uv[:, 0], uv[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _side_of(uv, q):
    """+1 when the 2-D point q is right of the polyline `uv` (looking along it), -1 when left."""
    best, side = 1e300, 0.0
    for a, b in zip(uv[:-1], uv[1:]):
        ab = b - a
        L2 = float(ab @ ab)
        if L2 < 1e-18:
            continue
        t = min(1.0, max(0.0, float((q - a) @ ab) / L2))
        d = float(np.linalg.norm(q - (a + t * ab)))
        if d < best - 1e-12:
            best, side = d, ab[0] * (q[1] - a[1]) - ab[1] * (q[0] - a[0])
    return 1 if side < 0 else -1


def _offset_one_side(plane, wire, chain, uv, closed, right, dist):
    """`wire` offset by `dist` towards its right (looking along the travel, `right` True) or left, with
    sharp corners: (offset wire, the offset's history). The history maps each edge of `wire` to its image.

    BRepOffsetAPI_MakeOffset(wire, GeomAbs_Intersection) takes its sign from a plane it finds from the wire
    itself, so which side +d is on is not known in advance. A probe with a distance too small to harm
    finds out, so the real offset only ever goes to the side the circle check below has cleared (OCC
    crashes when an offset reaches past a circle's centre). A straight chain on one line has no plane
    for OCC to find, and a lone line crashes it, so that case is a plain move."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Intersection
    n = plane.normal
    straight = all(BRepAdaptor_Curve(e).GetType() == GeomAbs_Line for e, _ in chain)
    pts = np.vstack([c[1] for c in chain])
    if straight and not closed:
        u = pts[-1] - pts[0]
        u = u / np.linalg.norm(u)
        if float(np.max(np.linalg.norm(np.cross(pts - pts[0], u), axis=1))) < 1e-7:
            t = _trsf_translate((-np.cross(n, u) if right else np.cross(n, u)) * dist)    # left of u is n x u
            moved, mk = _placed(wire, t)
            return _as_wire(moved), _maker_history(mk, [wire])
    radii = []
    for e, ep in chain:
        ad = BRepAdaptor_Curve(e)
        if ad.GetType() != GeomAbs_Circle:
            continue
        r = ad.Circle().Radius()
        radii.append(r)
        m = min(len(ep) // 2, len(ep) - 2)
        a2, b2 = _flat(plane, ep[m:m + 2])
        c2 = _flat(plane, _np(ad.Circle().Location())[None, :])[0]
        centre_right = (b2[0] - a2[0]) * (c2[1] - a2[1]) - (b2[1] - a2[1]) * (c2[0] - a2[0]) < 0
        if centre_right == right and dist >= r - 1e-9:
            raise KernelError(THIN_TOO_THICK)
    probe_d = min(dist, 1e-3 * max(1e-3, min(radii + [float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))])))

    def make(sign, d):
        mk = BRepOffsetAPI_MakeOffset(wire, GeomAbs_Intersection, not closed)
        mk.Perform(sign * d)
        res = mk.Shape()
        ws = _sub_shapes(res, TopAbs_WIRE) if res.ShapeType() != TopAbs_WIRE else [res]
        if not ws and res.ShapeType() == TopAbs_EDGE:
            ws = [BRepBuilderAPI_MakeWire(_as_edge(res)).Wire()]
        return mk, ws

    def side(ws):
        if len(ws) != 1:
            return None
        ruv = _flat(plane, np.vstack([c[1] for c in _travel_chain(_as_wire(ws[0]))]))
        if closed:                     # outside is the bigger loop
            return (1 if abs(_signed_area(ruv)) > abs(_signed_area(uv)) else -1) * (1 if _signed_area(uv) > 0 else -1)
        return _side_of(uv, ruv[0])
    try:
        mk, ws = make(1, probe_d)
        got = side(ws)
        if got is None:
            raise KernelError(THIN_TOO_THICK)
        sign = 1 if (got > 0) == right else -1
        if not (probe_d >= dist and sign == 1):      # else the probe was the whole offset
            mk, ws = make(sign, dist)
        if len(ws) != 1 or side(ws) != (1 if right else -1):
            raise KernelError(THIN_TOO_THICK)
    except KernelError:
        raise
    except Exception as exc:            # OCC raises Standard_Failure for a wire it cannot offset
        raise KernelError(THIN_TOO_THICK) from exc
    return _as_wire(ws[0]), _maker_history(mk, [wire])


def _open_wire_ends(wire):
    """The two end vertices of an open wire: the vertices only one of its edges has."""
    seen = []                                   # [vertex, number of edges that have it]
    for e in edges(wire):
        for v in _sub_shapes(e, TopAbs_VERTEX):
            for item in seen:
                if item[0].IsSame(v):
                    item[1] += 1
                    break
            else:
                seen.append([v, 1])
    ends = [v for v, n in seen if n == 1]
    if len(ends) != 2:
        raise KernelError(THIN_TOO_THICK)
    return ends[0], ends[1]


def thin_profile(plane, wire, t1, t2):
    """A thin face in `plane` from `wire`, a closed or open chain of sketch curves: (face, offset history, cap
    edges). The face is the strip between the wire offset by `t1` and by `t2` on its other side, with sharp
    corners.

    Closed wire: `t1` outside, `t2` inside; the face is the ring between them. Open wire: `t1` on the right of
    the travel along the wire (looking down the plane's normal), `t2` on the left; the two ends are closed with
    straight edges square to the wire, which are the returned cap edges (none for a closed wire). A side of 0
    keeps the wire itself there.

    The history maps every edge and vertex of `wire` to its image in the face, to chain before the history of
    the sweep made from the face. A wall that folds into itself (no single offset wire, or a face that is
    not valid) raises "The thickness is larger than this shape allows"."""
    t1, t2 = float(t1), float(t2)
    if t1 < 0 or t2 < 0 or t1 + t2 < 1e-9:
        raise KernelError("The thickness must be more than zero")
    chain = _travel_chain(wire)
    pts = np.vstack([chain[0][1]] + [c[1][1:] for c in chain[1:]])
    uv = _flat(plane, pts)
    tol = 1e-6 * max(1.0, float(np.ptp(pts, axis=0).max()))
    closed = float(np.linalg.norm(pts[0] - pts[-1])) < tol
    if closed:
        ccw = _signed_area(uv) > 0              # the right of a counter-clockwise loop is outside
        sides = {True: t1 if ccw else t2, False: t2 if ccw else t1}
    else:
        sides = {True: t1, False: t2}
    made, hists = {}, []
    for right, dist in sides.items():
        if dist < 1e-9:
            made[right] = wire
            continue
        made[right], h = _offset_one_side(plane, wire, chain, uv, closed, right, dist)
        hists.append(h)
    pln = gp_Pln(plane.gp_ax3())

    def area_of(w):
        return _signed_area(_flat(plane, np.vstack([c[1] for c in _travel_chain(w)])))

    def facing(w, ccw_wanted):
        return w if (area_of(w) > 0) == ccw_wanted else _as_wire(w.Reversed())
    caps = []
    if closed:
        outer, inner = (made[True], made[False]) if ccw else (made[False], made[True])
        mf = BRepBuilderAPI_MakeFace(pln, facing(outer, True), True)
        mf.Add(facing(inner, False))
        face = mf.Face()
    else:
        ends = []
        for w in (made[True], made[False]):
            a, b = _open_wire_ends(w)           # the wire's own vertices, so the caps join it exactly
            if np.linalg.norm(_vertex_xyz(a) - pts[0]) + np.linalg.norm(_vertex_xyz(b) - pts[-1]) \
                    > np.linalg.norm(_vertex_xyz(b) - pts[0]) + np.linalg.norm(_vertex_xyz(a) - pts[-1]):
                a, b = b, a
            ends.append((a, b))
        caps = [BRepBuilderAPI_MakeEdge(_as_vertex(ends[0][0]), _as_vertex(ends[1][0])).Edge(),
                BRepBuilderAPI_MakeEdge(_as_vertex(ends[0][1]), _as_vertex(ends[1][1])).Edge()]
        loop = make_wire(edges(made[True]) + caps + edges(made[False]))
        face = BRepBuilderAPI_MakeFace(pln, facing(loop, True), True).Face()
    if not is_valid(face) or mass_props(face)["area"] < 1e-9:
        raise KernelError(THIN_TOO_THICK)
    return face, _chain(*hists), caps


def _pipe_shell(path_wire, wire):
    return _pipe_maker(path_wire, wire).Shape()


SWEEP_NO_DIRECTION = "Pick a direction for Lock profile direction"
SWEEP_KEEP_NEEDS_PLANE = "Keep profile orientation needs a flat profile"


def _profile_normal(wire):
    """(a point on, the unit normal of) the plane a profile wire lies in; None when it is not flat."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_FindPlane
    fp = BRepBuilderAPI_FindPlane(wire)
    if not fp.Found():
        return None
    pln = fp.Plane()
    return _wire_centre(wire), _np(pln.Axis().Direction())


def _set_pipe_mode(mk, wire, control):
    """The pipe maker's orientation mode. No control: corrected Frenet, the profile follows the path. `keep`:
    a fixed trihedron from the profile's plane, so every section stays parallel to the profile. `("lock", d)`:
    a constant binormal along `d`."""
    kind = control[0] if control else None
    if kind is None:
        mk.SetMode(False)                   # corrected Frenet: keeps the profile upright along the path
    elif kind == "keep":
        got = _profile_normal(wire)
        if got is None:
            raise KernelError(SWEEP_KEEP_NEEDS_PLANE)
        mk.SetMode(gp_Ax2(_pnt(got[0]), _dir(got[1])))
    elif kind == "lock":
        if control[1] is None or np.linalg.norm(np.asarray(control[1], float)) < 1e-9:
            raise KernelError(SWEEP_NO_DIRECTION)
        d = np.asarray(control[1], float)
        mk.SetMode(_dir(d / np.linalg.norm(d)))
    else:
        raise KernelError("Unknown profile control")


def _pipe_maker(path_wire, wire, solid=True, control=None):
    """The finished pipe-shell maker for `wire` swept along `path_wire`, made into a solid unless `solid` is
    false (a surface sweep keeps the open shell). `control` is None, ("keep",) or ("lock", direction)."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.BRepBuilderAPI import BRepBuilderAPI_RightCorner
    mk = BRepOffsetAPI_MakePipeShell(path_wire)
    _set_pipe_mode(mk, wire, control)
    mk.SetTransitionMode(BRepBuilderAPI_RightCorner)   # clean mitred joins at sharp corners
    mk.Add(wire, False, False)
    mk.Build()
    if not mk.IsDone():
        raise KernelError("Sweep failed — the path may be too tight for the profile")
    if solid:
        mk.MakeSolid()
    return mk


def _pipe_history(mk, face, wire):
    """What a pipe-shell maker did to its profile: each edge and vertex of `wire` generated its side
    faces and edges, and (given the profile `face`) the face generated the start and end caps. The
    maker's own FirstShape/LastShape take no argument, unlike a prism's."""
    hist = BRepTools_History()
    for kind in (TopAbs_EDGE, TopAbs_VERTEX):
        for s in _sub_shapes(wire, kind):
            try:
                for g in mk.Generated(s):
                    hist.AddGenerated(s, g)
            except Exception:
                pass            # an OCCT history failure degrades the names; it never fails the operation
    if face is not None:
        for cap in (mk.FirstShape, mk.LastShape):
            try:
                c = cap()
                if not c.IsNull() and not c.IsSame(face):
                    hist.AddGenerated(face, c)
            except Exception:
                pass
    return hist


def sweep(profile_faces, path_wire, with_history=False, control=None):
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_WIRE
    if not profile_faces:
        raise KernelError("Select a profile to sweep")
    surface = any(f.ShapeType() == TopAbs_WIRE for f in profile_faces)
    if surface and not all(f.ShapeType() == TopAbs_WIRE for f in profile_faces):
        raise KernelError("Sweep takes faces or curves, not both")
    if surface:
        return _sweep_surface(profile_faces, path_wire, with_history, control)
    out, steps = [], []
    for f in profile_faces:
        outer = BRepTools.OuterWire_s(f)
        try:
            mk = _pipe_maker(path_wire, outer, control=control)
            body = mk.Shape()
            hist = _pipe_history(mk, f, outer) if with_history else None
            exp = TopExp_Explorer(f, TopAbs_WIRE)
            while exp.More():                 # holes in the profile become tunnels
                w = TopoDS.Wire(exp.Current()) if hasattr(TopoDS, "Wire") else exp.Current()
                if not w.IsSame(outer):
                    tmk = _pipe_maker(path_wire, w, control=control)
                    if with_history:
                        body, cut_hist = cut(body, tmk.Shape(), with_history=True)
                        hist = _chain(hist, _pipe_history(tmk, None, w), cut_hist)
                    else:
                        body = cut(body, tmk.Shape())
                exp.Next()
        except KernelError:
            raise
        except Exception as exc:
            raise KernelError("Sweep failed — the path may be too tight for the profile") from exc
        out.append(body)
        steps.append(hist)
    if with_history:
        res, fused = fuse_all(out, with_history=True)
    else:
        res = fuse_all(out)
    if not solids(res):
        raise KernelError("Sweep didn't make a solid — the profile should be a closed region")
    if volume(res) < 0:
        res = res.Reversed()
    return (res, _chain(_chain(*steps), fused)) if with_history else res


def _sweep_surface(profile_wires, path_wire, with_history, control=None):
    """Each profile wire (open or closed) swept along `path_wire` into a shell of its own, with a face per
    profile edge and path segment. The shells are returned as they are (one shell, or a compound of them):
    no hole tunnels, no solid check, no volume reversal, and no sewing, so each keeps its history."""
    shells, steps = [], []
    for w in profile_wires:
        try:
            mk = _pipe_maker(path_wire, w, solid=False, control=control)
        except KernelError:
            raise
        except Exception as exc:
            raise KernelError("Sweep failed — the path may be too tight for the profile") from exc
        shells.append(mk.Shape())
        if with_history:
            steps.append(_pipe_history(mk, None, w))
    shape = shells[0] if len(shells) == 1 else compound(shells)
    return (shape, _chain(*steps)) if with_history else shape


def _wire_centre(w) -> np.ndarray:
    g = GProp_GProps()
    BRepGProp.LinearProperties_s(w, g)
    return _np(g.CentreOfMass())


def _loft_hole_sets(profile_faces):
    """The inner wires of the profiles grouped into one list per hole, each list in profile order;
    [] when a profile has no hole or the profiles differ in hole count (then a loft is outer only).

    A hole is matched to the hole of the next profile whose centre is nearest once both are projected
    onto the plane normal to the line between the two profiles' centres, so the sketch order of the
    circles does not matter and an offset between the profiles does not either."""
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_WIRE
    holes = []
    for f in profile_faces:
        outer = BRepTools.OuterWire_s(f)
        holes.append([_as_wire(w) for w in _sub_shapes(f, TopAbs_WIRE) if not w.IsSame(outer)])
    n = len(holes[0])
    if n == 0 or any(len(h) != n for h in holes):
        return []
    sets = [[w] for w in holes[0]]
    for i in range(1, len(profile_faces)):
        c0, c1 = face_info(profile_faces[i - 1])["c"], face_info(profile_faces[i])["c"]
        d = np.asarray(c1, float) - np.asarray(c0, float)
        if np.linalg.norm(d) < 1e-9:
            d = np.asarray(face_info(profile_faces[i])["n"], float)
        d = d / np.linalg.norm(d)

        def flat(w):
            p = _wire_centre(w)
            return p - np.dot(p, d) * d
        prev = [flat(s[-1]) for s in sets]
        nxt = [flat(w) for w in holes[i]]
        pairs = sorted((float(np.linalg.norm(a - b)), j, k) for j, a in enumerate(prev) for k, b in enumerate(nxt))
        used_j, used_k = set(), set()
        for _dist, j, k in pairs:          # nearest pairs first; each hole is taken once
            if j not in used_j and k not in used_k:
                sets[j].append(holes[i][k])
                used_j.add(j)
                used_k.add(k)
    return sets


def _thru_sections(wires, ruled, max_degree=None, solid=True):
    """ThruSections over `wires`; an entry may be a vertex (a point profile, first or last only).
    `max_degree` caps the degree of the surface along the loft; `solid` False (a surface loft, I7.8)
    leaves the ends open instead of capping them."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    mk = BRepOffsetAPI_ThruSections(bool(solid), bool(ruled), 1e-6)
    if max_degree:
        mk.SetMaxDegree(max_degree)
    for w in wires:
        if w.ShapeType() == TopAbs_VERTEX:
            mk.AddVertex(_as_vertex(w))
        else:
            mk.AddWire(w)
    try:
        mk.Build()
    except Exception as exc:
        raise KernelError("Loft failed — try profiles with similar shapes") from exc
    if not mk.IsDone():
        raise KernelError("Loft failed — try profiles with similar shapes")
    return mk


def _thru_sections_history(mk, wires, faces=None, from_point=False) -> BRepTools_History:
    """Each profile edge generated the loft face that runs from it; with `faces`, the first and last
    profile faces generated the two end caps (spec I1.7: FreeCAD's MapperThruSections). A loft that
    starts at a point (I7.7) has no face generated from the next profile's edges, so `from_point` asks
    `Generated` for the face that ends on each edge."""
    hist = BRepTools_History()
    for w in wires:
        for e in _sub_shapes(w, TopAbs_EDGE):
            try:
                g = mk.GeneratedFace(e)
                if g.IsNull() and from_point:
                    gen = mk.Generated(e)
                    g = gen.First() if gen.Size() else g
                if not g.IsNull():
                    hist.AddGenerated(e, g)
            except Exception:
                pass            # an OCCT history failure degrades the names; it never fails the operation
    if faces:
        for f, cap in ((faces[0], mk.FirstShape), (faces[-1], mk.LastShape)):
            if f is None:           # a point end has no cap
                continue
            try:
                c = cap()
                if not c.IsNull() and not c.IsSame(f):
                    hist.AddGenerated(f, c)
            except Exception:
                pass
    return hist


def loft(profile_faces, ruled=False, with_history=False, closed=False):
    """A loft through the profiles' outer wires. When every profile has the same number of holes,
    each matched set of hole wires is lofted the same way and cut out, as sweep cuts its tunnels.
    I7.7: a profile may be a vertex, the first or the last only (a point: no cap at that end); with
    `closed` the first profile is added again as the last, so the loft is a closed shell with no caps."""
    from OCP.BRepTools import BRepTools
    if len(profile_faces) < 2:
        raise KernelError("Select at least two profiles, in order")
    pts = [f.ShapeType() == TopAbs_VERTEX for f in profile_faces]
    if any(pts[1:-1]):
        raise KernelError("A point can only be the first or the last profile")
    if all(pts):
        raise KernelError("A loft needs at least one profile that is not a point")
    if closed:
        if len(profile_faces) < 3:
            raise KernelError("A closed loft needs at least three profiles")
        if any(pts):
            raise KernelError("A closed loft cannot have a point profile")
    if (closed or any(pts)) and any(len(_sub_shapes(f, TopAbs_WIRE)) > 1 for f, pt in zip(profile_faces, pts) if not pt):
        raise KernelError("A closed loft or a loft to a point needs profiles without holes")
    outers = [f if pt else BRepTools.OuterWire_s(f) for f, pt in zip(profile_faces, pts)]
    # A closed loft is cubic along the loft: through 4 circles the default degree 4 curve swings 8 percent
    # off the ring, the cubic stays within 2 percent of it.
    mk = _thru_sections(outers + [outers[0]] if closed else outers, ruled, 3 if closed else None)
    body = mk.Shape()
    if closed or any(pts):
        if body.ShapeType() != TopAbs_SOLID and solids(body):
            body = solids(body)[0]
        if volume(body) < 0:
            body = body.Reversed()
        if len(solids(body)) != 1 or not is_valid(body):
            raise KernelError("Loft failed — try profiles with similar shapes")
        if not with_history:
            return body
        caps = None if closed else [None if pt else f for f, pt in zip(profile_faces, pts)]
        return body, _thru_sections_history(mk, [o for o in outers if o.ShapeType() != TopAbs_VERTEX], caps, pts[0])
    hist = _thru_sections_history(mk, outers, profile_faces) if with_history else None
    for wires in _loft_hole_sets(profile_faces):
        tool = _thru_sections(wires, ruled)
        core = tool.Shape()
        if volume(core) < 0:
            core = core.Reversed()
        if with_history:
            body, cut_hist = cut(body, core, with_history=True)
            hist = _chain(hist, _thru_sections_history(tool, wires), cut_hist)
        else:
            body = cut(body, core)
        if len(solids(body)) != 1 or not is_valid(body):
            raise KernelError("Loft failed — a hole runs out through the side of the loft")
    return (body, hist) if with_history else body


def loft_surface(wires, ruled=False, closed=False, with_history=False):
    """A surface loft (I7.8): ThruSections(False, ruled) through `wires`, open or closed chains in order,
    with no caps and no solid. A shell of one face per edge of the first wire and per gap between wires.
    `closed` adds the first wire again as the last (three or more wires), as `loft` does. The shell is
    returned as made (the faces are already shared along their edges), so the history keeps its names;
    `apply_body` sews it to the surface parts it touches."""
    if len(wires) < 2:
        raise KernelError("Select at least two profiles, in order")
    if closed and len(wires) < 3:
        raise KernelError("A closed loft needs at least three profiles")
    mk = _thru_sections(list(wires) + [wires[0]] if closed else list(wires), ruled, 3 if closed else None,
                        solid=False)
    shape = mk.Shape()
    if not faces(shape) or solids(shape):
        raise KernelError("Loft failed — try profiles with similar shapes")
    return (shape, _thru_sections_history(mk, wires)) if with_history else shape


# Loft end conditions (I7.2). The route was chosen by spike I7.1 (scripts/spikes/op2_loft.result.txt):
#   OCP 7.9: DECISION loft skin=hermite skin_angle_deg=0.0000 gordon79=absent gordon80=absent one_guide=absent corner_guide=absent rational=absent
#   OCP 8.0: DECISION loft skin=hermite skin_angle_deg=0.0000 gordon79=absent gordon80=ok one_guide=isocurve corner_guide=ok rational=direct
# Hermite skinning is exact on both builds; ghost sections through ThruSections reached only 0.23
# degree, so only the hermite route is built.
LOFT_SKIN = "hermite"
LOFT_CONDS = ("normal", "tangent", "match_tangent")      # Normal to profile, Tangent to profile, Match tangent
LOFT_MATCH_ANGLE = 0.5      # degrees: the most a Match tangent loft's normal may differ from its adjacent face's
LOFT_MATCH_TOL = 1e-4       # mm: how close an adjacent face must lie to a profile curve to touch it
LOFT_MATCH_ROUNDS = 6       # knot refinements along the profile before Match tangent gives up
LOFT_MATCH_ERR = "Could not match the adjacent faces smoothly"


def _skin_arrays():
    """The OCCT collection classes the skinning route needs; their Python names differ per build."""
    try:
        from OCP.TColgp import TColgp_Array1OfPnt as P1, TColgp_Array2OfPnt as P2
        from OCP.TColStd import TColStd_Array1OfReal as R1, TColStd_Array1OfInteger as I1
        from OCP.TopTools import TopTools_SequenceOfShape as Seq
    except ImportError:  # OCP 8.0
        from OCP.OCP.collections import (Array1_gp_Pnt as P1, Array2_gp_Pnt as P2, Array1_double as R1,
                                         Array1_int as I1, Sequence_TopoDS_Shape as Seq)
    return P1, P2, R1, I1, Seq


def _bspline_basis(knots, p, t, ders=0):
    """Values (row 0) and derivatives (rows 1..ders) at t of every degree-p B-spline basis function on
    `knots` (the full, multiplicity-expanded knot vector)."""
    k = np.asarray(knots, float)
    m = len(k) - 1
    lvl = [np.zeros(m)]
    span = max(j for j in range(m) if k[j] < k[j + 1]) if t >= k[-1] else \
        next(j for j in range(m) if k[j] <= t < k[j + 1])
    lvl[0][span] = 1.0
    for d in range(1, p + 1):
        prev, cur = lvl[-1], np.zeros(m - d)
        for i in range(m - d):
            a, b = k[i + d] - k[i], k[i + d + 1] - k[i + 1]
            cur[i] = ((t - k[i]) / a * prev[i] if a > 0 else 0.0) + ((k[i + d + 1] - t) / b * prev[i + 1] if b > 0 else 0.0)
        lvl.append(cur)

    def lift(d, lower):            # d/dt of the degree-d functions, from (derivatives of) degree d-1 ones
        out = np.zeros(len(lower) - 1)
        for i in range(len(out)):
            a, b = k[i + d] - k[i], k[i + d + 1] - k[i + 1]
            out[i] = d * ((lower[i] / a if a > 0 else 0.0) - (lower[i + 1] / b if b > 0 else 0.0))
        return out
    rows = [lvl[p]]
    if ders >= 1:
        rows.append(lift(p, lvl[p - 1]))
    if ders >= 2:
        rows.append(lift(p, lift(p - 1, lvl[p - 2])) if p >= 2 else np.zeros(len(lvl[p])))
    return rows


class _Section:
    """One compatible profile wire as B-spline curves, one per edge, in wire order, each over [0, 1]."""

    def __init__(self, wire):
        from OCP.BRepTools import BRepTools_WireExplorer
        self.wire = wire
        self.edges, self.curves = [], []
        ex = BRepTools_WireExplorer(wire)
        while ex.More():
            e = ex.Current()
            self.edges.append(e)
            self.curves.append(self._curve(e))
            ex.Next()

    @staticmethod
    def _curve(edge):
        from OCP.Geom import Geom_TrimmedCurve
        from OCP.GeomConvert import GeomConvert, GeomConvert_ApproxCurve
        from OCP.GeomAbs import GeomAbs_C2
        ad = BRepAdaptor_Curve(edge)
        r = BRep_Tool.Curve_s(edge, 0.0, 1.0)
        c = r[0] if isinstance(r, tuple) else r
        bs = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(c, ad.FirstParameter(), ad.LastParameter()))
        if bs.IsPeriodic():
            bs.SetNotPeriodic()
        if edge.Orientation() == TopAbs_REVERSED:
            bs.Reverse()
        if bs.IsRational():         # arcs, circles, conics: non-rational within 1e-6 (the card's tolerance)
            ap = GeomConvert_ApproxCurve(bs, 1e-6, GeomAbs_C2, 200, 8)
            if not ap.HasResult():
                raise KernelError("Loft failed — a profile curve could not be prepared for the end condition")
            bs = ap.Curve()
        return _bspline_data(bs)


def _bspline_data(bs):
    """(poles (n, 3), distinct knots normalised to [0, 1], multiplicities, degree) of a Geom_BSplineCurve."""
    kn = np.array([bs.Knot(i) for i in range(1, bs.NbKnots() + 1)])
    kn = (kn - kn[0]) / (kn[-1] - kn[0])
    return {"poles": np.array([_np(bs.Pole(i)) for i in range(1, bs.NbPoles() + 1)]),
            "knots": kn, "mults": [bs.Multiplicity(i) for i in range(1, bs.NbKnots() + 1)],
            "deg": bs.Degree()}


def _bspline_make(d):
    from OCP.Geom import Geom_BSplineCurve
    P1, _P2, R1, I1, _Seq = _skin_arrays()
    poles, kn, mu = P1(1, len(d["poles"])), R1(1, len(d["knots"])), I1(1, len(d["knots"]))
    for i, p in enumerate(d["poles"]):
        poles.SetValue(i + 1, _pnt(p))
    for i, (k, m) in enumerate(zip(d["knots"], d["mults"])):
        kn.SetValue(i + 1, float(k))
        mu.SetValue(i + 1, int(m))
    return Geom_BSplineCurve(poles, kn, mu, int(d["deg"]))


def _same_knots(curves):
    """The curves of one edge column brought to one degree and one knot vector (exact: degree raising
    and knot insertion do not move the curve)."""
    deg = max(c["deg"] for c in curves)
    bss = [_bspline_make(c) for c in curves]
    for b in bss:
        if b.Degree() < deg:
            b.IncreaseDegree(deg)
    want = {}
    for b in bss:
        for i in range(1, b.NbKnots() + 1):
            u = round(b.Knot(i), 12)
            want[u] = max(want.get(u, 0), b.Multiplicity(i))
    for b in bss:
        for u, m in sorted(want.items()):
            if 0.0 < u < 1.0:
                b.InsertKnot(u, m, 1e-10, False)
    out = [_bspline_data(b) for b in bss]
    n = len(out[0]["poles"])
    if any(len(o["poles"]) != n or len(o["knots"]) != len(out[0]["knots"]) for o in out):
        raise KernelError("Loft failed — the profiles could not be matched edge for edge")
    return out


def _wire_turn(points, normal):
    """+1 when the closed polygon `points` runs anticlockwise about `normal`, else -1."""
    a = sum(np.cross(points[i], points[(i + 1) % len(points)]) for i in range(len(points)))
    return 1.0 if float(np.dot(a, normal)) >= 0 else -1.0


def _outward(cols, normal):
    """Per column, per pole: the unit outward in-plane normal of the profile curve at that pole's
    Greville abscissa. A pole on a corner gets the mitre of its two edges' normals, the same in both
    columns, so neighbouring side faces still share their edge."""
    samples = [p for c in cols for p in c["poles"]]
    turn = _wire_turn(samples, normal)
    out, ends = [], []
    for c in cols:
        bs = _bspline_make(c)
        flat = np.repeat(c["knots"], c["mults"])
        deg = c["deg"]
        dirs = []
        for j in range(len(c["poles"])):
            g = float(np.mean(flat[j + 1:j + deg + 1]))
            pt, v = gp_Pnt(), gp_Vec()
            bs.D1(g, pt, v)
            t = _np(v)
            t = t - np.dot(t, normal) * normal
            nrm = np.cross(t, normal) * turn
            ln = np.linalg.norm(nrm)
            dirs.append(nrm / ln if ln > 1e-12 else np.zeros(3))
        out.append(dirs)
        ends.append((dirs[0], dirs[-1]))
    for k in range(len(cols)):
        a, b = ends[k - 1][1], ends[k][0]          # last pole of the previous column meets this first pole
        s = 1.0 + float(np.dot(a, b))
        mitre = (a + b) / s if s > 1e-6 else b
        out[k - 1][-1] = mitre
        out[k][0] = mitre
    return out


# Match tangent (I7.5): the end derivative of each column follows d(u), the direction that continues
# the adjacent face across the profile curve (in the face's tangent plane, square to the curve).

def _point_face_distance(p, face):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    return BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(_pnt(p)).Vertex(), face).Value()


def _face_normal_at(face, p):
    """The unit normal of the face's surface at the point nearest p (as the surface has it, unflipped)."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.GeomLProp import GeomLProp_SLProps
    surf = BRep_Tool.Surface_s(face)
    u, v = GeomAPI_ProjectPointOnSurf(_pnt(p), surf).LowerDistanceParameters()
    props = GeomLProp_SLProps(surf, u, v, 1, 1e-9)
    if not props.IsNormalDefined():
        raise KernelError(LOFT_MATCH_ERR)
    return _np(props.Normal())


def _match_setup(curves, faces, into):
    """Which adjacent face each curve of an end section continues: per curve (face, sign) or None when
    no face touches it, `sign` turning n x t away from the face. A face that touches no curve is refused."""
    used, out = set(), []
    for c in curves:
        bs = _bspline_make(c)
        pts = [_np(bs.Value(float(t))) for t in np.linspace(0.0, 1.0, 5)]
        hit = next((i for i, f in enumerate(faces)
                    if all(_point_face_distance(p, f) < LOFT_MATCH_TOL for p in pts)), None)
        if hit is None:
            out.append(None)
            continue
        used.add(hit)
        pt, v = gp_Pnt(), gp_Vec()
        bs.D1(0.5, pt, v)
        p, t = _np(pt), _np(v)
        d = np.cross(_face_normal_at(faces[hit], p), t)
        d /= np.linalg.norm(d)
        eps = max(1e-3 * np.linalg.norm(t), 1e-3)
        ahead, behind = _point_face_distance(p + eps * d, faces[hit]), _point_face_distance(p - eps * d, faces[hit])
        if abs(ahead - behind) > 0.1 * eps:          # the face lies behind the step that leaves it
            sign = 1.0 if ahead > behind else -1.0
        else:
            sign = 1.0 if float(np.dot(d, into)) >= 0 else -1.0
        out.append((faces[hit], sign))
    if len(used) < len(faces):
        raise KernelError("Pick faces that touch the profile")
    return out


def _match_dir(bs, entry, into, u):
    """(d, n) at parameter u of curve `bs`: the unit continuing direction and the plane it must stay in
    (the face's tangent plane; with no face, Normal to profile: d = into)."""
    pt, v = gp_Pnt(), gp_Vec()
    bs.D1(float(u), pt, v)
    t = _np(v)
    if entry is None:
        n = np.cross(t, into)
        return into, n / np.linalg.norm(n)
    face, sign = entry
    n = _face_normal_at(face, _np(pt))
    d = sign * np.cross(n, t)
    return d / np.linalg.norm(d), n


def _match_samples(c, per_span):
    kn = list(c["knots"])
    return np.unique(np.concatenate([np.linspace(a, b, per_span + 1) for a, b in zip(kn, kn[1:])]))


def _match_field(curves, entries, into):
    """Per curve: the derivative poles (n, 3) whose B-spline field follows d(u), fitted by least squares
    with the corner poles fixed, and the largest angle in degrees between the loft's normal and the face's."""
    bss = [_bspline_make(c) for c in curves]
    corners = []                                   # corners[k]: where curve k - 1 ends and curve k starts
    for k in range(len(curves)):
        da, na = _match_dir(bss[k - 1], entries[k - 1], into, 1.0)
        db, nb = _match_dir(bss[k], entries[k], into, 0.0)
        w, s = np.cross(na, nb), da + db
        if np.linalg.norm(w) > 1e-6 and abs(float(np.dot(w, s))) > 1e-9:
            w = w / np.linalg.norm(w) * np.sign(float(np.dot(w, s)))      # in both tangent planes
        else:
            w = s / np.linalg.norm(s) if np.linalg.norm(s) > 1e-9 else db
        corners.append(w)
    fields, worst = [], []
    for k, (c, bs, entry) in enumerate(zip(curves, bss, entries)):
        flat = np.repeat(c["knots"], c["mults"])
        deg, n = c["deg"], len(c["poles"])
        D = np.zeros((n, 3))
        D[0], D[-1] = corners[k], corners[(k + 1) % len(curves)]
        if n > 2:
            us = _match_samples(c, 3 * (deg + 1))
            B = np.array([_bspline_basis(flat, deg, float(u))[0] for u in us])
            target = np.array([_match_dir(bs, entry, into, u)[0] for u in us])
            rhs = target - np.outer(B[:, 0], D[0]) - np.outer(B[:, -1], D[-1])
            D[1:-1] = np.linalg.lstsq(B[:, 1:-1], rhs, rcond=None)[0]
        ang = 0.0
        for u in _match_samples(c, 4 * (deg + 1) + 1):
            pt, v = gp_Pnt(), gp_Vec()
            bs.D1(float(u), pt, v)
            nl = np.cross(_np(v), _bspline_basis(flat, deg, float(u))[0] @ D)
            nf = _match_dir(bs, entry, into, u)[1]
            ln = np.linalg.norm(nl)
            cos = abs(float(np.dot(nl, nf))) / ln if ln > 1e-12 else 0.0
            ang = max(ang, math.degrees(math.acos(min(1.0, cos))))
        fields.append(D)
        worst.append(ang)
    return fields, worst


def _refine_column(col):
    """A knot mid-way in every span of one edge column, in every section's curve (exact)."""
    mids = [(a + b) / 2 for a, b in zip(col[0]["knots"], col[0]["knots"][1:])]
    for i, c in enumerate(col):
        b = _bspline_make(c)
        for u in mids:
            b.InsertKnot(float(u), 1, 1e-10, False)
        col[i] = _bspline_data(b)


def _match_ends(cols, conds):
    """The unit derivative poles per column at each Match tangent end (None at any other end), refining
    the knots of a column along the profile until its angle is under LOFT_MATCH_ANGLE. Mutates `cols`."""
    nsec, ncol = len(cols[0]), len(cols)
    ends = [(e, i, c) for e, (i, c) in enumerate(((0, conds[0]), (nsec - 1, conds[1])))
            if c is not None and c["cond"] == "match_tangent"]
    if not ends:
        return [None, None]
    entries = {e: _match_setup([col[i] for col in cols], c["faces"], c["into"]) for e, i, c in ends}
    for rnd in range(LOFT_MATCH_ROUNDS + 1):
        out, bad = [None, None], set()
        for e, i, c in ends:
            out[e], worst = _match_field([col[i] for col in cols], entries[e], c["into"])
            bad |= {k for k, w in enumerate(worst) if w > LOFT_MATCH_ANGLE}
        if not bad:
            return out
        if rnd == LOFT_MATCH_ROUNDS:
            raise KernelError(LOFT_MATCH_ERR)
        for k in bad:
            _refine_column(cols[k])
        for i in range(nsec):                     # neighbouring columns still share their corner pole
            for k in range(ncol):
                cols[k][i]["poles"][-1] = cols[(k + 1) % ncol][i]["poles"][0]


def _skin_solid(wires, conds, with_history, cap_faces=None):
    """A loft through `wires` by Hermite skinning, as a solid, with the history of every input edge
    (and, given `cap_faces`, of the first and last profile faces).

    `conds` is (start, end); each is None or {"cond", "mag", "n", "into"}: `n` the unit normal of the
    end profile's plane and `into` the unit normal pointing from that profile into the loft; Match
    tangent adds "faces", the adjacent faces."""
    from OCP.BRepFill import BRepFill_CompatibleWires
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.Geom import Geom_BSplineSurface
    from OCP.TopAbs import TopAbs_SHELL
    _P1, P2, R1, I1, Seq = _skin_arrays()
    seq = Seq()
    for w in wires:
        seq.Append(w)
    cw = BRepFill_CompatibleWires(seq)
    try:
        cw.Perform()
    except Exception as exc:
        raise KernelError("Loft failed — try profiles with similar shapes") from exc
    if not cw.IsDone():
        raise KernelError("Loft failed — try profiles with similar shapes")
    res = cw.Shape()
    secs = [_Section(_as_wire(res.Value(i))) for i in range(1, res.Length() + 1)]
    ncol = len(secs[0].curves)
    if any(len(s.curves) != ncol for s in secs):
        raise KernelError("Loft failed — try profiles with similar shapes")
    cols = [_same_knots([s.curves[k] for s in secs]) for k in range(ncol)]   # cols[k][i]: column k, section i
    for i in range(len(secs)):                     # neighbouring columns share their corner pole exactly
        for k in range(ncol):
            cols[k][i]["poles"][-1] = cols[(k + 1) % ncol][i]["poles"][0]
    match = _match_ends(cols, conds)               # Match tangent may refine the columns' knots first
    nsec = len(secs)
    grid =[np.vstack([cols[k][i]["poles"] for k in range(ncol)]) for i in range(nsec)]
    gaps = [float(np.mean(np.linalg.norm(grid[i] - grid[i - 1], axis=1))) for i in range(1, nsec)]
    if min(gaps) < 1e-9:
        raise KernelError("Loft failed — two profiles are in the same place")
    v = np.concatenate([[0.0], np.cumsum(gaps)])   # a loft parameter per section: mean pole distance

    # Each column of poles is a cubic through the sections. Its parameter runs in mm, so an end
    # derivative of m * n per mm is the card's m * L * n per unit of the end span (L = that span).
    vflat = np.concatenate([[v[0]] * 4, v[1:-1], [v[-1]] * 4])
    nv = nsec + 2
    A = np.zeros((nv, nv))
    for i in range(nsec):
        A[i] = _bspline_basis(vflat, 3, v[i])[0]
    for row, (t, c) in zip((nsec, nsec + 1), ((v[0], conds[0]), (v[-1], conds[1]))):
        A[row] = _bspline_basis(vflat, 3, t, 2)[1 if c else 2]     # no condition: zero second derivative
    lu = np.linalg.inv(A)

    def end_ders(i, c, sign):
        """The derivative along the loft at every pole of section i, per column."""
        if c is None:
            return [np.zeros((len(cols[k][i]["poles"]), 3)) for k in range(ncol)]
        if c["cond"] == "match_tangent":
            return [sign * c["mag"] * d for d in match[0 if i == 0 else 1]]
        if c["cond"] == "normal":
            return [np.tile(sign * c["mag"] * c["into"], (len(cols[k][i]["poles"]), 1)) for k in range(ncol)]
        out = _outward([cols[k][i] for k in range(ncol)], c["n"])
        return [sign * c["mag"] * np.array(d) for d in out]
    # A derivative leaves the start profile into the loft and arrives at the end profile out of it.
    d0, d1 = end_ders(0, conds[0], 1.0), end_ders(nsec - 1, conds[1], -1.0)

    vk, vm = R1(1, nsec), I1(1, nsec)
    for i, t in enumerate(v):
        vk.SetValue(i + 1, float(t))
        vm.SetValue(i + 1, 4 if i in (0, nsec - 1) else 1)
    sew = BRepBuilderAPI_Sewing(1e-6)
    side = []
    for k in range(ncol):
        c0 = cols[k][0]
        nu = len(c0["poles"])
        poles = P2(1, nu, 1, nv)
        for j in range(nu):
            rhs = np.vstack([cols[k][i]["poles"][j] for i in range(nsec)] + [d0[k][j], d1[k][j]])
            ctrl = lu @ rhs
            for q in range(nv):
                poles.SetValue(j + 1, q + 1, _pnt(ctrl[q]))
        uk, um = R1(1, len(c0["knots"])), I1(1, len(c0["knots"]))
        for i, (kv, mv) in enumerate(zip(c0["knots"], c0["mults"])):
            uk.SetValue(i + 1, float(kv))
            um.SetValue(i + 1, int(mv))
        surf = Geom_BSplineSurface(poles, uk, vk, um, vm, int(c0["deg"]), 3)
        f = BRepBuilderAPI_MakeFace(surf, 0.0, 1.0, float(v[0]), float(v[-1]), 1e-7).Face()
        side.append(f)
        sew.Add(f)
    caps = []
    for i in (0, nsec - 1):
        mw = BRepBuilderAPI_MakeWire()
        for k in range(ncol):
            mw.Add(BRepBuilderAPI_MakeEdge(_bspline_make(cols[k][i])).Edge())
        mf = BRepBuilderAPI_MakeFace(mw.Wire(), True) if mw.IsDone() else None
        if mf is None or not mf.IsDone():
            raise KernelError("End conditions need flat end profiles")
        caps.append(mf.Face())
        sew.Add(caps[-1])
    sew.Perform()
    ex = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    if not ex.More():
        raise KernelError("Loft failed — the end condition made a surface that does not close")
    body = BRepBuilderAPI_MakeSolid(_as_shell(ex.Current())).Solid()
    ex.Next()
    if ex.More() or not is_valid(body):
        raise KernelError("Loft failed — the end condition made a surface that does not close")
    if volume(body) < 0:
        body.Reverse()
    if not with_history:
        return body, None
    hist = BRepTools_History()
    side = [sew.Modified(f) for f in side]
    # Each edge of the first profile generated the side faces of its span. Only the first: that is
    # all ThruSections' GeneratedFace answers for in `loft`, and a second source would change the names.
    for w, sec in ((wires[0], secs[0]),):
        for e in _sub_shapes(w, TopAbs_EDGE):
            try:
                gens = list(cw.GeneratedShapes(e))
            except Exception:
                gens = []
            for g in gens or [e]:
                for k, ce in enumerate(sec.edges):
                    if ce.IsSame(g):
                        hist.AddGenerated(e, side[k])
    if cap_faces:
        for f, cap in ((cap_faces[0], caps[0]), (cap_faces[-1], caps[1])):
            hist.AddGenerated(f, sew.Modified(cap))
    return body, hist


def _skin_end(face, other, cond):
    """An end condition made ready for `_skin_solid`: the profile's plane normal and the normal that
    points from it towards its neighbouring profile."""
    if cond is None:
        return None
    if cond.get("cond") not in LOFT_CONDS:
        raise KernelError(f"Unknown loft end condition '{cond.get('cond')}'")
    mag = float(cond.get("mag", 1.0))
    if abs(mag) < 1e-9:
        raise KernelError("A loft end condition needs a magnitude other than zero")
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_Plane:
        raise KernelError("End conditions need flat end profiles")
    n = _np(ad.Plane().Axis().Direction())
    d = np.asarray(face_info(other)["c"], float) - np.asarray(face_info(face)["c"], float)
    into = n if float(np.dot(d, n)) >= 0 else -n
    out = {"cond": cond["cond"], "mag": mag, "n": n, "into": into}
    if cond["cond"] == "match_tangent":
        out["faces"] = list(cond.get("faces") or [])
        if not out["faces"]:
            raise KernelError("Match tangent needs the adjacent faces — pick the faces that touch the profile")
    return out


def loft_skin(profile_faces, start=None, end=None, with_history=False):
    """A loft whose ends leave the first profile and meet the last as `start` and `end` say: each is
    None or {"cond": "normal" | "tangent" | "match_tangent", "mag": <signed magnitude, default 1>}, and
    Match tangent also has "faces": the adjacent faces (TopoDS faces) the loft continues smoothly.

    Built by the route spike I7.1 chose (LOFT_SKIN, Hermite skinning): the wires are made compatible,
    each edge column becomes one B-spline face through every section with the end derivatives set
    (Normal to profile m*L*n, Tangent to profile m*L*t with t the outward in-plane normal, Match tangent
    m*L*d with d continuing the adjacent face across the profile curve), the two profile faces close it
    as caps, and the faces are sewn into a solid. One side face per edge of the first profile, with the
    same history as `loft`, so the faces get the same names on either route. Holes are matched as `loft`
    matches them and lofted with the outer loop's conditions, except that a Match tangent end leaves
    them with no condition there (with no condition at either end they are lofted as `loft` does)."""
    from OCP.BRepTools import BRepTools
    if len(profile_faces) < 2:
        raise KernelError("Select at least two profiles, in order")
    conds = (_skin_end(profile_faces[0], profile_faces[1], start),
             _skin_end(profile_faces[-1], profile_faces[-2], end))
    if conds[0] is None and conds[1] is None:
        raise KernelError("loft_skin needs an end condition; use loft")
    outers = [BRepTools.OuterWire_s(f) for f in profile_faces]
    body, hist = _skin_solid(outers, conds, with_history, profile_faces)
    hole_conds = tuple(None if c is not None and c["cond"] == "match_tangent" else c for c in conds)
    for wires in _loft_hole_sets(profile_faces):
        if hole_conds == (None, None):
            tool = _thru_sections(wires, False)
            core = tool.Shape()
            if volume(core) < 0:
                core = core.Reversed()
            core_hist = _thru_sections_history(tool, wires) if with_history else None
        else:
            core, core_hist = _skin_solid(wires, hole_conds, with_history)
        if with_history:
            body, cut_hist = cut(body, core, with_history=True)
            hist = _chain(hist, core_hist, cut_hist)
        else:
            body = cut(body, core)
        if len(solids(body)) != 1 or not is_valid(body):
            raise KernelError("Loft failed — a hole runs out through the side of the loft")
    return (body, hist) if with_history else body


# Loft guides (I7.3), on the route spike I7.1 measured on OCP 8.0 (gordon80=ok, one_guide=isocurve,
# corner_guide=ok, rational=direct). GeomFill_Gordon is bound only in the 8.0 build.
LOFT_GUIDE_TOL = 1e-5          # mm: how close a guide must come to each profile, and the surface to each curve
LOFT_GUIDE_ANGLE = 1e-3        # radians: a larger turn where two guide edges meet is a sharp corner
LOFT_ONE_GUIDE = "isocurve"    # one guide is joined by isocurves of the unguided loft (_network_side)


def _gordon_class():
    try:
        from OCP.GeomFill import GeomFill_Gordon
    except ImportError:
        raise KernelError("Loft guides need OpenCascade 8.0 or newer") from None
    return GeomFill_Gordon


def _curve_array(curves):
    try:
        from OCP.TColGeom import TColGeom_Array1OfCurve as A
    except ImportError:  # OCP 8.0
        from OCP.OCP.collections import Array1_Geom_Curve as A
    a = A(1, len(curves))
    for i, c in enumerate(curves):
        a.SetValue(i + 1, c)
    return a


def _curve_deviation(curve, surf, n=50):
    """The largest distance from `n` + 1 evenly spaced points of `curve` to `surf`."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    a, b = curve.FirstParameter(), curve.LastParameter()
    worst = 0.0
    for k in range(n + 1):
        pr = GeomAPI_ProjectPointOnSurf(curve.Value(a + (b - a) * k / n), surf)
        worst = max(worst, pr.LowerDistance() if pr.NbPoints() else math.inf)
    return worst


def _is_rational(c):
    from OCP.Geom import Geom_BSplineCurve, Geom_Conic
    return (isinstance(c, Geom_BSplineCurve) and c.IsRational()) or isinstance(c, Geom_Conic)


def _nonrational(c, tol, order):
    """A rational curve as a non-rational B-spline within `tol`, C0 / C1 / C2 for `order` 0 / 1 / 2;
    any other curve unchanged."""
    from OCP.GeomConvert import GeomConvert_ApproxCurve
    from OCP.GeomAbs import GeomAbs_C0, GeomAbs_C2
    if not _is_rational(c):
        return c
    ap = GeomConvert_ApproxCurve(c, tol, (GeomAbs_C0, GeomAbs_C1, GeomAbs_C2)[order], 400, 8)
    if not ap.HasResult():
        raise KernelError("Loft failed — a guide or profile curve could not be prepared")
    return ap.Curve()


def network_surface(profile_curves, guide_curves, tol=LOFT_GUIDE_TOL):
    """The one Gordon surface through a network of curves (open or closed), and its report
    {"status", "approximate", "guide_dev", "profile_dev"}; it fails when the surface misses a curve by
    more than `tol`. Every profile must cross every guide; both lists need two or more curves.

    GeomFill_Gordon.Report() cannot be called from Python on OCP 8.0.1 (its BuildReport type is not
    registered), so the deviations are measured by projecting points of each curve onto the surface."""
    gordon = _gordon_class()
    if len(profile_curves) < 2 or len(guide_curves) < 2:
        raise KernelError("A guide network needs at least two profiles and two guides")

    def build(profiles, guides):
        g = gordon()
        g.Init(_curve_array(profiles), _curve_array(guides), float(tol))
        g.SetApproximationMode(gordon.ApproximationMode_e.ExactOnly)
        try:
            g.Perform()
        except Exception:
            return g, False
        return g, bool(g.IsDone())
    g, done = build(profile_curves, guide_curves)
    rational = [c for c in list(profile_curves) + list(guide_curves) if _is_rational(c)]
    if not done and rational:
        # Spike I7.1 found rational profiles (circles) work as given (rational=direct), but rational
        # guides with polygon profiles fail CurveCompatibilityFailed: retry with the rational curves
        # made non-rational within tol / 10. Profiles keep their corners (C0), guides are smooth (C2).
        g, done = build([_nonrational(c, tol / 10, 0) for c in profile_curves],
                        [_nonrational(c, tol / 10, 2) for c in guide_curves])
    if not done:
        raise KernelError(f"Loft failed — the guides and profiles do not form a network ({g.Status().name})")
    surf = g.Surface()
    rep = {"status": g.Status().name, "approximate": bool(g.IsApproximate()),
           "guide_dev": max(_curve_deviation(c, surf) for c in guide_curves),
           "profile_dev": max(_curve_deviation(c, surf) for c in profile_curves)}
    if rep["guide_dev"] > tol:
        raise KernelError(f"Loft failed — the surface misses a guide by {rep['guide_dev']:.2g} mm")
    if rep["profile_dev"] > tol:
        raise KernelError(f"Loft failed — the surface misses a profile by {rep['profile_dev']:.2g} mm")
    return surf, rep


def _edge_bspline(edge):
    """An edge's curve as a B-spline running in the edge's direction (rational for arcs and circles)."""
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomConvert import GeomConvert
    ad = BRepAdaptor_Curve(edge)
    r = BRep_Tool.Curve_s(edge, 0.0, 1.0)
    c = r[0] if isinstance(r, tuple) else r
    bs = GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(c, ad.FirstParameter(), ad.LastParameter()))
    if edge.Orientation() == TopAbs_REVERSED:
        bs.Reverse()
    return bs


def _turn(prev, nxt):
    """The angle between the end tangent of B-spline `prev` and the start tangent of `nxt`."""
    pt, t0, t1 = gp_Pnt(), gp_Vec(), gp_Vec()
    prev.D1(prev.LastParameter(), pt, t0)
    nxt.D1(nxt.FirstParameter(), pt, t1)
    if t0.Magnitude() < 1e-12 or t1.Magnitude() < 1e-12:
        return math.pi
    return t0.Angle(t1)


def _join(curves, what):
    """B-spline curves end to end as one (GeomConvert_CompCurveToBSplineCurve), corners kept as C0 knots."""
    from OCP.GeomConvert import GeomConvert_CompCurveToBSplineCurve
    if not curves:
        raise KernelError(f"{what} is empty")
    comp = GeomConvert_CompCurveToBSplineCurve(curves[0])
    for c in curves[1:]:
        if not comp.Add(c, 1e-6, True):         # True: append; the default prepends where it can
            raise KernelError(f"{what} is not one connected chain")
    return comp.BSplineCurve()


def _wire_edges(wire):
    from OCP.BRepTools import BRepTools_WireExplorer
    out, ex = [], BRepTools_WireExplorer(wire)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def _guide_bspline(wire, n):
    """Guide `n` (1-based) as one B-spline; a sharp corner between two of its edges is refused."""
    curves = [_edge_bspline(e) for e in _wire_edges(wire)]
    for a, b in zip(curves, curves[1:]):
        if _turn(a, b) > LOFT_GUIDE_ANGLE:
            raise KernelError(f"Guide {n} has a sharp corner — a guide must be tangent-continuous")
    return _join(curves, f"Guide {n}")


def _slot_point(edges, s):
    """The point a fraction s - k along edge k = floor(s) of a wire's edges (in wire order)."""
    k = int(math.floor(s + 1e-12)) % len(edges)
    f = min(max(s - math.floor(s + 1e-12), 0.0), 1.0)
    e = edges[k]
    ad = BRepAdaptor_Curve(e)
    a, b = ad.FirstParameter(), ad.LastParameter()
    return ad.Value(b - f * (b - a) if e.Orientation() == TopAbs_REVERSED else a + f * (b - a))


def _slot_of(edges, p):
    """Where on a wire's edges the point p lies, as edge index + fraction along it, in [0, len(edges))."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    best = None
    for k, e in enumerate(edges):
        ad = BRepAdaptor_Curve(e)
        a, b = ad.FirstParameter(), ad.LastParameter()
        r = BRep_Tool.Curve_s(e, 0.0, 1.0)
        pr = GeomAPI_ProjectPointOnCurve(p, r[0] if isinstance(r, tuple) else r, a, b)
        if pr.NbPoints():
            f = (pr.LowerDistanceParameter() - a) / (b - a)
            if e.Orientation() == TopAbs_REVERSED:
                f = 1.0 - f
            if best is None or pr.LowerDistance() < best[0]:
                best = (pr.LowerDistance(), k + min(max(f, 0.0), 1.0))
    return best[1] % len(edges)


def _iso(edge_lists, s):
    """The unguided loft's isocurve at slot s: through the point at slot s of every profile."""
    from OCP.GeomAPI import GeomAPI_Interpolate
    try:
        from OCP.TColgp import TColgp_HArray1OfPnt as H
    except ImportError:  # OCP 8.0
        from OCP.OCP.collections import HArray1_gp_Pnt as H
    pts = H(1, len(edge_lists))
    for i, edges in enumerate(edge_lists):
        pts.SetValue(i + 1, _slot_point(edges, s))
    it = GeomAPI_Interpolate(pts, False, 1e-9)
    it.Perform()
    if not it.IsDone():
        raise KernelError("Loft failed — the profiles are too close together for guides")
    return it.Curve()


LOFT_GUIDE_GAP = 0.1           # edges: an isocurve this close to where a guide crosses a profile is left out
LOFT_NETWORK_MIN = 4           # curves round a smooth loop; fewer leave GeomFill_Gordon's guide direction slack


def _gap(a, b, n):
    d = abs(a - b) % n
    return min(d, n - d)


def _loop_slots(n, crossed):
    """For a profile loop with no sharp corner: the slots of the isocurves that join the guides (one
    at every edge joint no guide crosses, then the middle of the widest gap until LOFT_NETWORK_MIN
    curves go round), and the seam slot in the middle of the widest remaining gap."""
    slots = [float(k) for k in range(n)
             if all(_gap(k, s, n) > LOFT_GUIDE_GAP for row in crossed for s in row)]

    def widest():
        ring = sorted(slots + [row[0] for row in crossed])
        widths = [(ring[(i + 1) % len(ring)] - ring[i]) % n or n for i in range(len(ring))]
        i = int(np.argmax(widths))
        return (ring[i] + widths[i] / 2) % n
    while len(slots) + len(crossed) < LOFT_NETWORK_MIN:
        slots.append(widest())
    return slots, widest()


def _loop_curve(edges, seam):
    """A closed profile loop as one B-spline that starts and ends at slot `seam`."""
    from OCP.Geom import Geom_TrimmedCurve
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    from OCP.GeomConvert import GeomConvert
    n = len(edges)
    k = int(math.floor(seam)) % n
    bss = [_edge_bspline(e) for e in edges]
    b = bss[k]
    t = GeomAPI_ProjectPointOnCurve(_slot_point(edges, seam), b).LowerDistanceParameter()
    a0, a1 = b.FirstParameter(), b.LastParameter()
    head = [GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(b, t, a1))] if a1 - t > 1e-9 * (a1 - a0) else []
    tail = [GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(b, a0, t))] if t - a0 > 1e-9 * (a1 - a0) else []
    return _join(head + [bss[(k + i) % n] for i in range(1, n)] + tail, "A profile")


def _network_faces(surf, joints, mid, closed):
    """A Gordon surface cut into one face per profile edge: (faces, cap pieces at the first and the last
    profile, each a list per face of the (curve, a, b) edges along that face's end). `joints` are the first profile's edge ends in
    order (an open patch lists both of its ends) and `mid` a point inside its first edge."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    u0, u1, v0, v1 = surf.Bounds()

    def uv(p):
        return GeomAPI_ProjectPointOnSurf(p, surf).LowerDistanceParameters()
    uvs = [uv(p) for p in joints] + [uv(mid)]
    us, vs = np.array([q[0] for q in uvs]), np.array([q[1] for q in uvs])
    along_u = (np.ptp(us) / (u1 - u0)) > (np.ptp(vs) / (v1 - v0))   # the profile runs along u
    if along_u:
        ts, lo, hi, bound, s_lo, s_hi = list(us), u0, u1, float(np.mean(vs)), v0, v1
    else:
        ts, lo, hi, bound, s_lo, s_hi = list(vs), v0, v1, float(np.mean(us)), u0, u1
    span = hi - lo
    t_mid = ts.pop()
    ts = [lo if abs(t - lo) < 1e-7 * span else hi if abs(t - hi) < 1e-7 * span else t for t in ts]
    if closed:
        try:                # periodic, so a face may run across the seam
            if along_u and not surf.IsUPeriodic():
                surf.SetUPeriodic()
            elif not along_u and not surf.IsVPeriodic():
                surf.SetVPeriodic()
        except Exception as exc:
            raise KernelError("Loft failed — the guided surface does not close round the profile") from exc
        ts = [lo if t == hi else t for t in ts]
        if len(ts) == 1:
            cuts = [ts[0], ts[0] + span]
        else:               # the joints in wire order, unwrapped to run one way round from the first
            sgn = 1.0 if (t_mid - ts[0]) % span < (ts[1] - ts[0]) % span else -1.0
            cuts = [ts[0]]
            for t in ts[1:]:
                cuts.append(cuts[-1] + sgn * ((sgn * (t - cuts[-1])) % span))
            cuts.append(ts[0] + sgn * span)
    else:
        cuts = ts
        steps = np.diff(cuts)
        if not (np.all(steps > 0) or np.all(steps < 0)) or {cuts[0], cuts[-1]} != {lo, hi}:
            raise KernelError("Loft failed — the guides pull the profile edges out of order")
    first_at_lo = abs(bound - s_lo) <= abs(bound - s_hi)
    out, caps = [], ([], [])
    for k in range(len(cuts) - 1):
        a, b = sorted((cuts[k], cuts[k + 1]))
        if b - a < 1e-9 * span:
            raise KernelError("Loft failed — two profile corners meet on the guided surface")
        if along_u:
            out.append(BRepBuilderAPI_MakeFace(surf, a, b, s_lo, s_hi, 1e-7).Face())
            iso = (surf.VIso(s_lo), surf.VIso(s_hi))
        else:
            out.append(BRepBuilderAPI_MakeFace(surf, s_lo, s_hi, a, b, 1e-7).Face())
            iso = (surf.UIso(s_lo), surf.UIso(s_hi))
        first, last = (iso[0], iso[1]) if first_at_lo else (iso[1], iso[0])
        shift = math.floor((a - lo) / span + 1e-12) * span if closed else 0.0
        a, b = a - shift, b - shift               # cap edges stay inside the curve's own period
        pieces = [(a, b)] if b <= hi + 1e-12 * span else [(a, hi), (lo, b - span)]
        caps[0].append([(first, p, q) for p, q in pieces])
        caps[1].append([(last, p, q) for p, q in pieces])
    return out, caps


def _network_cap(pieces, profile_face):
    mw = BRepBuilderAPI_MakeWire()
    for c, a, b in (p for face in pieces for p in face):
        mw.Add(BRepBuilderAPI_MakeEdge(c, a, b).Edge())
    if not mw.IsDone():
        raise KernelError("Loft failed — the guided surface's end does not close")
    mf = BRepBuilderAPI_MakeFace(mw.Wire(), True)
    if not mf.IsDone():                 # a profile face that is not flat: cap on its own surface
        mf = BRepBuilderAPI_MakeFace(BRep_Tool.Surface_s(profile_face), mw.Wire(), True)
    if not mf.IsDone():
        raise KernelError("Loft failed — the guided surface's end does not close")
    return mf.Face()


def _network_side(edge_lists, wires, guides, guide_wires):
    """The guided side faces, one per edge of the compatible profiles (in the first profile's wire
    order), and the cap pieces at the first and the last profile.

    Profiles with sharp corners are cut at the corners into patches; each patch is one open network
    bounded by the unguided loft's isocurve at each corner (or the guide that crosses there) with the
    guides inside it. One closed network round a sharp-cornered loop is very sensitive to where its
    seam lies (measured on two bowed guides on squares: the far face 0.29 mm past its guide, or a face
    pinched 2 mm inside); open patches are not. A smooth loop (a circle) is one closed network, joined
    by isocurves (`_loop_slots`) and with its seam in the widest gap between them."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    n = len(edge_lists[0])
    crossed = []                       # per guide, the slot where it crosses each profile
    for gw in guide_wires:
        crossed.append([_slot_of(edges, BRepExtrema_DistShapeShape(gw, w).PointOnShape2(1))
                        for edges, w in zip(edge_lists, wires)])
    curves = [[_edge_bspline(e) for e in edges] for edges in edge_lists]
    corners = [k for k in range(n) if any(_turn(c[k - 1], c[k]) > LOFT_GUIDE_ANGLE for c in curves)]
    first = edge_lists[0]
    if not corners:
        slots, seam = _loop_slots(n, crossed)
        surf, _rep = network_surface([_loop_curve(edges, seam) for edges in edge_lists],
                                     guides + [_iso(edge_lists, s) for s in slots])
        return _network_faces(surf, [_slot_point(first, k) for k in range(n)], _slot_point(first, 0.5), True)

    def at_corner(g, c):
        near = [_gap(s, c, n) < LOFT_GUIDE_GAP for s in crossed[g]]
        if not any(near):
            return False
        for m, edges in enumerate(edge_lists):
            v = BRepBuilderAPI_MakeVertex(_slot_point(edges, c)).Vertex()
            if BRepExtrema_DistShapeShape(guide_wires[g], v).Value() > LOFT_GUIDE_TOL:
                raise KernelError(f"Guide {g + 1} passes close to a corner of profile {m + 1} — "
                                  "put it on the corner or further away")
        return True
    faces, caps = [None] * n, ([None] * n, [None] * n)
    for j, c in enumerate(corners):
        d = corners[(j + 1) % len(corners)]
        d = d + n if d <= c else d
        ends = []
        for s in (c, d):
            hit = [g for g in range(len(guides)) if at_corner(g, s % n)]
            ends.append(guides[hit[0]] if hit else _iso(edge_lists, float(s % n)))
        inner = []
        for g, row in enumerate(crossed):
            inside = [LOFT_GUIDE_GAP <= (s - c) % n <= (d - c) - LOFT_GUIDE_GAP for s in row]
            if any(inside) and not all(inside):
                raise KernelError(f"Guide {g + 1} runs across a corner of the profiles — a guide must stay "
                                  "on the same side of the loft from profile to profile")
            if all(inside):
                inner.append(((row[0] - c) % n, guides[g]))
        idx = [k % n for k in range(c, d)]
        surf, _rep = network_surface([_join([cs[k] for k in idx], "A profile") for cs in curves],
                                     [ends[0]] + [g for _s, g in sorted(inner, key=lambda t: t[0])] + [ends[1]])
        fs, cp = _network_faces(surf, [_slot_point(first, k) for k in range(c, d + 1)],
                                _slot_point(first, c + 0.5), False)
        for k, f, p0, p1 in zip(idx, fs, cp[0], cp[1]):
            faces[k], caps[0][k], caps[1][k] = f, p0, p1
    return faces, caps


def loft_network(profile_faces, guide_wires, with_history=False):
    """A solid loft whose outer loop passes through the guide wires (OCP 8.0 only).

    Each guide becomes one B-spline (refused with a sharp corner) and must touch every profile. The
    compatible profile wires and the guides make one network surface (`network_surface`) per run of
    edges between sharp corners, joined at the corners by the unguided loft's isocurves
    (`_network_side`); the surfaces are cut into one side face per first-profile edge (not at the
    guides), capped with the profiles, sewn and made solid, with the same history as `loft` and
    `loft_skin` so the faces get the same names on every route. One guide works the same way (spike
    I7.1: one_guide=isocurve). Holes are matched as `loft` matches them and lofted unguided."""
    from OCP.BRepTools import BRepTools
    from OCP.BRepFill import BRepFill_CompatibleWires
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    from OCP.TopAbs import TopAbs_SHELL
    _gordon_class()
    if len(profile_faces) < 2:
        raise KernelError("Select at least two profiles, in order")
    if not guide_wires:
        raise KernelError("loft_network needs a guide; use loft")
    outers = [BRepTools.OuterWire_s(f) for f in profile_faces]
    guides = [_guide_bspline(w, n + 1) for n, w in enumerate(guide_wires)]
    for n, gw in enumerate(guide_wires):
        for m, ow in enumerate(outers):
            d = BRepExtrema_DistShapeShape(gw, ow)
            if not d.IsDone() or d.Value() > LOFT_GUIDE_TOL:
                raise KernelError(f"Guide {n + 1} does not touch profile {m + 1}")
    _P1, _P2, _R1, _I1, Seq = _skin_arrays()
    seq = Seq()
    for w in outers:
        seq.Append(w)
    cw = BRepFill_CompatibleWires(seq)
    try:
        cw.Perform()
    except Exception as exc:
        raise KernelError("Loft failed — try profiles with similar shapes") from exc
    if not cw.IsDone():
        raise KernelError("Loft failed — try profiles with similar shapes")
    res = cw.Shape()
    wires = [_as_wire(res.Value(i)) for i in range(1, res.Length() + 1)]
    edge_lists = [_wire_edges(w) for w in wires]
    if any(len(r) != len(edge_lists[0]) for r in edge_lists):
        raise KernelError("Loft failed — try profiles with similar shapes")
    side, caps_at = _network_side(edge_lists, wires, guides, guide_wires)
    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in side:
        sew.Add(f)
    caps = [_network_cap(caps_at[0], profile_faces[0]), _network_cap(caps_at[1], profile_faces[-1])]
    for c in caps:
        sew.Add(c)
    sew.Perform()
    sx = TopExp_Explorer(sew.SewedShape(), TopAbs_SHELL)
    if not sx.More():
        raise KernelError("Loft failed — the guided surface does not close")
    body = BRepBuilderAPI_MakeSolid(_as_shell(sx.Current())).Solid()
    sx.Next()
    if sx.More() or not is_valid(body):
        raise KernelError("Loft failed — the guided surface does not close")
    if volume(body) < 0:
        body.Reverse()
    hist = None
    if with_history:
        hist = BRepTools_History()
        side = [sew.Modified(f) for f in side]
        # Each edge of the first profile generated the side face of its span, as in `_skin_solid`.
        for e in _sub_shapes(outers[0], TopAbs_EDGE):
            try:
                gens = list(cw.GeneratedShapes(e))
            except Exception:
                gens = []
            for g in gens or [e]:
                for k, ce in enumerate(edge_lists[0]):
                    if ce.IsSame(g):
                        hist.AddGenerated(e, side[k])
        for f, cap in ((profile_faces[0], caps[0]), (profile_faces[-1], caps[1])):
            hist.AddGenerated(f, sew.Modified(cap))
    for hole in _loft_hole_sets(profile_faces):      # holes are lofted unguided, as `loft` lofts them
        tool = _thru_sections(hole, False)
        core = tool.Shape()
        if volume(core) < 0:
            core = core.Reversed()
        if with_history:
            body, cut_hist = cut(body, core, with_history=True)
            hist = _chain(hist, _thru_sections_history(tool, hole), cut_hist)
        else:
            body = cut(body, core)
        if len(solids(body)) != 1 or not is_valid(body):
            raise KernelError("Loft failed — a hole runs out through the side of the loft")
    return (body, hist) if with_history else body


def loft_surface_network(profile_wires, guide_wires, with_history=False):
    """A surface loft through open profile curves and guides (I7.8, OCP 8.0 only): the one face
    `network_surface` builds from the curves, no caps and no solid. Each profile is one B-spline (its
    corners kept as C0 knots), each guide one B-spline (refused with a sharp corner) and every guide must
    touch every profile. The profiles' own order is kept: it is the order the Gordon network is built in.
    The first profile's edges all generated the one face, so each names it."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    _gordon_class()
    if len(profile_wires) < 2:
        raise KernelError("Select at least two profiles, in order")
    if len(guide_wires) < 2:
        raise KernelError("Add a second guide: a surface loft with guides needs at least two")
    profiles = []
    for m, w in enumerate(profile_wires):
        b = _join([_edge_bspline(e) for e in _wire_edges(w)], f"Profile {m + 1}")
        if b.StartPoint().Distance(b.EndPoint()) < 1e-6:
            raise KernelError("A surface loft with guides takes open profile curves, not closed ones, yet")
        profiles.append(b)
    guides = [_guide_bspline(w, n + 1) for n, w in enumerate(guide_wires)]
    for n, gw in enumerate(guide_wires):
        for m, pw in enumerate(profile_wires):
            d = BRepExtrema_DistShapeShape(gw, pw)
            if not d.IsDone() or d.Value() > LOFT_GUIDE_TOL:
                raise KernelError(f"Guide {n + 1} does not touch profile {m + 1}")
    surf, _rep = network_surface(profiles, guides)
    face = BRepBuilderAPI_MakeFace(surf, 1e-7).Face()
    if not is_valid(face):
        raise KernelError("Loft failed — the guided surface is not valid")
    if not with_history:
        return face
    hist = BRepTools_History()
    for e in _sub_shapes(profile_wires[0], TopAbs_EDGE):
        hist.AddGenerated(e, face)
    return face, hist


def draft(shape, face_list, pull_dir, angle_deg, neutral: Plane, with_history=False):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
    if not face_list:
        raise KernelError("Select faces to draft")
    mk = BRepOffsetAPI_DraftAngle(shape)
    pln = gp_Pln(_pnt(neutral.origin), _dir(neutral.normal))
    for f in face_list:
        mk.Add(f, _dir(pull_dir), math.radians(angle_deg), pln, True)
        if not mk.AddDone():
            raise KernelError("Can't draft one of the faces (drafting works on flat or cylindrical side faces)")
    try:
        mk.Build()
    except Exception as exc:
        raise KernelError("Draft failed — try a smaller angle") from exc
    if not mk.IsDone():
        raise KernelError("Draft failed — try a smaller angle")
    if with_history:
        return mk.Shape(), _maker_history(mk, [shape])
    return mk.Shape()


def section_cut(shape, p0, n):
    """The model with everything in front of the cutting plane removed (the side `n` points to), for a section
    view. Each body is cut on its own, so a multi-part model stays separate parts; a surface body keeps the
    pieces whose centroid is on the kept side."""
    p0, n = np.asarray(p0, float), _unit_np(n)
    lo, hi = bbox(shape)
    size = float(np.linalg.norm(hi - lo)) * 2 + 10
    pl = Plane(p0, _any_perp(n), n)
    tool = BRepBuilderAPI_MakeFace(gp_Pln(pl.gp_ax3()), -size, size, -size, size).Face()
    keep = []
    for sol in bodies(shape) or [shape]:
        sp = BRepAlgoAPI_Splitter()
        sp.SetArguments(_list([sol]))
        sp.SetTools(_list([tool]))
        sp.SetFuzzyValue(1e-7)
        sp.Build()
        pieces = []
        for b in (bodies(sp.Shape()) if sp.IsDone() else [sol]):
            pieces += _cut_apart(b, [tool]) if is_surface(b) else [b]
        for q in pieces:
            c = mass_props(q)["centroid"] if is_surface(q) else interior_point_solid(q)
            if float((c - p0) @ n) < 0:
                keep.append(q)
    return compound(keep)


def interior_point_solid(sol) -> np.ndarray:
    """A point inside a solid (its centre of mass if that is inside, else the middle of a face nudged in)."""
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    c = mass_props(sol)["centroid"]
    if BRepClass3d_SolidClassifier(sol, _pnt(c), 1e-7).State() == TopAbs_IN:
        return c
    lo, hi = bbox(sol)
    eps = float(np.linalg.norm(hi - lo)) * 1e-4 + 1e-6
    for f in faces(sol):
        m = tessellate(f)
        if not len(m.tris):
            continue
        tri = m.verts[m.tris].astype(float)
        cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        i = int(np.argmax(np.linalg.norm(cr, axis=1)))
        q, nrm = tri[i].mean(0), _unit_np(cr[i])
        for sgn in (-1, 1):
            t = q + sgn * eps * nrm
            if BRepClass3d_SolidClassifier(sol, _pnt(t), 1e-7).State() == TopAbs_IN:
                return t
    return c


def _unit_np(v):
    v = np.asarray(v, float)
    return v / (np.linalg.norm(v) + 1e-300)


def _any_perp(n):
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    x = np.cross(n, a)
    return x / np.linalg.norm(x)


def section_faces(cut_shape, p0, n, tol=1e-6):
    """The cut faces of a section (those lying in the cutting plane), grouped by solid: [[faces of solid 0], …]."""
    p0, n = np.asarray(p0, float), _unit_np(n)
    out = []
    for sol in solids(cut_shape):
        fs = []
        for f in faces(sol):
            pl = planar_face_plane(f)
            if pl is not None and abs(abs(float(pl.normal @ n)) - 1) < 1e-7 and \
                    abs(float((np.asarray(pl.origin) - p0) @ n)) < tol * 100 + 1e-7:
                fs.append(f)
        out.append(fs)
    return out


def hatch_segments(face, origin, along, across, spacing):
    """Hatch lines filling a flat face: straight lines running `along`, `spacing` apart in the `across`
    direction (both in the face's plane), clipped to the face (holes stay empty). [(P, Q)] in 3D."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    along, across = _unit_np(along), _unit_np(across)
    pts = np.array(_vertices_np(face) or [interior_point(face)])
    m = tessellate(face)
    if len(m.verts):
        pts = np.vstack([pts, m.verts.astype(float)])
    o = np.asarray(origin, float)
    a = (pts - o) @ along
    b = (pts - o) @ across
    if spacing <= 0 or (b.max() - b.min()) / spacing > 4000:
        return []
    k0, k1 = math.floor(b.min() / spacing), math.ceil(b.max() / spacing)
    lines = []
    for k in range(k0, k1 + 1):
        base = o + across * k * spacing
        P, Q = base + along * (a.min() - 1), base + along * (a.max() + 1)
        lines.append(BRepBuilderAPI_MakeEdge(_pnt(P), _pnt(Q)).Edge())
    if not lines:
        return []
    cm = BRepAlgoAPI_Common(compound(lines), face)
    cm.Build()
    if not cm.IsDone():
        return []
    out = []
    for e in edges(cm.Shape()):
        pl = discretize_edge(e)
        if len(pl) >= 2:
            out.append((np.asarray(pl[0], float), np.asarray(pl[-1], float)))
    return out


def split_by_plane(shape, plane: Plane, with_history=False):
    """The solids the plane cuts `shape` into. with_history=True returns (pieces, history, tool): the
    history of the splitter over the shape and the plane face it cut with, which is `tool`."""
    lo, hi = bbox(shape)
    size = float(np.linalg.norm(hi - lo)) * 2 + 10
    c = plane.origin + ((lo + hi) / 2 - plane.origin) - (((lo + hi) / 2 - plane.origin) @ plane.normal) * plane.normal
    u0, v0 = plane.to_local(c)
    tool = BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0 - size, u0 + size, v0 - size, v0 + size).Face()
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_list([shape]))
    sp.SetTools(_list([tool]))
    sp.SetFuzzyValue(1e-6)
    sp.Build()
    if not sp.IsDone():
        raise KernelError("Split failed")
    pieces = solids(sp.Shape())
    if len(pieces) < 2:
        raise KernelError("The plane doesn't cut through the part")
    return (pieces, _maker_history(sp, [shape, tool]), tool) if with_history else pieces


def plane_face(plane: Plane, around, size):
    """A square of a plane, 2·size across, centred where `around` projects onto it."""
    around = np.asarray(around, float)
    c = around - ((around - plane.origin) @ plane.normal) * plane.normal
    u0, v0 = plane.to_local(c)
    return BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0 - size, u0 + size, v0 - size, v0 + size).Face()


def extended_face(face, size):
    """The whole surface a face lies on, big enough (about `size`) to cut right through a part: a plane as a big
    square, a cylinder or cone all the way round and long, a sphere or torus complete, a surface swept from a
    curve (an extruded sketch curve) long. Other surfaces are used as they are."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import (GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus,
                             GeomAbs_SurfaceOfExtrusion)
    ad = BRepAdaptor_Surface(face)
    t = ad.GetType()
    surf = BRep_Tool.Surface_s(face)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    uc, vc = (u0 + u1) / 2, (v0 + v1) / 2
    if t == GeomAbs_Plane:
        u0, u1, v0, v1 = uc - size, uc + size, vc - size, vc + size
    elif t in (GeomAbs_Cylinder, GeomAbs_SurfaceOfExtrusion):
        if t == GeomAbs_Cylinder:
            u0, u1 = 0.0, 2 * math.pi
        v0, v1 = vc - size, vc + size
    elif t == GeomAbs_Cone:
        cone = ad.Cone()
        apex_v = -cone.RefRadius() / math.sin(cone.SemiAngle()) if abs(math.sin(cone.SemiAngle())) > 1e-9 else -1e9
        u0, u1 = 0.0, 2 * math.pi
        lo_, hi_ = vc - size, vc + size
        if apex_v < vc:
            lo_ = max(lo_, apex_v + 1e-6 * size)
        else:
            hi_ = min(hi_, apex_v - 1e-6 * size)
        v0, v1 = lo_, hi_
    elif t in (GeomAbs_Sphere, GeomAbs_Torus):
        u0, u1, v0, v1 = surf.Bounds()
    else:
        return face
    mf = BRepBuilderAPI_MakeFace(surf, u0, u1, v0, v1, 1e-7)
    if not mf.IsDone():
        return face
    out = mf.Face()
    if face.Orientation() == TopAbs_REVERSED:
        out.Reverse()
    return out


def _tool_normal_at(point, tool):
    """(distance, nearest point, unit normal there) from a point to a cutting surface, or None."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape, BRepExtrema_IsInFace
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepLProp import BRepLProp_SLProps
    p = np.asarray(point, float)
    dss = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(_pnt(p)).Vertex(), tool)
    if not dss.IsDone():
        return None
    for i in range(1, dss.NbSolution() + 1):
        if dss.SupportTypeShape2(i) != BRepExtrema_IsInFace:
            continue
        f = _as_face(dss.SupportOnShape2(i))
        u, v = dss.ParOnFaceS2(i)
        pr = BRepLProp_SLProps(BRepAdaptor_Surface(f), u, v, 1, 1e-7)
        if not pr.IsNormalDefined():
            continue
        n = _np(pr.Normal())
        if f.Orientation() == TopAbs_REVERSED:
            n = -n
        return float(dss.Value()), _np(dss.PointOnShape2(i)), n / np.linalg.norm(n)
    return None


def side_of(point, tool) -> float:
    """Which side of a cutting surface (a face or a shell) a point is on: + on the side its normal points to."""
    p = np.asarray(point, float)
    got = _tool_normal_at(p, tool)
    if got is not None:
        return float((p - got[1]) @ got[2])
    best, dmin = 0.0, None
    for f in faces(tool):           # nearest spot is on an edge of the surface: use the nearest face's surface
        try:
            q, n = surface_point_normal(f, p)
        except KernelError:
            continue
        d = float(np.linalg.norm(p - q))
        if dmin is None or d < dmin:
            dmin, best = d, float((p - q) @ n)
    return best


def piece_side(piece, tool) -> float:
    """Which side of a cutting surface a piece cut off by it lies on (+ = the side the surface's normal points
    to). A piece the surface cut has a face lying on it, facing away from the piece: that settles it exactly.
    Otherwise a point inside the piece is tested; for a shell piece (a surface body) that is its area centroid."""
    if is_surface(piece):
        return side_of(mass_props(piece)["centroid"], tool)
    lo, hi = bbox(piece)
    tol = float(np.linalg.norm(hi - lo)) * 1e-6 + 1e-7
    tool_faces = faces(tool)
    # a tool of several faces (a surface body): "front" is the first face's side wherever the piece has a face
    # on it; otherwise the face the piece lies against decides (a V-shaped tool has opposing normals)
    for ts in ([tool_faces[0], tool] if len(tool_faces) > 1 else [tool]):
        for f in faces(piece):
            try:
                q, n = surface_point_normal(f)
            except KernelError:
                continue
            got = _tool_normal_at(q, ts)
            if got is None or got[0] > tol:
                continue
            dot = float(n @ got[2])
            if abs(dot) > 0.5:
                return -dot          # the piece's outside faces against the tool's normal: it's on the + side
    return side_of(interior_point_solid(piece), tool)


def _cut_apart(body, tools):
    """The pieces of a surface body that a split has left in one shell: faces stay together across an edge
    unless that edge lies on one of the cutting surfaces `tools`. A body no edge of which lies on a tool is
    returned as it is."""
    fs = faces(body)
    if len(fs) < 2:
        return [body]
    tol = float(np.linalg.norm(bbox(body)[1] - bbox(body)[0])) * 1e-6 + 1e-7
    parent = list(range(len(fs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for e in edges(body):
        pair = edge_faces(body, e)
        if len(pair) != 2 or any(_dist(e, t) <= tol for t in tools):
            continue
        a, b = (next(i for i, f in enumerate(fs) if f.IsSame(x)) for x in pair)
        parent[find(a)] = find(b)
    groups = {}
    for i, f in enumerate(fs):
        groups.setdefault(find(i), []).append(f)
    if len(groups) == 1:
        return [body]
    out = []
    for g in groups.values():
        b, sh = BRep_Builder(), TopoDS_Shell()
        b.MakeShell(sh)
        for f in g:
            b.Add(sh, f)
        out.append(sh)
    return out


def _dist(a, b) -> float:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    d = BRepExtrema_DistShapeShape(a, b)
    return float(d.Value()) if d.IsDone() else float("inf")


def split_with(shape, tools, with_history=False):
    """The bodies a shape is cut into by any number of cutting surfaces (faces or shells): solids, and the
    shells a surface body is cut into.
    with_history=True returns (pieces, history): the splitter's history over the shape and the tools, in that
    order, which is what naming needs (as split_by_plane gives for one plane)."""
    tools = list(tools)
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_list([shape]))
    sp.SetTools(_list(tools))
    sp.SetFuzzyValue(1e-6)
    sp.Build()
    if not sp.IsDone():
        raise KernelError("Split failed")
    pieces = []
    for b in bodies(sp.Shape()):
        pieces += _cut_apart(b, tools) if is_surface(b) else [b]
    return (pieces, _maker_history(sp, [shape] + tools)) if with_history else pieces


def transform(shape, translate_v=(0, 0, 0), axis_p=(0, 0, 0), axis_d=(0, 0, 1), angle_deg=0.0, with_history=False,
              scale=1.0, scale_center=(0, 0, 0)):
    """Scale about `scale_center` first, then rotate, then translate. A scale of 1 is the plain move."""
    scale = float(scale)
    if not scale > 0:
        raise KernelError("The scale must be more than 0")
    t = gp_Trsf()
    if abs(angle_deg) > 1e-12:
        t.SetRotation(gp_Ax1(_pnt(axis_p), _dir(axis_d)), math.radians(angle_deg))
    if scale != 1.0:
        sc = gp_Trsf()
        sc.SetScale(_pnt(scale_center), scale)
        t = t.Multiplied(sc)
    m = gp_Trsf()
    m.SetTranslation(_vec(translate_v))
    t = m.Multiplied(t)
    mk = BRepBuilderAPI_Transform(shape, t, True)
    return (mk.Shape(), _maker_history(mk, [shape])) if with_history else mk.Shape()


def cylinder(base, direction, radius, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    ax = gp_Ax2(_pnt(base), _dir(direction))
    return BRepPrimAPI_MakeCylinder(ax, float(radius), float(height)).Shape()


def cone(base, direction, r1, r2, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    ax = gp_Ax2(_pnt(base), _dir(direction))
    return BRepPrimAPI_MakeCone(ax, float(r1), float(r2), float(height)).Shape()


# --------------------------------------------------------------------------- modelled threads

# ISO 261 coarse pitches
ISO_PITCH = {"M1.6": 0.35, "M2": 0.4, "M2.5": 0.45, "M3": 0.5, "M4": 0.7, "M5": 0.8, "M6": 1.0, "M8": 1.25,
             "M10": 1.5, "M12": 1.75, "M14": 2.0, "M16": 2.0, "M18": 2.5, "M20": 2.5, "M22": 2.5,
             "M24": 3.0}
THREAD_H = 0.5413   # thread depth (5H/8) as a fraction of the pitch, ISO 68-1


def _frame(origin, axis):
    """Transform from the local Z-up thread frame to world (origin, axis)."""
    t = gp_Trsf()
    t.SetTransformation(gp_Ax3(_pnt(origin), _dir(axis)), gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))
    return t


def _uv_segment(surf, a, b):
    """An edge on `surf` that is a straight line in its (angle, height) parameters — on a
    cylinder that's an exact helix (or a straight vertical line)."""
    from OCP.Geom2d import Geom2d_Line
    from OCP.gp import gp_Pnt2d, gp_Dir2d
    from OCP.BRepLib import BRepLib
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    e = BRepBuilderAPI_MakeEdge(Geom2d_Line(gp_Pnt2d(*a), gp_Dir2d(dx, dy)), surf, 0.0, L).Edge()
    # long helices need enough approximation segments (about 8 per turn) to stay accurate
    turns = abs(dx) / (2 * math.pi)
    BRepLib.BuildCurves3d_s(e, 1e-7, GeomAbs_C1, 14, max(30, int(math.ceil(turns * 8))))
    return e


def _tooth(r_in, r_out, w_in, w_out, pitch, z0, turns, left=False):
    """One continuous helical thread tooth about +Z, built face by face: its base and crest are exact
    cylinder faces and its flanks are ruled between exact helices. Base width w_in at r_in, crest
    width w_out at r_out.

    Every face covers at most one turn (the tooth is split at angle 0 on each turn). A single face
    that wraps the cylinder many times is legal in OpenCascade, but its boolean operations then
    produce subtly wrong results on some lengths; one turn per face keeps them exact."""
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.BRepFill import BRepFill
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    sgn = -1.0 if left else 1.0
    S_in = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), r_in)
    S_out = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), r_out)
    n = max(1, int(round(turns)))
    faces_ = []

    def band(S, w, k):
        u0, u1 = sgn * 2 * math.pi * k, sgn * 2 * math.pi * (k + 1)
        h0, h1 = z0 + k * pitch, z0 + (k + 1) * pitch
        pts = [(u0, h0 - w / 2), (u1, h1 - w / 2), (u1, h1 + w / 2), (u0, h0 + w / 2)]
        es = [_uv_segment(S, pts[i], pts[(i + 1) % 4]) for i in range(4)]
        mw = BRepBuilderAPI_MakeWire()
        for e in es:
            mw.Add(e)
        return BRepBuilderAPI_MakeFace(S, mw.Wire(), True).Face(), es

    for k in range(n):
        f_in, e_in = band(S_in, w_in, k)
        f_out, e_out = band(S_out, w_out, k)
        faces_ += [f_in, f_out, BRepFill.Face_s(e_in[0], e_out[0]), BRepFill.Face_s(e_in[2], e_out[2])]
        if k == 0:
            faces_.append(BRepFill.Face_s(e_in[3], e_out[3]))     # start wall
        if k == n - 1:
            faces_.append(BRepFill.Face_s(e_in[1], e_out[1]))     # end wall
    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in faces_:
        sew.Add(f)
    sew.Perform()
    shell = _caster("Shell")(sew.SewedShape())
    sol = BRepBuilderAPI_MakeSolid(shell).Solid()
    if volume(sol) < 0:
        sol.Reverse()
    return sol


def _local_cylinder(r, z0, z1):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), float(r), float(z1 - z0)).Shape()


def _threaded_rod(rmin, rmaj, pitch, a, b, left=False):
    """An ISO threaded rod about +Z from z=a to z=b (core at rmin, crests at rmaj).
    The tooth's base sits a little inside the core (flanks extended at the ISO 30° angle) — with the
    exact-helix tooth that joins reliably and about 3× faster than a base lying exactly on the core."""
    P = float(pitch)
    if rmaj - rmin < 1e-3:
        raise KernelError("Thread is too shallow")
    if b - a <= 1e-6:
        raise KernelError("Thread length must be positive")
    eps = 0.05 * P
    w_in = 0.75 * P + 2 * eps * math.tan(math.radians(30))
    start = a - 2 * P
    turns = int(math.ceil((b + 2 * P - start) / P))
    tooth = _tooth(rmin - eps, rmaj, w_in, 0.125 * P, P, start, turns, left)
    # the core must reach well past both ends of the tooth (its end turns are about a pitch wide):
    # a tooth end poking out of the core's flat end makes the join subtly broken
    rod = fuse(_local_cylinder(rmin, start - 1.5 * P, start + turns * P + 1.5 * P), tooth, check=True, single=True)
    return common(rod, _local_cylinder(rmaj + 0.1 * P, a, b), check=True)


def threaded_plugs(origin, axis, major, pitch, z0, z1, core_r=None, extend=(True, True), left=False, clearance=0.0):
    """Solids that, cut from a part, leave an ISO internal (tapped) thread along `axis` between
    distances z0 and z1 from `origin`. `extend` says whether each end opens into air (the thread then
    runs out cleanly past it). core_r: radius of an existing hole the thread is cut into.
    clearance: extra radial room (mm), e.g. for 3D-printed threads."""
    P = float(pitch)
    rmaj = major / 2 + clearance
    rmin = max(rmaj - THREAD_H * P, core_r or 0.0)
    a = z0 - (2 * P if extend[0] else 0.0)
    b = z1 + (2 * P if extend[1] else 0.0)
    return [BRepBuilderAPI_Transform(_threaded_rod(rmin, rmaj, P, a, b, left), _frame(origin, axis), True).Shape()]


def cut_internal_thread(shape, origin, axis, major, pitch, z0, z1, with_history=False, **kw):
    """Tap a thread into `shape`: cut the threaded plug sections out of it, one at a time.
    with_history=True returns (shape, history, plugs): the cuts' histories composed in order, and the
    plugs that were cut (the tools whose faces become the thread's flanks)."""
    n0 = len(faces(shape))
    plugs = threaded_plugs(origin, axis, major, pitch, z0, z1, **kw)
    steps = []
    for plug in plugs:
        if with_history:
            shape, h = cut(shape, plug, check=True, with_history=True)
            steps.append(h)
        else:
            shape = cut(shape, plug, check=True)
    if len(faces(shape)) <= n0:
        raise KernelError("The thread didn't cut into any material here")
    return (shape, _chain(*steps), plugs) if with_history else shape


def external_thread(shape, origin, axis, shaft_r, pitch, z0, z1, left=False, clearance=0.0, with_history=False):
    """Thread a round shaft: the plain section between z0 and z1 is replaced by an ISO threaded rod
    whose crests sit on the shaft's surface (less any clearance).
    with_history=True returns (shape, history, tools): the cut and the fuse composed in order, and the
    sleeve and rod they used."""
    P = float(pitch)
    rmaj = shaft_r - clearance
    T = _frame(origin, axis)
    sleeve = BRepBuilderAPI_Transform(_local_cylinder(shaft_r + 0.1 * P, z0, z1), T, True).Shape()
    n0 = len(faces(shape))
    rod = BRepBuilderAPI_Transform(_threaded_rod(rmaj - THREAD_H * P, rmaj, P, z0, z1, left), T, True).Shape()
    lo, hi = bbox(shape)
    slo, shi = bbox(sleeve)
    if np.all(lo >= slo - 1e-6) and np.all(hi <= shi + 1e-6):
        # the thread runs the whole length of a plain rod: nothing is left of the original, it *is* the rod
        return (rod, BRepTools_History(), [rod]) if with_history else rod
    if not with_history:
        shape = cut(shape, sleeve, check=True)
        out = fuse(shape, rod, check=True, single=True)
    else:
        shape, h_cut = cut(shape, sleeve, check=True, with_history=True)
        out, h_fuse = fuse(shape, rod, check=True, single=True, with_history=True)
    if len(faces(out)) <= n0:
        raise KernelError("Couldn't thread this shaft")
    return (out, _chain(h_cut, h_fuse), [rod, sleeve]) if with_history else out


def material_at(shape, point) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    c = BRepClass3d_SolidClassifier(shape, _pnt(point), 1e-7)
    return c.State() == TopAbs_IN


def cylinder_face_frame(face):
    """For a cylindrical face: (axis point, unit axis, radius, z-min, z-max along the axis, is_hole)."""
    from OCP.BRepLProp import BRepLProp_SLProps
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_Cylinder:
        raise KernelError("Pick a round (cylindrical) face")
    cy = ad.Cylinder()
    p0 = _np(cy.Axis().Location())
    d = _np(cy.Axis().Direction())
    r = cy.Radius()
    zs = [float((v - p0) @ d) for v in _vertices_np(face)]
    poly = [np.asarray(q, float) for e in edges(face) for q in discretize_edge(e, r * 0.01)]
    zs += [float((q - p0) @ d) for q in poly]
    u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
    v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
    props = BRepLProp_SLProps(ad, u, v, 1, 1e-7)
    n = _np(props.Normal())
    if face.Orientation() == TopAbs_REVERSED:
        n = -n
    pt = _np(props.Value())
    radial = pt - p0 - ((pt - p0) @ d) * d
    is_hole = float(n @ radial) < 0          # normal points at the axis: material is outside → a hole
    return p0, d, r, min(zs), max(zs), is_hole


# --------------------------------------------------------------------------- section view

def section_caps(shape, plane: Plane):
    """Faces where `plane` cuts `shape` (the solid caps shown in a section view).
    Material on the +normal side is removed."""
    lo, hi = bbox(shape)
    c = (lo + hi) / 2
    size = float(np.linalg.norm(hi - lo)) + 10
    d0 = float((c - plane.origin) @ plane.normal)
    if d0 - size > 0 or d0 + size < 0:
        return []                                  # plane misses the part entirely
    u0, v0 = plane.to_local(c)
    big = BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0 - size, u0 + size, v0 - size, v0 + size).Face()
    try:
        common_ = BRepAlgoAPI_Common(shape, big)
        common_.Build()
        if not common_.IsDone():
            return []
        return [f for f in faces(common_.Shape())]
    except Exception:
        return []


# --------------------------------------------------------------------------- assemblies: placement, storage, export

def trsf_from_matrix(M) -> gp_Trsf:
    """A 4x4 rigid transform (numpy) as an OpenCascade gp_Trsf."""
    M = np.asarray(M, float)
    t = gp_Trsf()
    t.SetValues(*(float(M[r, c]) for r in range(3) for c in range(4)))
    return t


def located(shape, M):
    """The shape placed by a 4x4 rigid transform. Shares geometry with the original (cheap)."""
    M = np.asarray(M, float)
    if np.allclose(M, np.eye(4), atol=1e-12):
        return shape
    return shape.Moved(TopLoc_Location(trsf_from_matrix(M)))


def shape_to_brep(shape) -> bytes:
    """Serialise a shape (OpenCascade's native BREP text), zlib-compressed."""
    import os, tempfile, zlib
    from OCP.BRepTools import BRepTools
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        BRepTools.Write_s(shape, path)
        with open(path, "rb") as fh:
            return zlib.compress(fh.read(), 6)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def shape_from_brep(data: bytes):
    import os, tempfile, zlib
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    fd, path = tempfile.mkstemp(suffix=".brep")
    with os.fdopen(fd, "wb") as fh:
        fh.write(zlib.decompress(data))
    try:
        shp = TopoDS_Shape()
        BRepTools.Read_s(shp, path, BRep_Builder())
        if shp.IsNull():
            raise KernelError("Stored part copy is unreadable")
        return shp
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def export_step_named(items, path, assembly_name="Assembly"):
    """STEP export as a real assembly: one product per distinct part, placed once per instance, keeping
    names and colours. items: [(local shape, 4x4 transform, instance name, (r, g, b) or None)].
    Returns "named", or "flat" when the named writer failed and a plain file was written instead."""
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Writer
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorGen
        from OCP.TDataStd import TDataStd_Name
        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        top = st.NewShape()
        TDataStd_Name.Set_s(top, TCollection_ExtendedString(str(assembly_name)))
        protos = []                       # [(shape, label)] one product per distinct part
        for shape, M, name, color in items:
            lab = next((l for s, l in protos if s.IsSame(shape)), None)
            if lab is None:
                lab = st.AddShape(shape, False)
                base = str(name).rsplit(" <", 1)[0] if name else "Part"
                TDataStd_Name.Set_s(lab, TCollection_ExtendedString(base))
                if color is not None:
                    ct.SetColor(lab, Quantity_Color(float(color[0]), float(color[1]), float(color[2]),
                                                    Quantity_TOC_RGB), XCAFDoc_ColorGen)
                protos.append((shape, lab))
            comp = st.AddComponent(top, lab, TopLoc_Location(trsf_from_matrix(M)))
            if name:
                TDataStd_Name.Set_s(comp, TCollection_ExtendedString(str(name)))
        st.UpdateAssemblies()
        w = STEPCAFControl_Writer()
        w.SetNameMode(True)
        w.SetColorMode(True)
        if not w.Transfer(doc, STEPControl_AsIs):
            raise KernelError("transfer failed")
        if w.Write(str(path)) != IFSelect_RetDone:
            raise KernelError("write failed")
        return "named"
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("named STEP export failed (%s); wrote a flat file", e)
        export_step([located(s, M) for s, M, _n, _c in items], path)
        return "flat"


def save_step_result(result, prefix):
    """Write an import result as prefix.brep (all solids) + prefix.json (names, colours) — used to pass a STEP
    file read in a background process back to the app quickly."""
    import json as _json
    comp = compound([sol for sol, *_ in result])
    from OCP.BRepTools import BRepTools
    BRepTools.Write_s(comp, prefix + ".brep")
    meta = [{"name": nm, "color": list(c) if c else None, "faces": {str(k): list(v) for k, v in (fc or {}).items()}}
            for _s, nm, c, fc in result]
    with open(prefix + ".json", "w") as fh:
        _json.dump(meta, fh)


def load_step_result(prefix):
    import json as _json
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape, TopoDS_Iterator
    shp = TopoDS_Shape()
    BRepTools.Read_s(shp, prefix + ".brep", BRep_Builder())
    with open(prefix + ".json") as fh:
        meta = _json.load(fh)
    sols = []
    it = TopoDS_Iterator(shp)
    while it.More():
        sols.append(it.Value())
        it.Next()
    if len(sols) != len(meta):
        raise KernelError("Background STEP read returned mismatched data")
    return [(sol, m["name"], tuple(m["color"]) if m["color"] else None,
             {int(k): tuple(v) for k, v in m["faces"].items()}) for sol, m in zip(sols, meta)]


def face_edge_indices(shape, face_indices):
    """Indices (in edges(shape) order) of the edges bounding the given faces."""
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    fs = faces(shape)
    out = set()
    for fi in face_indices:
        if 0 <= fi < len(fs):
            for e in edges(fs[fi]):
                k = m.FindIndex(e)
                if k > 0:
                    out.add(k - 1)
    return out


# --------------------------------------------------------------------------- 2D drawing views (hidden-line removal)

_HLR_CACHE: dict = {}


def _hlr_curves(compound_shape, defl):
    """2D curves of an HLR result compound (it lies in the projection plane, z = 0)."""
    out = []
    if compound_shape is None or compound_shape.IsNull():
        return out
    for e in edges(compound_shape):
        try:
            ad = BRepAdaptor_Curve(e)
            ct = ad.GetType()
            a, b = ad.FirstParameter(), ad.LastParameter()
            if ct == GeomAbs_Line:
                p0, p1 = _np(ad.Value(a)), _np(ad.Value(b))
                if np.linalg.norm(p1[:2] - p0[:2]) > 1e-9:
                    out.append(("line", (float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))))
                continue
            if ct == GeomAbs_Circle:
                ci = ad.Circle()
                c = _np(ci.Location())
                r = ci.Radius()
                p0, p1 = _np(ad.Value(a)), _np(ad.Value(b))
                a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
                sweep = b - a
                if _np(ci.Axis().Direction())[2] < 0:     # runs clockwise on the paper: flip to counter-clockwise
                    a0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
                out.append(("circle", (float(c[0]), float(c[1])), float(r), float(a0), float(a0 + sweep)))
                continue
            pts = discretize_edge(e, defl)
            if len(pts) == 2:
                p0, p1 = pts[0], pts[1]
                if abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]) > 1e-12:
                    out.append(("line", (float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))))
                continue
            if len(pts) >= 2:
                xy = np.asarray(pts, float)[:, :2]
                # a curve seen edge-on (e.g. a hole's rim from the side) is just a straight line
                ctr = xy.mean(axis=0)
                _u, _s, vt = np.linalg.svd(xy - ctr)
                dd = vt[0]
                if float(np.max(np.abs((xy - ctr) @ np.array([-dd[1], dd[0]])))) < max(defl, 1e-7):
                    t = (xy - ctr) @ dd
                    a2, b2 = ctr + dd * t.min(), ctr + dd * t.max()
                    if np.linalg.norm(b2 - a2) > 1e-9:
                        out.append(("line", (float(a2[0]), float(a2[1])), (float(b2[0]), float(b2[1]))))
                    continue
                out.append(("poly", [(float(q[0]), float(q[1])) for q in xy]))
        except Exception:
            continue
    return out


def _line_param(p, q):
    """(direction angle, offset from the origin, t0, t1) of the infinite line through p, q, with t measured along
    a fixed direction of that line — so collinear segments can be compared as intervals."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy)
    if n < 1e-15:
        return None
    dx, dy = dx / n, dy / n
    if dx < 0 or (dx == 0 and dy < 0):
        dx, dy = -dx, -dy
    t0, t1 = p[0] * dx + p[1] * dy, q[0] * dx + q[1] * dy
    return math.atan2(dy, dx), p[0] * dy - p[1] * dx, min(t0, t1), max(t0, t1)


def _drop_covered(hidden, visible, tol):
    """Hidden lines lying exactly under visible ones (e.g. a back edge right behind a front edge) are left out.
    Visible lines are grouped by the infinite line they're on and sorted along it, so finding whether a hidden
    line is covered is a binary search — fast even with tens of thousands of edges (big STEP models)."""
    import bisect
    ang_q, off_q = 1e-6, max(tol * 10, 1e-9)
    groups = {}
    for c in visible:
        if c[0] != "line":
            continue
        lp = _line_param(c[1], c[2])
        if lp is not None:
            k = (int(round(lp[0] / ang_q)), int(round(lp[1] / off_q)))
            groups.setdefault(k, []).append((lp[2], lp[3]))
    index = {}
    for k, iv in groups.items():
        iv.sort()
        starts = [a for a, _b in iv]
        best, run = [], -1e300
        for _a, b in iv:
            run = max(run, b)
            best.append(run)                 # furthest reach of any interval starting at or before this one
        index[k] = (starts, best)
    circ = {}
    for c in visible:
        if c[0] == "circle":
            circ.setdefault((round(c[1][0] / off_q), round(c[1][1] / off_q)), []).append(c[2])
    out = []
    for c in hidden:
        if c[0] == "line":
            lp = _line_param(c[1], c[2])
            if lp is not None:
                k0, k1 = int(round(lp[0] / ang_q)), int(round(lp[1] / off_q))
                covered = False
                for da in (-1, 0, 1):
                    for do in (-1, 0, 1):
                        ix = index.get((k0 + da, k1 + do))
                        if ix is None:
                            continue
                        j = bisect.bisect_right(ix[0], lp[2] + tol) - 1
                        if j >= 0 and ix[1][j] >= lp[3] - tol:
                            covered = True
                            break
                    if covered:
                        break
                if covered:
                    continue
        elif c[0] == "circle":
            kx, ky = round(c[1][0] / off_q), round(c[1][1] / off_q)
            if any(abs(r - c[2]) < tol for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for r in circ.get((kx + dx, ky + dy), ())):
                continue
        out.append(c)
    return out


def view_axes(d, up):
    """Right / up / towards-the-viewer unit vectors of a drawing view."""
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    up = np.asarray(up, float)
    up = up - d * (up @ d)
    if np.linalg.norm(up) < 1e-9:
        up = np.array([0.0, 0, 1]) if abs(d[2]) < 0.9 else np.array([0.0, 1, 0])
        up = up - d * (up @ d)
    up = up / np.linalg.norm(up)
    right = np.cross(up, d)
    return right, up, d


def _hlr_key(shape, d, up, fast=False):
    _r, upv, dv = view_axes(d, up)
    return (id(shape), tuple(np.round(dv, 9)), tuple(np.round(upv, 9)), bool(fast))


def hlr_cached(shape, d, up):
    """The exact view if it has been worked out already, else None."""
    hit = _HLR_CACHE.get(_hlr_key(shape, d, up))
    return hit[1] if hit is not None and hit[0] is shape else None


def hlr_store(shape, d, up, res, fast=False):
    if len(_HLR_CACHE) > 96:
        _HLR_CACHE.clear()
    _HLR_CACHE[_hlr_key(shape, d, up, fast)] = (shape, res)


def hlr_project(shape, d, up, defl=None, fast=False):
    """Hidden-line views for 2D drawings, like Onshape's. d = direction from the part towards the viewer.
    Returns {"visible", "hidden", "tangent"}: lists of ("line", p0, p1) / ("circle", centre, r, a0, a1)
    / ("poly", points) in the view's 2D coordinates (model units; x = right, y = up, origin = world origin).
    fast=True works from the part's mesh: near-instant, but round edges come out as short straight pieces
    (used for a first look while the exact view is worked out)."""
    from OCP.HLRAlgo import HLRAlgo_Projector
    right, upv, dv = view_axes(d, up)
    key = _hlr_key(shape, d, up, fast)
    hit = _HLR_CACHE.get(key)
    if hit is not None and hit[0] is shape:
        return hit[1]
    if fast:
        ex = hlr_cached(shape, d, up)
        if ex is not None:
            return ex
    lo, hi = bbox(shape)
    size = float(np.linalg.norm(hi - lo)) or 1.0
    defl = defl or max(size * 2e-4, 1e-3)
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*dv), gp_Dir(*right))
    if fast:
        from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        BRepMesh_IncrementalMesh(shape, size * 1e-3, False, 0.15, True)
        algo = HLRBRep_PolyAlgo()
        algo.Load(shape)
        algo.Projector(HLRAlgo_Projector(ax))
        algo.Update()
        h = HLRBRep_PolyHLRToShape()
        h.Update(algo)
    else:
        from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
        algo = HLRBRep_Algo()
        algo.Add(shape)
        algo.Projector(HLRAlgo_Projector(ax))
        algo.Update()
        algo.Hide()
        h = HLRBRep_HLRToShape(algo)
    vis = _hlr_curves(h.VCompound(), defl) + _hlr_curves(h.OutLineVCompound(), defl)
    hid = _hlr_curves(h.HCompound(), defl) + _hlr_curves(h.OutLineHCompound(), defl)
    tan = _hlr_curves(h.Rg1LineVCompound(), defl)
    res = {"visible": vis, "hidden": _drop_covered(hid, vis, size * 1e-6 + 1e-6), "tangent": tan}
    hlr_store(shape, d, up, res, fast)
    return res


def hlr_is_quick(shape):
    """Simple parts are projected exactly straight away; anything bigger gets a quick first look."""
    return len(edges(shape)) <= 60


def snap_features(shape):
    """3D points a drawing dimension can attach to: (vertices Nx3, [(centre, axis, radius)]).
    The seam point on a full circle (where a hole's edge starts and ends) isn't a real corner, so it's left out.
    Both lists come in the shape's own order, so the same feature keeps its index while the model's topology
    stays the same (e.g. when only a dimension changes)."""
    vm = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vm)
    real = set()                         # vertices on at least one edge that isn't a full circle or a seam
    seams = seam_edge_indices(shape)
    for ei, e in enumerate(edges(shape)):
        if ei in seams:
            continue
        try:
            ad = BRepAdaptor_Curve(e)
            closed = ad.GetType() == GeomAbs_Circle and abs((ad.LastParameter() - ad.FirstParameter())
                                                             - 2 * math.pi) < 1e-7
        except Exception:
            closed = False
        fv, lv = TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)
        if not fv.IsNull() and fv.IsSame(lv):
            closed = True                    # any loop (e.g. a cross hole's rim on a round hub) has no real corner
        if closed or BRep_Tool.Degenerated_s(e):
            continue
        for v in (fv, lv):
            if not v.IsNull():
                real.add(vm.FindIndex(v))
    verts = []
    for i in range(1, vm.Size() + 1):
        verts.append(_np(BRep_Tool.Pnt_s(_as_vertex(vm.FindKey(i)))) if i in real else None)
    circles = []
    seen = {}
    for e in edges(shape):
        try:
            ad = BRepAdaptor_Curve(e)
            if ad.GetType() != GeomAbs_Circle:
                continue
            ci = ad.Circle()
            c = _np(ci.Location())
            ax = _np(ci.Axis().Direction())
            r = ci.Radius()
            full = abs((ad.LastParameter() - ad.FirstParameter()) - 2 * math.pi) < 1e-7
            k = (tuple(np.round(c, 6)), round(r, 6))
            if k in seen:
                if full:
                    j = seen[k]
                    circles[j] = (circles[j][0], circles[j][1], circles[j][2], True, False)
                continue
            seen[k] = len(circles)
            circles.append((c, ax, r, full, False))
        except Exception:
            continue
    # holes and shafts by their round faces too: a hole drilled across a round hub has no flat circular edge,
    # but its cylinder still looks like a circle when seen along its axis
    for f in faces(shape):
        try:
            ad = BRepAdaptor_Surface(f)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cy = ad.Cylinder()
            ax = _np(cy.Axis().Direction())
            p0 = _np(cy.Axis().Location())
            c = p0 - ax * float(p0 @ ax)
            r = cy.Radius()
            full = abs((ad.LastUParameter() - ad.FirstUParameter()) - 2 * math.pi) < 1e-7
            k = ("cyl", tuple(np.round(c, 6)), tuple(np.round(np.abs(ax), 6)), round(r, 6))
            if k in seen:
                continue
            seen[k] = len(circles)
            circles.append((c, ax, r, full, True))
        except Exception:
            continue
    nan = np.full(3, np.nan)
    arr = np.array([nan if q is None else q for q in verts]) if verts else np.zeros((0, 3))
    return arr, circles
