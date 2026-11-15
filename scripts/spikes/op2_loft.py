"""Spike I7.1: skinning route (Hermite end derivatives, ghost section) and GeomFill_Gordon.

Run on both builds:  both fs_py scripts/spikes/op2_loft.py
Prints one DECISION line per build and keeps a per-build section in
op2_loft.result.txt beside this file (the other build's section is kept).

Angle measured: deviation of the loft-direction derivative from the profile
normal z at 8 points per first-profile edge, both ends. For a side face this is
asin(|n_side . z|), i.e. how far the side-face normal is from lying in the
profile plane (90 degrees to z).  0 means the wall leaves the profile normal to it.
"""
import math
import os
import sys
import traceback

import numpy as np
import OCP
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeSolid,
                                BRepBuilderAPI_Sewing, BRepBuilderAPI_Transform)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepFill import BRepFill_CompatibleWires
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.Geom import Geom_BSplineSurface, Geom_Circle
from OCP.GeomAPI import GeomAPI_Interpolate, GeomAPI_ProjectPointOnSurf
from OCP.GeomConvert import GeomConvert_ApproxCurve, GeomConvert
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

try:
    from OCP.TColgp import TColgp_HArray1OfPnt as PntArr
    from OCP.TColStd import TColStd_HArray1OfReal as RealHArr
    from OCP.TopTools import TopTools_SequenceOfShape as ShapeSeq
    from OCP.TColStd import TColStd_Array1OfReal as RealArr, TColStd_Array1OfInteger as IntArr
    from OCP.TColgp import TColgp_Array2OfPnt as Pnt2Arr
    from OCP.TColGeom import TColGeom_Array1OfCurve as CurveArr
except ImportError:  # OCP 8.0
    from OCP.OCP.collections import HArray1_gp_Pnt as PntArr
    from OCP.OCP.collections import HArray1_double as RealHArr
    from OCP.OCP.collections import Sequence_TopoDS_Shape as ShapeSeq
    from OCP.OCP.collections import Array1_Geom_Curve as CurveArr
    from OCP.OCP.collections import Array1_double as RealArr, Array1_int as IntArr
    from OCP.OCP.collections import Array2_gp_Pnt as Pnt2Arr

