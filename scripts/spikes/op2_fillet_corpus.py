"""Corpus I8.5: fillets on swept and lofted bodies, retry rungs, and whether a fillet can crash the process.

    both fs_py scripts/spikes/op2_fillet_corpus.py

Changes no product code. Prints one `DECISION fillet_corpus ...` line and writes it with one row per case,
radius and build to scripts/spikes/op2_fillet_corpus.result.txt (one section per build).

Every fillet runs in its own child process (`--child CASE RADIUS RUNG`, one case per process), so a native crash
shows as a negative exit status in the parent, not as the end of the run. A child that outlives TIMEOUT seconds
is killed and recorded as a hang. The parent never runs a fillet itself.

Per case: a search child bisects the largest radius that builds; rows are the case's nominal radii plus 10, 50
and 90 percent of that largest radius, plus 120 and 300 percent of it (rows meant to fail, so the rungs have
something to rescue and the crash question gets pushed; the card did not ask for them). Each failed row then runs, in its own child each, the retry rungs:
  quasi   MakeFillet(shape, ChFi3d_QuasiAngular)
  poly    MakeFillet(shape, ChFi3d_Polynomial)
  params  SetParams with each tolerance doubled from the builder's defaults (Ta 1e-2, Tesp 1e-4, T2d 1e-5,
          TApp3d 1e-4, TolApp2d 1e-5, Fleche 1e-3; recalled from OCCT's ChFi3d_Builder, there is no getter)
  limit   ShapeFix_ShapeTolerance.LimitTolerance(copy of input, 1e-7, 1e-4), then the plain fillet
A rung rescues a row when it builds a result the kernel's own `_blend_ok` accepts.

The STEP case of the card is absent: examples/ holds no STEP file.
"""
import json
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
RESULT = os.path.join(HERE, "op2_fillet_corpus.result.txt")
TIMEOUT = 240
RUNGS = ("quasi", "poly", "params", "limit")
SEARCH_CAP = 10.0
LINE = 30.0          # straight run before and after the bend of the swept path

import OCP  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve  # noqa: E402
from OCP.BRepBuilderAPI import (BRepBuilderAPI_Copy, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,  # noqa: E402
                                BRepBuilderAPI_MakePolygon)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet  # noqa: E402
from OCP.BRepGProp import BRepGProp, BRepGProp_Face  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.GC import GC_MakeArcOfCircle  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.GeomAPI import GeomAPI_Interpolate, GeomAPI_ProjectPointOnSurf  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE  # noqa: E402
from OCP.TopExp import TopExp  # noqa: E402
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec  # noqa: E402

from freeshape import kernel as K  # noqa: E402

try:
    from OCP.TColgp import TColgp_HArray1OfPnt  # noqa: E402
except ImportError:  # OCP >= 8
    from OCP.OCP.collections import HArray1_gp_Pnt as TColgp_HArray1OfPnt

try:
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape as _ShapeMap  # noqa: E402
except ImportError:  # OCP >= 8
    _ShapeMap = K.TopTools_IndexedDataMapOfShapeListOfShape

BUILD = "ocp" + str(getattr(OCP, "__version__", "?"))


# ------------------------------------------------------------------------------------------------ bodies

def _poly_face(pts):
    mk = BRepBuilderAPI_MakePolygon()
    for p in pts:
        mk.Add(gp_Pnt(*p))
    mk.Close()
    return BRepBuilderAPI_MakeFace(mk.Wire()).Face()


def _circle_face(r, z):
    e = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), r)).Edge()
    return BRepBuilderAPI_MakeFace(K.make_wire([e])).Face()


def _spline_face(pts, z):
    """A closed periodic spline through `pts` (x, y) at height z."""
    arr = TColgp_HArray1OfPnt(1, len(pts))
    for i, (x, y) in enumerate(pts):
        arr.SetValue(i + 1, gp_Pnt(x, y, z))
    it = GeomAPI_Interpolate(arr, True, 1e-6)
    it.Perform()
    e = BRepBuilderAPI_MakeEdge(it.Curve()).Edge()
    return BRepBuilderAPI_MakeFace(K.make_wire([e])).Face()


def _centre(shape):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    c = g.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _plane_faces(shape):
    return [f for f in K.faces(shape) if K.face_info(f)["type"] == int(K.GeomAbs_Plane)]


