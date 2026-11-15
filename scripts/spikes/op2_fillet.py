"""Spike I8.1: per-vertex fillet radii and contour inspection on the installed OCP build.

    fs_py scripts/spikes/op2_fillet.py        (run it on both OCP builds: `both fs_py ...`)

Changes no product code. Prints one `DECISION fillet ...` line and writes the report (that line, the
measured offsets and the inspection-method table) to scripts/spikes/op2_fillet.result.txt, appending
one section per build so a two-build run leaves both in the file.

Route tried first: Add(E) with no radius, ResetContour(ic), SetRadius(r, ic, V) once per vertex of
the contour. Fallback: Add(R1, R2, E) per edge, R1/R2 picked by FirstVertex and Abscissa.
The overloads taking a function object (a Law_Function argument to Add, SetRadius and SetLaw) crash the
process in OCCT 7.9.3 and 8.0.0 and are never called. A radius of 0 is never probed.
"""
import math
import os
import sys

import OCP
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "op2_fillet.result.txt")
BUILD = "ocp" + str(getattr(OCP, "__version__", "?"))
R_LO, R_HI = 2.0, 6.0
FRACS = (0.0, 0.25, 0.5, 0.75, 1.0)


def subshapes(shape, kind, cast):
    out = []
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        s = cast(ex.Current())
        if not any(s.IsSame(o) for o in out):
            out.append(s)
        ex.Next()
    return out


def _cast(name):
    # 7.9 spells the downcasts Face_s / Edge_s / Vertex_s, 8.0 Face / Edge / Vertex
    return getattr(TopoDS, name + "_s", None) or getattr(TopoDS, name)


def faces(s):
    return subshapes(s, TopAbs_FACE, _cast("Face"))


def edges(s):
    return subshapes(s, TopAbs_EDGE, _cast("Edge"))


def verts(s):
    return subshapes(s, TopAbs_VERTEX, _cast("Vertex"))


def point(v):
    from OCP.BRep import BRep_Tool
    return BRep_Tool.Pnt_s(v)


def valid(shape):
    return bool(BRepCheck_Analyzer(shape).IsValid())


def volume(shape):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def make_block():
    box = BRepPrimAPI_MakeBox(40.0, 20.0, 10.0).Shape()
    for e in edges(box):
        a, b = (point(v) for v in verts(e))
        if abs(a.Z() - 10) < 1e-6 and abs(b.Z() - 10) < 1e-6 and abs(a.Y() - 20) < 1e-6 and abs(b.Y() - 20) < 1e-6:
            return box, [e]
    raise RuntimeError("block edge not found")


def make_boss():
    """40 x 20 x 10 boss with 8 mm corner arcs: the top outline is a closed tangent chain of
    4 lines and 4 arcs. Corners are rounded with the constant-radius fillet.
    The corner radius (8) stays above the largest variable radius (6) so the offset curve stays valid."""
    box = BRepPrimAPI_MakeBox(40.0, 20.0, 10.0).Shape()
    mk = BRepFilletAPI_MakeFillet(box)
    for e in edges(box):
        a, b = (point(v) for v in verts(e))
        if abs(a.X() - b.X()) < 1e-6 and abs(a.Y() - b.Y()) < 1e-6:
            mk.Add(8.0, e)
    mk.Build()
    boss = mk.Shape()
    top = [e for e in edges(boss)
           if all(abs(point(v).Z() - 10) < 1e-6 for v in verts(e))]
    # a long line edge (y = 20) first, then the rest of the loop is reached by tangent propagation
    line = [e for e in top if BRepAdaptor_Curve(e).GetType().name == "GeomAbs_Line"
            and abs(point(verts(e)[0]).Y() - 20) < 1e-6]
    return boss, top, line[0]


def contour_vertices(mk, ic):
    out = []
    for j in range(1, mk.NbEdges(ic) + 1):
        for v in verts(mk.Edge(ic, j)):
            if not any(v.IsSame(o) for o in out):
                out.append(v)
    return out


def radius_table(mk, ic, vlist, radii):
    """vertex -> radius for contour ic; vertices ordered along the contour by Abscissa."""
    return {i: radii(mk.Abscissa(ic, v), v) for i, v in enumerate(vlist)}