BUILD = os.environ.get("OCP") or os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(OCP.__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.join(HERE, "op2_loft.result.txt")
LOG = []
Z = np.array([0.0, 0.0, 1.0])


def _cast(kind, shape):
    """TopoDS static casts were renamed between 7.9 (Wire_s) and 8.0 (Wire)."""
    fn = getattr(TopoDS, kind + "_s", None) or getattr(TopoDS, kind)
    return fn(shape)


def log(msg):
    LOG.append(msg)
    print(msg, file=sys.stderr)


def square(x0, z, s=20.0):
    p = BRepBuilderAPI_MakePolygon()
    for x, y in ((x0, 0), (x0 + s, 0), (x0 + s, s), (x0, s)):
        p.Add(gp_Pnt(x, y, z))
    p.Close()
    return p.Wire()


def wire_pts(w):
    out = []
    ex = BRepTools_WireExplorer(w)
    while ex.More():
        from OCP.BRep import BRep_Tool as B
        from OCP.TopoDS import TopoDS as T
        from OCP.TopAbs import TopAbs_FORWARD
        v = B.Pnt_s(ex.CurrentVertex())
        out.append(np.array([v.X(), v.Y(), v.Z()]))
        ex.Next()
    return out


def check_solid(shape):
    ok = BRepCheck_Analyzer(shape).IsValid()
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return bool(ok), g.Mass()


def faces_of(shape):
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        yield _cast('Face', ex.Current())
        ex.Next()


def end_angle_deg(shape, n_per_edge=8):
    """Max deviation of the side-face tangent in the loft direction from z at both ends."""
    worst = 0.0
    count = 0
    for f in faces_of(shape):
        s = BRepAdaptor_Surface(f)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(f)
        # skip the planar caps (their normal is z everywhere)
        if abs(s.GetType().value if hasattr(s.GetType(), "value") else int(s.GetType())) == 0:
            continue
        # which parameter runs along the loft? choose the one whose derivative has the larger z-extent
        mu, mv = (u0 + u1) / 2, (v0 + v1) / 2
        p = gp_Pnt(); du = gp_Vec(); dv = gp_Vec()
        s.D1(mu, mv, p, du, dv)
        loft_is_v = abs(dv.Z()) >= abs(du.Z())
        for end in (0, 1):
            for k in range(n_per_edge):
                t = (k + 0.5) / n_per_edge
                if loft_is_v:
                    uu = u0 + t * (u1 - u0); vv = v0 if end == 0 else v1
                else:
                    vv = v0 + t * (v1 - v0); uu = u0 if end == 0 else u1
                s.D1(uu, vv, p, du, dv)
                d = dv if loft_is_v else du
                a = np.array([d.X(), d.Y(), d.Z()])
                if np.linalg.norm(a) < 1e-12:
                    continue
                ang = math.degrees(math.acos(min(1.0, abs(a @ Z) / np.linalg.norm(a))))
                worst = max(worst, ang); count += 1
    return worst, count


def compat(w0, w1):
    seq = ShapeSeq()
    seq.Append(w0); seq.Append(w1)
    cw = BRepFill_CompatibleWires(seq)
    cw.Perform()
    sh = cw.Shape()
    return _cast('Wire', sh.Value(1)), _cast('Wire', sh.Value(2))


def hermite_skin(w0, w1, mag=1.0):
    c0, c1 = compat(w0, w1)
    P0, P1 = wire_pts(c0), wire_pts(c1)
    n = len(P0)
    assert n == len(P1)
    L = float(np.mean([np.linalg.norm(b - a) for a, b in zip(P0, P1)]))
    d = mag * L * Z  # Normal to profile, into the loft (both ends: derivative along +z)
    # control point grid: degree-1 in u (polyline closed), cubic in v
    poles = Pnt2Arr(1, n + 1, 1, 4)
    for i in range(n + 1):
        a, b = P0[i % n], P1[i % n]
        harr = PntArr(1, 2)
        harr.SetValue(1, gp_Pnt(*a)); harr.SetValue(2, gp_Pnt(*b))
        interp = GeomAPI_Interpolate(harr, False, 1e-9)
        interp.Load(gp_Vec(*d), gp_Vec(*d), False)
        interp.Perform()
        cur = interp.Curve()
        assert cur.NbPoles() == 4, cur.NbPoles()
        for j in range(1, 5):
            poles.SetValue(i + 1, j, cur.Pole(j))
    uk = RealArr(1, n + 1)
    um = IntArr(1, n + 1)
    for i in range(n + 1):
        uk.SetValue(i + 1, float(i)); um.SetValue(i + 1, 1)
    um.SetValue(1, 2); um.SetValue(n + 1, 2)
    vk = RealArr(1, 2); vm = IntArr(1, 2)
    vk.SetValue(1, 0.0); vk.SetValue(2, 1.0); vm.SetValue(1, 4); vm.SetValue(2, 4)
    surf = Geom_BSplineSurface(poles, uk, vk, um, vm, 1, 3)
    sew = BRepBuilderAPI_Sewing(1e-6)
    for i in range(n):
        sew.Add(BRepBuilderAPI_MakeFace(surf, float(i), float(i + 1), 0.0, 1.0, 1e-7).Face())
    sew.Add(BRepBuilderAPI_MakeFace(c0, True).Face())
    sew.Add(BRepBuilderAPI_MakeFace(c1, True).Face())
    sew.Perform()
    return solid_from_sewn(sew.SewedShape())


def solid_from_sewn(sewn):
    ex = TopExp_Explorer(sewn, TopAbs_SHELL)
    ms = BRepBuilderAPI_MakeSolid(_cast('Shell', ex.Current()))
    return ms.Solid()


def translate(w, dz):
    t = gp_Trsf(); t.SetTranslation(gp_Vec(0, 0, dz))
    return BRepBuilderAPI_Transform(w, t, True).Shape()


def ghost_loft(w0, w1, eps):
    ts = BRepOffsetAPI_ThruSections(True, False, 1e-6)
    for w in (w0, translate(w0, eps), translate(w1, -eps), w1):
        ts.AddWire(_cast('Wire', w))
    ts.Build()
    return ts.Shape()


def probe(name, fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a spike records every failure
        txt = "%s: %s" % (type(e).__name__, str(e).splitlines()[0] if str(e) else "")
        log("  %s EXCEPTION %s" % (name, txt))
        log("    " + traceback.format_exc().strip().splitlines()[-3].strip())
        return None


# --------------------------------------------------------------------- Gordon
def bs_curve(pts, tangents=None):
    n = len(pts)
    h = PntArr(1, n)
    for i, p in enumerate(pts):
        h.SetValue(i + 1, gp_Pnt(*p))
    it = GeomAPI_Interpolate(h, False, 1e-9)
    it.Perform()
    return it.Curve()


def curve_arr(curves):
    a = CurveArr(1, len(curves))
    for i, c in enumerate(curves):
        a.SetValue(i + 1, c)
    return a


def closed_poly_curve(wire):
    from OCP.GeomConvert import GeomConvert_CompCurveToBSplineCurve
    comp = None
    ex = BRepTools_WireExplorer(wire)
    while ex.More():
        bs = GeomConvert.CurveToBSplineCurve_s(_trim(ex.Current()))
        if comp is None:
            comp = GeomConvert_CompCurveToBSplineCurve(bs)
        else:
            comp.Add(bs, 1e-6)
        ex.Next()
    return comp.BSplineCurve()


def _trim(edge):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.Geom import Geom_TrimmedCurve
    ad = BRepAdaptor_Curve(edge)
    r = BRep_Tool.Curve_s(edge, 0.0, 1.0)
    c = r[0] if isinstance(r, tuple) else r
    return Geom_TrimmedCurve(c, ad.FirstParameter(), ad.LastParameter())


def dev_curve_to_surface(curve, surf, n=60):
    f, l = curve.FirstParameter(), curve.LastParameter()
    worst = 0.0
    for k in range(n + 1):
        p = curve.Value(f + (l - f) * k / n)
        pr = GeomAPI_ProjectPointOnSurf(p, surf)
        worst = max(worst, pr.LowerDistance() if pr.NbPoints() else float("inf"))
    return worst


def gordon_run(G, profiles, guides, tol=1e-6):
    g = G()
    g.Init(curve_arr(profiles), curve_arr(guides), tol)
    g.SetApproximationMode(G.ApproximationMode_e.ExactOnly)
    g.Perform()
    done = bool(g.IsDone())
    status = g.Status().name
    rep = "Report() not callable from Python (unregistered type GeomFill_Gordon::BuildReport)"
    out = {"IsDone": done, "Status": status}
    if done:
        s = g.Surface()
        out["IsApproximate"] = bool(g.IsApproximate())
        out["guide_dev"] = max(dev_curve_to_surface(c, s) for c in guides)
        out["profile_dev"] = max(dev_curve_to_surface(c, s) for c in profiles)
        out["surf_closed_u"] = bool(s.IsUClosed()); out["surf_closed_v"] = bool(s.IsVClosed())
        out["degree"] = (s.UDegree(), s.VDegree())
    try:
        r = g.Report()
        out["MaxGuideDeviation"] = r.MaxGuideDeviation
    except Exception as e:  # noqa: BLE001
        out["MaxGuideDeviation"] = "n/a (%s: %s)" % (type(e).__name__, str(e).splitlines()[0][:80])
    return out


def gordon_probes():
    try:
        from OCP.GeomFill import GeomFill_Gordon as G
    except ImportError as e:
        log("  GeomFill_Gordon import: absent (%s)" % e)
        return None
    res = {}
    w0, w1 = square(0, 0), square(15, 30)
    pr0, pr1 = closed_poly_curve(w0), closed_poly_curve(w1)
    g1 = bs_curve([(10, 0, 0), (10, 0, 10), (25, 0, 20), (25, 0, 30)])
    g2 = bs_curve([(10, 20, 0), (10, 20, 10), (25, 20, 20), (25, 20, 30)])

    def run(key, profiles, guides):
        r = probe(key, lambda: gordon_run(G, profiles, guides))
        res[key] = r
        log("  %s: %s" % (key, r))
        return r

    run("a_two_guides", [pr0, pr1], [g1, g2])
    run("b_one_guide_first_and_last", [pr0, pr1], [g1, g1])

    def iso():
        ts = BRepOffsetAPI_ThruSections(True, True, 1e-6)
        ts.AddWire(w0); ts.AddWire(w1); ts.Build()
        # far-side face: the one whose midpoint has the largest y
        best, by = None, -1
        for f in faces_of(ts.Shape()):
            s = BRepAdaptor_Surface(f)
            u0, u1, v0, v1 = BRepTools.UVBounds_s(f)
            p = s.Value((u0 + u1) / 2, (v0 + v1) / 2)
            if p.Y() > by and abs(p.Z() - 15) < 1e-6:
                best, by = f, p.Y()
        srf = BRep_Tool.Surface_s(best)
        u0, u1, v0, v1 = BRepTools.UVBounds_s(best)
        # iso-curve along the loft direction at mid-edge
        sa = BRepAdaptor_Surface(best)
        p0 = sa.Value((u0 + u1) / 2, v0); p1 = sa.Value((u0 + u1) / 2, v1)
        log("    far-side iso endpoints %s -> %s" % ((p0.X(), p0.Y(), p0.Z()), (p1.X(), p1.Y(), p1.Z())))
        # the loft direction is the parameter along which z changes
        pa = sa.Value(u0, v0); pb = sa.Value(u1, v0)
        loft_is_v = abs(pa.Z() - pb.Z()) < 1e-9
        c = srf.VIso((v0 + v1) / 2) if False else (srf.UIso((u0 + u1) / 2) if loft_is_v else srf.VIso((v0 + v1) / 2))
        from OCP.Geom import Geom_TrimmedCurve
        lo, hi = (v0, v1) if loft_is_v else (u0, u1)
        return GeomConvert.CurveToBSplineCurve_s(Geom_TrimmedCurve(c, lo, hi))
    iso_c = probe("c_iso", iso)
    if iso_c is not None:
        run("c_one_guide_plus_far_isocurve", [pr0, pr1], [g1, iso_c])
    else:
        res["c_one_guide_plus_far_isocurve"] = None

    c1 = bs_curve([(0, 0, 0), (0, 0, 10), (15, 0, 20), (15, 0, 30)])
    c2 = bs_curve([(20, 20, 0), (20, 20, 10), (35, 20, 20), (35, 20, 30)])
    run("d_corner_guides", [pr0, pr1], [c1, c2])

    circ0 = Geom_Circle(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 10.0)
    circ1 = Geom_Circle(gp_Ax2(gp_Pnt(15, 0, 30), gp_Dir(0, 0, 1)), 10.0)
    h1 = bs_curve([(10, 0, 0), (10, 0, 10), (25, 0, 20), (25, 0, 30)])
    h2 = bs_curve([(-10, 0, 0), (-10, 0, 10), (5, 0, 20), (5, 0, 30)])
    run("e_circle_direct", [circ0, circ1], [h1, h2])
    run("e_circle_rational_bspline", [GeomConvert.CurveToBSplineCurve_s(circ0), GeomConvert.CurveToBSplineCurve_s(circ1)], [h1, h2])

    from OCP.GeomAbs import GeomAbs_C1

    def approx(c):
        a = GeomConvert_ApproxCurve(c, 1e-6, GeomAbs_C1, 200, 5)
        assert a.IsDone(), "ApproxCurve not done"
        log("    approx error %.2e, poles %d" % (a.MaxError(), a.Curve().NbPoles()))
        return a.Curve()
    ap = probe("e_approx", lambda: [approx(circ0), approx(circ1)])
    if ap:
        run("e_circle_approx", ap, [h1, h2])
    else:
        res["e_circle_approx"] = None
    return res


def ok(r):
    return bool(r and r.get("IsDone") and r.get("guide_dev", 9) < 1e-3)


def main():
    log("== build %s" % BUILD)
    w0, w1 = square(0, 0), square(15, 30)

    log("-- skinning: Hermite end derivatives")
    herm = probe("hermite", lambda: hermite_skin(w0, w1))
    h_ang, h_valid = None, False
    if herm is not None:
        h_valid, vol = check_solid(herm)
        h_ang, n = end_angle_deg(herm)
        log("  hermite: valid=%s volume=%.4f (expect 12000) max_angle_deg=%.6f samples=%d" % (h_valid, vol, h_ang, n))

    log("-- skinning: ghost sections via ThruSections")
    best = None
    for eps in (3.0, 1.0, 0.3, 0.1):
        r = probe("ghost%g" % eps, lambda e=eps: ghost_loft(w0, w1, e))
        if r is None:
            continue
        v, vol = check_solid(r)
        a, n = end_angle_deg(r)
        log("  ghost eps=%g: valid=%s volume=%.4f max_angle_deg=%.6f samples=%d" % (eps, v, vol, a, n))
        if v and (best is None or a < best[0]):
            best = (a, eps)
    if best:
        log("  ghost best: eps=%g angle=%.6f" % (best[1], best[0]))

    use_hermite = herm is not None and h_valid and h_ang is not None and h_ang < 0.1
    if use_hermite:
        skin, skin_ang = "hermite", h_ang
    else:
        skin, skin_ang = "ghost", (best[0] if best else float("nan"))

    log("-- Gordon (8.0 only)")
    gres = gordon_probes()
    if gres is None:  # 7.9: class absent, nothing to measure
        g79, g80, one, corner, rat = "absent", "absent", "absent", "absent", "absent"
    else:
        g79 = other_build_gordon_absent()
        g80 = "ok" if ok(gres.get("a_two_guides")) else "fails"
        if ok(gres.get("b_one_guide_first_and_last")):
            one = "duplicate"
        elif ok(gres.get("c_one_guide_plus_far_isocurve")):
            one = "isocurve"
        else:
            one = "unsupported"
        corner = "ok" if ok(gres.get("d_corner_guides")) else "fails"
        if ok(gres.get("e_circle_direct")) or ok(gres.get("e_circle_rational_bspline")):
            rat = "direct"
        else:
            rat = "approximate"
    line = ("DECISION loft skin=%s skin_angle_deg=%.4f gordon79=%s gordon80=%s one_guide=%s "
            "corner_guide=%s rational=%s" % (skin, skin_ang, g79, g80, one, corner, rat))
    print(line)
    write_result(line)
    return 0


def other_build_gordon_absent():
    """gordon79 as read from the 7.9 section of the result file (this run cannot import-test 7.9)."""
    try:
        txt = open(RESULT).read()
        for sec in txt.split("##### build ")[1:]:
            if sec.startswith("ocp7.") and "gordon79=absent" in sec:
                return "absent"
    except OSError:
        pass
    return "unmeasured"


def write_result(line):
    marker = "##### build "
    sections = {}
    if os.path.exists(RESULT):
        cur = None
        for ln in open(RESULT).read().splitlines():
            if ln.startswith(marker):
                cur = ln[len(marker):].strip(); sections[cur] = []
            elif cur:
                sections[cur].append(ln)
    sections[BUILD] = [line, ""] + LOG
    with open(RESULT, "w") as fh:
        for k in sorted(sections):
            fh.write("%s%s\n%s\n\n" % (marker, k, "\n".join(sections[k]).strip("\n")))


if __name__ == "__main__":
    sys.exit(main())