def _sweep_body(bend):
    """10 x 10 square along line, 90 degree arc of centreline radius `bend`, line. Returns the body and the
    path length of the centreline."""
    a, b = (0, 0, 0), (LINE, 0, 0)
    s = bend * math.sin(math.pi / 4)
    mid, c = (LINE + s, bend - bend * math.cos(math.pi / 4), 0), (LINE + bend, bend, 0)
    d = (LINE + bend, bend + LINE, 0)
    edges = [BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b)).Edge(),
             BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(gp_Pnt(*b), gp_Pnt(*mid), gp_Pnt(*c)).Value()).Edge(),
             BRepBuilderAPI_MakeEdge(gp_Pnt(*c), gp_Pnt(*d)).Edge()]
    prof = _poly_face([(0, -5, -5), (0, 5, -5), (0, 5, 5), (0, -5, 5)])
    return K.sweep([prof], K.make_wire(edges))


def _sweep_chains(body, bend):
    """The lengthwise edges of the swept body, as {(side, z): [edges]} with side 'outer' or 'inner' (the inner
    side faces the bend centre) and z the sign of the profile corner. Junction edges (the 10 mm profile
    squares where line meets arc) are told apart from lengthwise edges by length: lengthwise ones exceed 10.5."""
    ends = [(0, 0, 0), (LINE + bend, bend + LINE, 0)]
    caps = [f for f in _plane_faces(body) if any(math.dist(_centre(f), q) < 1e-6 for q in ends)]
    cap_edges = [e for f in caps for e in K.edges(f)]
    out = {}
    for e in K.edges(body):
        if any(e.IsSame(c) for c in cap_edges):
            continue
        i = K.edge_info(e)
        if i["len"] < 10.5:
            continue
        pts = K.discretize_edge(e)
        m = pts[len(pts) // 2]
        z = 1 if m[2] > 0 else -1
        if m[0] < LINE - 1e-6:
            outer = m[1] < 0
        elif m[1] > bend + 1e-6:
            outer = m[0] > LINE + bend
        else:
            outer = math.hypot(m[0] - LINE, m[1] - bend) > bend
        out.setdefault(("outer" if outer else "inner", z), []).append(e)
    return out


def case_sweep4_r20():
    body = _sweep_body(20.0)
    return body, [e for es in _sweep_chains(body, 20.0).values() for e in es]


def case_sweep_outer_r20():
    body = _sweep_body(20.0)
    return body, _sweep_chains(body, 20.0)[("outer", 1)]


def case_sweep_inner_r15():
    body = _sweep_body(15.0)
    return body, _sweep_chains(body, 15.0)[("inner", 1)]


def _end_rims(body):
    """Edges of the two planar end faces."""
    out = []
    for f in _plane_faces(body):
        for e in K.edges(f):
            if not any(e.IsSame(o) for o in out):
                out.append(e)
    return out


def case_loft_sq_circle():
    body = K.loft([_poly_face([(-10, -10, 0), (10, -10, 0), (10, 10, 0), (-10, 10, 0)]), _circle_face(10, 30)])
    return body, _end_rims(body)


def case_loft_spline():
    blob = [(12 * math.cos(t) * (1 + 0.25 * math.cos(3 * t)), 9 * math.sin(t) * (1 + 0.25 * math.cos(3 * t)))
            for t in [i * 2 * math.pi / 8 for i in range(8)]]
    body = K.loft([_circle_face(10, 0), _spline_face(blob, 25)])
    return body, _end_rims(body)


def case_revolve_spline():
    arr = TColgp_HArray1OfPnt(1, 5)
    for i, (x, z) in enumerate([(8, 0), (11, 10), (7, 20), (12, 30), (9, 40)]):
        arr.SetValue(i + 1, gp_Pnt(x, 0, z))
    it = GeomAPI_Interpolate(arr, False, 1e-6)
    it.Perform()
    spline = BRepBuilderAPI_MakeEdge(it.Curve()).Edge()
    es = [spline,
          BRepBuilderAPI_MakeEdge(gp_Pnt(9, 0, 40), gp_Pnt(0, 0, 40)).Edge(),
          BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 40), gp_Pnt(0, 0, 0)).Edge(),
          BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(8, 0, 0)).Edge()]
    face = BRepBuilderAPI_MakeFace(K.make_wire(es)).Face()
    body = K.revolve([face], (0, 0, 0), (0, 0, 1), 360.0)
    rims = [e for e in K.edges(body) if K.edge_info(e)["type"] == int(K.GeomAbs_Circle)]
    return body, rims


# id -> (builder, nominal radii)
CASES = {
    "sweep4_bend20": (case_sweep4_r20, (1.0, 4.0)),
    "sweep_outer_bend20": (case_sweep_outer_r20, (1.0,)),
    "sweep_inner_bend15": (case_sweep_inner_r15, (4.0,)),
    "loft_square_circle": (case_loft_sq_circle, (1.0, 3.0)),
    "loft_spline": (case_loft_spline, (1.0, 3.0)),
    "revolve_spline_rim": (case_revolve_spline, (1.0, 3.0)),
}