def run_vertex_route(shape, edge_list, radius_of, reset):
    """Returns (mk, ok, note). radius_of(abscissa, vertex, ic) -> radius."""
    mk = BRepFilletAPI_MakeFillet(shape)
    for e in edge_list:
        mk.Add(e)
    for ic in range(1, mk.NbContours() + 1):
        if reset:
            mk.ResetContour(ic)
        for v in contour_vertices(mk, ic):
            mk.SetRadius(radius_of(mk, ic, v), ic, v)
    mk.Build()
    ok = bool(mk.IsDone())
    return mk, ok


def run_edge_pair_route(shape, edge_list, r_first, r_last_of):
    """Fallback. Probe builder gives FirstVertex/Abscissa; the real builder gets Add(R1, R2, E)."""
    probe = BRepFilletAPI_MakeFillet(shape)
    for e in edge_list:
        probe.Add(e)
    mk = BRepFilletAPI_MakeFillet(shape)
    for ic in range(1, probe.NbContours() + 1):
        for j in range(1, probe.NbEdges(ic) + 1):
            e = probe.Edge(ic, j)
            va, vb = verts(e)
            if probe.Abscissa(ic, va) > probe.Abscissa(ic, vb):
                va, vb = vb, va
            mk.Add(r_last_of(probe, ic, va), r_last_of(probe, ic, vb), e)
    mk.Build()
    return mk, bool(mk.IsDone())


def contact_offsets(mk, result, edge, tops_z=10.0):
    """Distance from points on the original edge (at FRACS of its length) to the line where the
    fillet's new faces meet the planar top face: the fillet radius at that station."""
    gen = list(mk.Generated(edge))
    contact = []
    for f in faces(result):
        s = BRepAdaptor_Surface(f)
        if s.GetType() != GeomAbs_Plane:
            continue
        if abs(s.Plane().Axis().Direction().Z()) < 0.999:
            continue
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, p)
        if abs(p.CentreOfMass().Z() - tops_z) > 1e-6:
            continue
        for e in edges(f):
            for g in gen:
                if any(e.IsSame(ge) for ge in edges(g)):
                    contact.append(e)
    c = BRepAdaptor_Curve(edge)
    u0, u1 = c.FirstParameter(), c.LastParameter()
    # parameter 0 must be the vertex that was given R_LO; callers pass edges oriented by param
    out = []
    for f in FRACS:
        pnt = c.Value(u0 + f * (u1 - u0))
        d = BRepExtrema_DistShapeShape()
        d.LoadS1(BRepBuilderAPI_MakeVertex(pnt).Vertex())
        best = None
        for e in contact:
            d.LoadS2(e)
            d.Perform()
            if d.IsDone():
                best = d.Value() if best is None else min(best, d.Value())
        out.append(best)
    return out, len(gen)


def edge_param_dir(edge):
    c = BRepAdaptor_Curve(edge)
    return c.Value(c.FirstParameter())


def probe_methods(mk, ic, v, e):
    """callable / missing / error per inspection method, after Build."""
    calls = [
        ("NbContours", lambda: mk.NbContours()),
        ("NbEdges", lambda: mk.NbEdges(ic)),
        ("Edge", lambda: mk.Edge(ic, 1)),
        ("FirstVertex", lambda: mk.FirstVertex(ic)),
        ("LastVertex", lambda: mk.LastVertex(ic)),
        ("Abscissa", lambda: mk.Abscissa(ic, v)),
        ("NbFaultyContours", lambda: mk.NbFaultyContours()),
        ("FaultyContour", lambda: mk.FaultyContour(1)),
        ("StripeStatus", lambda: mk.StripeStatus(ic)),
        ("NbFaultyVertices", lambda: mk.NbFaultyVertices()),
        ("FaultyVertex", lambda: mk.FaultyVertex(1)),
        ("HasResult", lambda: mk.HasResult()),
    ]
    res = {}
    for name, fn in calls:
        if not hasattr(mk, name):
            res[name] = "missing"
            continue
        try:
            val = fn()
            res[name] = "callable (%s)" % (val if isinstance(val, (int, float, bool)) or hasattr(val, "name") else type(val).__name__)
        except Exception as exc:  # callable but nothing to return, eg FaultyContour(1) on a good build
            res[name] = "callable (raised %s on this input)" % type(exc).__name__
    return res


def ease_report(vals):
    base = vals[0]
    return [None if v is None else round(v, 3) for v in vals]


def main():
    lines = ["== %s (python %s)" % (BUILD, sys.version.split()[0])]
    failures = []

    # ---- block, edge pair of radii 2 -> 6, both with and without ResetContour
    block, ble = make_block()
    edge = ble[0]
    c = BRepAdaptor_Curve(edge)
    start = c.Value(c.FirstParameter())
    lo_x = start.X()  # the vertex at the edge's parameter start gets R_LO
    vol0 = volume(block)

    def blk_radius(mk, ic, v):
        return R_LO if abs(point(v).X() - lo_x) < 1e-6 else R_HI

    results = {}
    for reset in (True, False):
        mk, ok = run_vertex_route(block, ble, blk_radius, reset)
        key = "reset" if reset else "no_reset"
        info = {"done": ok}
        if ok:
            res = mk.Shape()
            info["valid"] = valid(res)
            info["volume_removed"] = round(vol0 - volume(res), 3)
            info["faces"] = len(faces(res))
            info["offsets"], info["faces_per_edge"] = contact_offsets(mk, res, edge)
        results[key] = (mk, info)
        lines.append("block vertex-route %s: %s" % (key, info))
    mk_b = results["reset"][0]
    ic1 = 1
    lines.append("block contours=%d edges_in_contour1=%d" % (mk_b.NbContours(), mk_b.NbEdges(1)))

    # ---- block, fallback Add(R1, R2, E)
    def pair_radius(probe, ic, v):
        return R_LO if abs(point(v).X() - lo_x) < 1e-6 else R_HI
    try:
        mk_p, ok_p = run_edge_pair_route(block, ble, R_LO, pair_radius)
        info = {"done": ok_p}
        if ok_p:
            res = mk_p.Shape()
            info["valid"] = valid(res)
            info["offsets"], info["faces_per_edge"] = contact_offsets(mk_p, res, edge)
        lines.append("block edge-pair route: %s" % info)
        pair_ok = ok_p and info.get("valid", False)
    except Exception as exc:
        lines.append("block edge-pair route raised %r" % exc)
        pair_ok = False

    # ---- boss: closed tangent loop, radii alternate R_LO / R_HI around the 8 vertices
    boss, top, line_edge = make_boss()
    bvol0 = volume(boss)
    probe = BRepFilletAPI_MakeFillet(boss)
    probe.Add(line_edge)
    order = {}
    for ic in range(1, probe.NbContours() + 1):
        vs = sorted(contour_vertices(probe, ic), key=lambda v: probe.Abscissa(ic, v))
        for k, v in enumerate(vs):
            order[(ic, k)] = v
    first_v = probe.FirstVertex(1)
    lines.append("boss contours=%d edges_in_contour1=%d closed=%s" % (
        probe.NbContours(), probe.NbEdges(1), probe.Closed(1)))
    vlist = sorted(contour_vertices(probe, 1), key=lambda v: probe.Abscissa(1, v))
    lines.append("boss contour vertices=%d" % len(vlist))

    def boss_radius(mk, ic, v):
        idx = next(i for i, o in enumerate(sorted(contour_vertices(mk, ic), key=lambda w: mk.Abscissa(ic, w))) if o.IsSame(v))
        return R_LO if idx % 2 == 0 else R_HI

    boss_info = {}
    for reset in (True, False):
        mk, ok = run_vertex_route(boss, [line_edge], boss_radius, reset)
        key = "reset" if reset else "no_reset"
        info = {"done": ok}
        if ok:
            res = mk.Shape()
            info["valid"] = valid(res)
            info["volume_removed"] = round(bvol0 - volume(res), 3)
            info["faces_added"] = len(faces(res)) - len(faces(boss))
            # offset along the chosen line edge, between an R_LO and an R_HI vertex
            c1 = BRepAdaptor_Curve(line_edge)
            p0 = c1.Value(c1.FirstParameter())
            near_lo = min(verts(line_edge), key=lambda v: point(v).Distance(p0))
            flip = boss_radius(mk, 1, near_lo) != R_LO
            offs, nfaces = contact_offsets(mk, res, line_edge, tops_z=10.0)
            if flip:
                offs = offs[::-1]
            info["offsets"] = [None if o is None else round(o, 3) for o in offs]
            info["faces_per_edge"] = nfaces
        boss_info[key] = info
        lines.append("boss vertex-route %s: %s" % (key, info))

    # ---- boss, fallback
    try:
        def boss_pair(probe_, ic, v):
            idx = next(i for i, o in enumerate(sorted(contour_vertices(probe_, ic), key=lambda w: probe_.Abscissa(ic, w))) if o.IsSame(v))
            return R_LO if idx % 2 == 0 else R_HI
        mk_bp, ok_bp = run_edge_pair_route(boss, [line_edge], R_LO, boss_pair)
        info = {"done": ok_bp}
        if ok_bp:
            res = mk_bp.Shape()
            info["valid"] = valid(res)
        lines.append("boss edge-pair route: %s" % info)
    except Exception as exc:
        lines.append("boss edge-pair route raised %r" % exc)

    # ---- inspection after Build, on the block's working builder
    v0 = verts(edge)[0]
    insp = probe_methods(mk_b, 1, v0, edge)
    # the failure methods are meaningful on a failing build: make one (radius too big for the face)
    fail = BRepFilletAPI_MakeFillet(block)
    fail.Add(15.0, edge)
    fail_info = {}
    try:
        fail.Build()
        fail_info["done"] = bool(fail.IsDone())
        fail_info["NbFaultyContours"] = fail.NbFaultyContours()
        fail_info["NbFaultyVertices"] = fail.NbFaultyVertices()
        fail_info["HasResult"] = bool(fail.HasResult())
        if fail.NbFaultyContours() > 0:
            fc = fail.FaultyContour(1)
            fail_info["FaultyContour(1)"] = fc
            fail_info["StripeStatus"] = fail.StripeStatus(fc).name
        if fail.NbFaultyVertices() > 0:
            fv = fail.FaultyVertex(1)
            fail_info["FaultyVertex(1)"] = "vertex at %s" % tuple(round(x, 2) for x in (point(fv).X(), point(fv).Y(), point(fv).Z()))
    except Exception as exc:
        fail_info["raised"] = repr(exc)
    lines.append("failing build (radius 15 on 10 high block): %s" % fail_info)
    for k, v in insp.items():
        lines.append("inspect %s: %s" % (k, v))
    missing = [k for k, v in insp.items() if v == "missing"]
    all_callable = not missing and all(v.startswith("callable") for v in insp.values())
    inspect_ok = "ok" if all_callable and fail_info.get("NbFaultyContours", 0) > 0 else "partial"

    # ---- decide
    rb, nb = results["reset"][1], results["no_reset"][1]
    vertex_ok = rb.get("done") and rb.get("valid") and rb["offsets"][1] is not None
    if vertex_ok:
        route = "vertex"
    elif pair_ok:
        route = "edge_pair"
    else:
        route = "none"
    nr_same = (nb.get("done") and nb.get("valid") and nb.get("offsets") is not None
               and all(a is not None and b is not None and abs(a - b) < 1e-3
                       for a, b in zip(nb["offsets"], rb["offsets"])))
    reset_flag = "not_needed" if nr_same else "needed"
    src = {"vertex": rb, "edge_pair": None}[route] if route == "vertex" else None
    if route == "vertex":
        offs, nfaces = rb["offsets"], rb["faces_per_edge"]
    elif route == "edge_pair":
        offs, nfaces = info["offsets"], info["faces_per_edge"]
    else:
        offs, nfaces = [float("nan")] * 5, 0

    def at(i):
        v = offs[i]
        return "%.3f" % v if v is not None else "nan"
    decision = ("DECISION fillet route=%s reset_contour=%s inspect=%s faces_per_edge=%d "
                "ease_25pct=%s ease_50pct=%s ease_75pct=%s" % (route, reset_flag, inspect_ok, nfaces, at(1), at(2), at(3)))
    lines.append("block offsets at 0/25/50/75/100%% (radii %.0f -> %.0f; a straight line gives 2/3/4/5/6): %s" % (
        R_LO, R_HI, [None if o is None else round(o, 3) for o in offs]))
    lines.append(decision)
    print(decision)

    # report file: one section per build (replace this build's section if present)
    text = ""
    if os.path.exists(RESULT):
        with open(RESULT) as fh:
            text = fh.read()
    header = "== " + BUILD
    sections = [s for s in text.split("\n\n") if s.strip() and not s.startswith(header)]
    sections.append("\n".join(lines))
    with open(RESULT, "w") as fh:
        fh.write("\n\n".join(sections) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