# --------------------------------------------------------------------------------------------- measuring

def _face_normal(face, p):
    surf = BRep_Tool.Surface_s(face)
    pr = GeomAPI_ProjectPointOnSurf(p, surf)
    u, v = pr.LowerDistanceParameters()
    pnt, vn = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(u, v, pnt, vn)
    return vn.Normalized() if vn.Magnitude() > 0 else vn


def g1_max_angle(result, fillet_faces, npts=20):
    """Largest angle (rad) between a fillet face and its neighbour along the fillet's two longest trim lines,
    sampled at `npts` points each. None when no trim line was found."""
    m = _ShapeMap()
    TopExp.MapShapesAndAncestors_s(result, TopAbs_EDGE, TopAbs_FACE, m)
    worst = None
    in_result = K.faces(result)         # the result's own, correctly oriented instances of the fillet faces
    for f in [r for r in in_result if any(r.IsSame(g) for g in fillet_faces)]:
        cands = []
        for e in K.edges(f):
            if not m.Contains(e):
                continue
            others = [g for g in m.FindFromKey(e) if not g.IsSame(f)]
            if len(others) == 1:
                cands.append((K.edge_info(e)["len"], e, others[0]))
        cands.sort(key=lambda c: -c[0])
        for _, e, g in cands[:2]:
            ad = BRepAdaptor_Curve(e)
            t0, t1 = ad.FirstParameter(), ad.LastParameter()
            for i in range(npts):
                p = ad.Value(t0 + (t1 - t0) * (i + 0.5) / npts)
                dot = max(-1.0, min(1.0, _face_normal(f, p).Dot(_face_normal(K._as_face(g), p))))
                ang = math.acos(dot)
                worst = ang if worst is None else max(worst, ang)
    return worst


# ---------------------------------------------------------------------------------------------- the child

def _maker_for(rung):
    from OCP.ChFi3d import ChFi3d_Polynomial, ChFi3d_QuasiAngular
    if rung == "quasi":
        return lambda s: BRepFilletAPI_MakeFillet(s, ChFi3d_QuasiAngular)
    if rung == "poly":
        return lambda s: BRepFilletAPI_MakeFillet(s, ChFi3d_Polynomial)
    if rung == "params":
        def make(s):
            mk = BRepFilletAPI_MakeFillet(s)
            mk.SetParams(2e-2, 2e-4, 2e-5, 2e-4, 2e-5, 2e-3)
            return mk
        return make
    return BRepFilletAPI_MakeFillet


def child_search(case):
    body, edges = CASES[case][0]()
    lo, hi = 0.0, SEARCH_CAP
    if K._try_blend(BRepFilletAPI_MakeFillet, body, edges, hi) is not None:
        return {"largest": hi}
    for _ in range(11):
        mid = (lo + hi) / 2
        if K._try_blend(BRepFilletAPI_MakeFillet, body, edges, mid) is not None:
            lo = mid
        else:
            hi = mid
    return {"largest": lo if lo > 1e-6 else None}


def child_fillet(case, radius, rung):
    body, edges = CASES[case][0]()
    src, src_edges = body, edges
    if rung == "limit":
        from OCP.ShapeFix import ShapeFix_ShapeTolerance
        cp = BRepBuilderAPI_Copy(body, True)
        src = cp.Shape()
        src_edges = [K._caster("Edge")(cp.ModifiedShape(e)) for e in edges]
        ShapeFix_ShapeTolerance().LimitTolerance(src, 1e-7, 1e-4)
    why = {}
    out = K._try_blend(_maker_for(rung), src, src_edges, radius, why=why)
    res = {"built": out is not None, "nsolids_in": len(K.solids(body))}
    if out is None:
        cause = why.get("cause", "?")
        diag = K._diagnose_blend(why["maker"]) if "maker" in why else {"reasons": []}
        text = "; ".join(diag["reasons"])
        if cause == "exception":
            msg = str(why["exc"]).strip().splitlines()[0][:120] if str(why["exc"]).strip() else type(why["exc"]).__name__
            text = (text + "; " if text else "") + "exception: " + msg
        elif cause == "rejected":
            text = (text + "; " if text else "") + "result rejected as invalid or overlapping"
        elif not text:
            text = "build not done"
        res["status"] = f"{cause}: {text}"
        return res
    mk = why["maker"]
    faces = []
    for e in src_edges:
        try:
            for g in mk.Generated(e):
                if not any(g.IsSame(o) for o in faces):
                    faces.append(K._as_face(g))
        except Exception:
            pass
    res.update(status="built", valid=K.is_valid(out), nsolids=len(K.solids(out)),
               removed=K.volume(body) - K.volume(out), edge_length=sum(K.edge_info(e)["len"] for e in edges),
               fillet_faces=len(faces))
    res["g1"] = g1_max_angle(out, faces) if faces else None
    return res


def child_main(argv):
    case, radius, rung = argv[0], argv[1], argv[2] if len(argv) > 2 else "none"
    try:
        res = child_search(case) if radius == "search" else child_fillet(case, float(radius), rung)
    except Exception as exc:        # a Python exception is a result; a signal is not caught here by design
        res = {"built": False, "status": "child exception: " + type(exc).__name__ + " " + str(exc)[:120]}
    sys.stdout.write("\nRESULT " + json.dumps(res) + "\n")
    sys.stdout.flush()
    return 0


# --------------------------------------------------------------------------------------------- the parent

def run_child(case, radius, rung="none", timeout=TIMEOUT):
    """Run one case in its own process. Returns the child's dict plus "exit": "0", "signal N", "exit N" or
    "timeout". A child that died has no "built" key set to True and its "status" says how it died."""
    cmd = [sys.executable, os.path.abspath(__file__), "--child", case, str(radius), rung]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return {"built": False, "exit": "timeout", "status": f"hang: no result after {timeout}s"}
    res = None
    for ln in p.stdout.splitlines():
        if ln.startswith("RESULT "):
            res = json.loads(ln[7:])
    if p.returncode < 0:
        return {"built": False, "exit": f"signal {-p.returncode}",
                "status": f"crash: child killed by signal {-p.returncode}"}
    if p.returncode != 0 or res is None:
        return {"built": False, "exit": f"exit {p.returncode}", "status": f"crash: child exited {p.returncode}"}
    res["exit"] = "0"
    return res


def _fmt(res):
    g1 = res.get("g1")
    return "g1={} valid={} removed={}".format(
        "-" if g1 is None else "%.2e" % g1, res.get("valid", "-"),
        "-" if res.get("removed") is None else "%.3f" % res["removed"])


def main():
    rows, searches = [], {}
    for case, (_, nominal) in CASES.items():
        s = run_child(case, "search")
        largest = s.get("largest") if s.get("exit") == "0" else None
        searches[case] = (largest, s)
        radii = [(r, "nominal") for r in nominal]
        if largest:
            radii += [(largest * f, "%d%%" % round(f * 100)) for f in (0.1, 0.5, 0.9, 1.2, 3.0)]
        for r, tag in radii:
            res = run_child(case, "%.6g" % r)
            res.update(case=case, radius=r, tag=tag, rescued_by=None, rungs={})
            if not res["built"]:
                for rung in RUNGS:
                    rr = run_child(case, "%.6g" % r, rung)
                    res["rungs"][rung] = rr
                    if rr["built"] and rr.get("valid") and res["rescued_by"] is None:
                        res["rescued_by"] = rung
            rows.append(res)
    fails = [r for r in rows if not r["built"]]
    every = [r for r in rows] + [rr for r in rows for rr in r["rungs"].values()] + [s for _, s in searches.values()]
    crashes = sum(1 for c in every if c["exit"].startswith("signal") or c["exit"].startswith("exit"))
    hangs = sum(1 for c in every if c["exit"] == "timeout")
    rescued = sorted({rung for r in rows for rung, rr in r["rungs"].items() if rr["built"] and rr.get("valid")},
                     key=RUNGS.index)
    decision = "DECISION fillet_corpus cases=%d fail=%d crashes=%d rescued_by=%s crash_guard=%s" % (
        len(rows), len(fails), crashes, ",".join(rescued) or "none", "needed" if crashes or hangs else "not_needed")

    lines = ["== " + BUILD + " (python %s)" % sys.version.split()[0], decision,
             "hangs (child killed after %ds, counted as needing a guard, not in crashes): %d" % (TIMEOUT, hangs),
             "largest radius found by search: " + ", ".join(
                 "%s=%s" % (c, "none" if v is None else "%.3f" % v) for c, (v, _) in searches.items()),
             "case | radius | tag | built | status | rescued_by | exit | measures"]
    for r in rows:
        lines.append("%s | %.4g | %s | %s | %s | %s | %s | %s" % (
            r["case"], r["radius"], r["tag"], r["built"], r["status"], r["rescued_by"] or "-", r["exit"], _fmt(r)))
        for rung, rr in r["rungs"].items():
            lines.append("    rung %s | built=%s | %s | exit %s | %s" % (
                rung, rr["built"], rr["status"], rr["exit"], _fmt(rr)))
    print(decision)

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
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        sys.exit(child_main(sys.argv[2:]))
    sys.exit(main())
