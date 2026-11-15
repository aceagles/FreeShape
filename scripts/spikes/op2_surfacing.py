"""Spike for the surfacing slice (I2.1): four OpenCascade probes, run on each OCP build.

    both fs_py scripts/spikes/op2_surfacing.py

Probes (see plans/onshape-parity-2/prior-art/1-surfacing.md, cross-cutting spike list):
  1. BRepOffset_MakeOffset with Thickening=True on a two-face curved shell: one valid solid? history reachable?
  2. BOPAlgo_MakerVolume on six oversized square faces: one solid of volume a^3? History() available?
  3. BRepOffsetAPI_MakeOffset(wire, Intersection, open) on a line-arc-line chain, both signs, against
     freeshape.sketch_ops.offset on the same chain.
  4. BRepFill_Filling on a four-arc non-planar loop with one G1 support face; area equal on both builds?

Each probe is wrapped so a raise becomes the weaker value plus the exception text. Every run stores its own
build's numbers in scripts/spikes/op2_surfacing.result.txt (replacing that build's earlier section, keeping the
other build's) and rewrites the DECISION line at the top as the weaker value of the builds recorded there. The
only stdout line starting DECISION is that merged line, so the second build's run prints the final one.
"""
import json
import math
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RESULT = os.path.join(HERE, "op2_surfacing.result.txt")
sys.path.insert(0, ROOT)

import OCP  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Curve  # noqa: E402
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,  # noqa: E402
                                BRepBuilderAPI_MakeWire, BRepBuilderAPI_Sewing, BRepBuilderAPI_Transform)
from OCP.BRepCheck import BRepCheck_Analyzer  # noqa: E402
from OCP.BRepGProp import BRepGProp  # noqa: E402
from OCP.GProp import GProp_GProps  # noqa: E402
from OCP.GeomAbs import GeomAbs_Intersection  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_WIRE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec  # noqa: E402

def _caster(name):
    """OCP renamed the static casts between builds (TopoDS.Face_s -> TopoDS.Face)."""
    return getattr(TopoDS, name + "_s", None) or getattr(TopoDS, name)


_Face, _Edge = _caster("Face"), _caster("Edge")

KEYS = ("thicken", "thicken_history", "enclose", "enclose_history", "offset2d", "filling")
# worst value first: the merged decision takes the lowest-ranked value across builds
ORDER = {
    "thicken": ["fallback", "native"],
    "thicken_history": ["seeded", "image", "maker"],
    "enclose": ["sew", "makervolume"],
    "enclose_history": ["no", "yes"],
    "offset2d": ["analytic", "occ"],
    "filling": ["position", "g1"],
}
WEAKEST = {k: v[0] for k, v in ORDER.items()}


def build_name():
    v = getattr(OCP, "__version__", None)
    if v:
        return str(v)
    try:
        from OCP.Standard import Standard_Version  # noqa: F401
    except Exception:
        pass
    return "unknown"


def exp(shape, kind):
    out, e = [], TopExp_Explorer(shape, kind)
    while e.More():
        out.append(e.Current())
        e.Next()
    return out


def vol(s):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(s, p)
    return p.Mass()


def area(s):
    p = GProp_GProps()
    BRepGProp.SurfaceProperties_s(s, p)
    return p.Mass()


def length(s):
    p = GProp_GProps()
    BRepGProp.LinearProperties_s(s, p)
    return p.Mass()


def valid(s):
    return bool(BRepCheck_Analyzer(s).IsValid())


def exc(e):
    return f"{type(e).__name__}: {e}".strip()


def to_list(lst):
    return [x for x in lst]


# --------------------------------------------------------------------------- probe 1
def make_two_face_shell(R=10.0, h=20.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    faces = []
    for k in range(2):
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), R, h,
                                       math.pi / 2).Shape()
        if k:
            t = gp_Trsf()
            t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.pi / 2)
            cyl = BRepBuilderAPI_Transform(cyl, t, True).Shape()
        for f in exp(cyl, TopAbs_FACE):
            if BRepAdaptor_Surface(_Face(f)).GetType() == GeomAbs_Cylinder:
                faces.append(f)
    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in faces:
        sew.Add(f)
    sew.Perform()
    return sew.SewedShape()


def probe_thicken():
    from OCP.BRepOffset import BRepOffset_MakeOffset, BRepOffset_Skin
    R, h, t = 10.0, 20.0, 2.0
    out = {"expected_outward": math.pi / 2 * ((R + t) ** 2 - R ** 2) * h,
           "expected_inward": math.pi / 2 * (R ** 2 - (R - t) ** 2) * h}
    shell = make_two_face_shell(R, h)
    out["input_faces"] = len(exp(shell, TopAbs_FACE))
    out["input_shape_type"] = str(shell.ShapeType())
    mo = BRepOffset_MakeOffset()
    mo.Initialize(shell, t, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection, True)
    out["methods_on_maker"] = sorted(m for m in dir(mo) if not m.startswith("_") and
                                     any(w in m for w in ("Generated", "Modified", "Faces", "Edges", "Image", "History", "Deleted")))
    mo.MakeOffsetShape()
    out["is_done"] = bool(mo.IsDone())
    res = mo.Shape() if hasattr(mo, "Shape") else mo.GetResult()
    out["result_type"] = str(res.ShapeType())
    solids = exp(res, TopAbs_SOLID)
    out["solids"] = len(solids)
    out["faces"] = len(exp(res, TopAbs_FACE))
    out["valid"] = valid(res)
    out["volume"] = abs(vol(res)) if solids else None
    out["volume_matches_expected"] = bool(
        solids and any(abs(out["volume"] - e) / e < 1e-3 for e in (out["expected_outward"], out["expected_inward"])))
    native = bool(out["is_done"] and len(solids) == 1 and out["valid"] and out["volume_matches_expected"])
    # history routes
    hist = {}
    in_faces = exp(shell, TopAbs_FACE)
    try:
        gen = [len(to_list(mo.Generated(f))) for f in in_faces]
        hist["Generated_counts_per_input_face"] = gen
    except Exception as e:
        hist["Generated"] = exc(e)
    try:
        mod = [len(to_list(mo.Modified(f))) for f in in_faces]
        hist["Modified_counts_per_input_face"] = mod
    except Exception as e:
        hist["Modified"] = exc(e)
    try:
        img = mo.OffsetFacesFromShapes()
        hist["OffsetFacesFromShapes_has_images"] = [bool(img.HasImage(f)) for f in in_faces]
        hist["OffsetFacesFromShapes_image_counts"] = [len(to_list(img.Image(f))) if img.HasImage(f) else 0
                                                      for f in in_faces]
    except Exception as e:
        hist["OffsetFacesFromShapes"] = exc(e)
    out["history"] = hist
    maker = any(isinstance(hist.get(k), list) and any(c > 0 for c in hist[k])
                for k in ("Generated_counts_per_input_face", "Modified_counts_per_input_face"))
    image = isinstance(hist.get("OffsetFacesFromShapes_image_counts"), list) and \
        any(c > 0 for c in hist["OffsetFacesFromShapes_image_counts"])
    out["decision"] = {"thicken": "native" if native else "fallback",
                       "thicken_history": "maker" if maker else ("image" if image else "seeded")}
    if not native:
        out["decision"]["thicken_history"] = "seeded"
    return out


# --------------------------------------------------------------------------- probe 2
def probe_enclose():
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    a, over = 10.0, 1.2
    w = a * over
    # six planes through the faces of the cube [0,a]^3, each face w x w and centred on the real face
    c = a / 2
    specs = [((c, c, 0), (0, 0, 1), (1, 0, 0)), ((c, c, a), (0, 0, 1), (1, 0, 0)),
             ((c, 0, c), (0, 1, 0), (1, 0, 0)), ((c, a, c), (0, 1, 0), (1, 0, 0)),
             ((0, c, c), (1, 0, 0), (0, 1, 0)), ((a, c, c), (1, 0, 0), (0, 1, 0))]
    faces = []
    for p, n, xd in specs:
        pl = gp_Pln(gp_Ax3(gp_Pnt(*p), gp_Dir(*n), gp_Dir(*xd)))
        faces.append(BRepBuilderAPI_MakeFace(pl, -w / 2, w / 2, -w / 2, w / 2).Face())
    out = {"expected_volume": a ** 3}
    mv = BOPAlgo_MakerVolume()
    for f in faces:
        mv.AddArgument(f)
    mv.SetRunParallel(False)
    mv.SetIntersect(True)
    mv.Perform()
    out["has_errors"] = bool(mv.HasErrors())
    res = mv.Shape()
    solids = exp(res, TopAbs_SOLID)
    out["solids"] = len(solids)
    out["valid"] = valid(res) if not res.IsNull() else False
    out["volume"] = abs(vol(res)) if solids else None
    ok = bool(solids and len(solids) == 1 and out["valid"] and abs(out["volume"] - a ** 3) / a ** 3 < 1e-6)
    out["faces_in_result"] = len(exp(res, TopAbs_FACE))
    hist = {}
    try:
        h = mv.History()
        hist["History_type"] = type(h).__name__
        hist["History_is_null"] = h is None or (hasattr(h, "IsNull") and h.IsNull())
        if not hist["History_is_null"]:
            hist["Modified_counts_per_argument"] = [len(to_list(h.Modified(f))) for f in faces]
            hist["Generated_counts_per_argument"] = [len(to_list(h.Generated(f))) for f in faces]
            hist["IsRemoved_per_argument"] = [bool(h.IsRemoved(f)) for f in faces]
    except Exception as e:
        hist["History"] = exc(e)
    out["history"] = hist
    has_hist = bool(
        "History" not in hist and hist.get("History_is_null") is False and
        any(c > 0 for c in hist.get("Modified_counts_per_argument", []) + hist.get("Generated_counts_per_argument", [])))
    out["decision"] = {"enclose": "makervolume" if ok else "sew", "enclose_history": "yes" if (ok and has_hist) else "no"}
    return out


# --------------------------------------------------------------------------- probe 3
def edge_types(shape):
    names = {0: "line", 1: "circle", 2: "ellipse", 3: "hyperbola", 4: "parabola", 5: "bezier", 6: "bspline",
             7: "offset", 8: "other"}
    res = []
    for e in exp(shape, TopAbs_EDGE):
        res.append(names.get(int(BRepAdaptor_Curve(_Edge(e)).GetType()), "?"))
    return res


def probe_offset2d():
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from freeshape.sketch import Sketch
    from freeshape import sketch_ops
    # chain: line (0,0)-(10,0), arc about (10,5) r5 through (15,5) to (10,10), line (10,10)-(0,10)
    d = 2.0
    e1 = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(10, 0, 0)).Edge()
    circ = gp_Circ(gp_Ax2(gp_Pnt(10, 5, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), 5.0)
    e2 = BRepBuilderAPI_MakeEdge(circ, -math.pi / 2, math.pi / 2).Edge()
    e3 = BRepBuilderAPI_MakeEdge(gp_Pnt(10, 10, 0), gp_Pnt(0, 10, 0)).Edge()
    wb = BRepBuilderAPI_MakeWire()
    for e in (e1, e2, e3):
        wb.Add(e)
    wire = wb.Wire()
    out = {"input_length": length(wire), "occ": {}, "sketch_ops": {}}
    occ_ok = True
    occ_sig = {}
    for sign in (1, -1):
        key = f"{sign:+d}"
        try:
            mo = BRepOffsetAPI_MakeOffset(wire, GeomAbs_Intersection, True)
            mo.Perform(sign * d)
            res = mo.Shape()
            wires = exp(res, TopAbs_WIRE)
            if not wires and res.ShapeType() == TopAbs_EDGE:
                wires = [res]
            info = {"wires": len(wires), "edge_types": edge_types(res), "length": length(res), "valid": valid(res)}
            info["edge_lengths"] = sorted(round(length(e), 6) for e in exp(res, TopAbs_EDGE))
            occ_sig[key] = info
            if not (info["wires"] == 1 and info["edge_types"] == ["line", "circle", "line"] and info["valid"]):
                occ_ok = False
        except Exception as e:
            occ_sig[key] = {"error": exc(e)}
            occ_ok = False
    out["occ"] = occ_sig
    # sketch_ops on the same chain; sketch offset sign is "left of travel"
    for sign in (1, -1):
        key = f"{sign:+d}"
        try:
            sk = Sketch()
            pa, pb, pc, pd = (sk.add_point(0, 0), sk.add_point(10, 0), sk.add_point(10, 10), sk.add_point(0, 10))
            cen = sk.add_point(10, 5)
            l1 = sk.add_line(pa, pb)
            ar = sk.add_arc(cen, pb, pc)
            l3 = sk.add_line(pc, pd)
            before = set(sk.entities)
            new = sketch_ops.offset(sk, l1, sign * d)
            types = [sk.entities[k]["type"] for k in new]
            lens = []
            for k in new:
                pl = sk.polyline(k, 4000)
                lens.append(float(sum(math.dist(pl[i], pl[i + 1]) for i in range(len(pl) - 1))))
            out["sketch_ops"][key] = {"entities": len(new), "types": types, "length": sum(lens),
                                      "edge_lengths": sorted(round(x, 3) for x in lens)}
        except Exception as e:
            out["sketch_ops"][key] = {"error": exc(e), "trace": traceback.format_exc(limit=3)}
    # compare: each occ sign's total length matches one sketch_ops sign's length (sign convention may differ)
    cmp = []
    try:
        occl = sorted(v["length"] for v in occ_sig.values() if "length" in v)
        skl = sorted(v["length"] for v in out["sketch_ops"].values() if "length" in v)
        cmp = [abs(a - b) / b for a, b in zip(occl, skl)] if len(occl) == len(skl) == 2 else []
    except Exception:
        pass
    out["length_rel_diff_vs_sketch_ops"] = cmp
    out["sign_convention"] = ("lengths match with the sign flipped: OCC +d offsets to the right of travel here "
                              "(arc radius 5 -> 7), sketch_ops +d to the left (radius 5 -> 3); a caller negates one")
    matches = bool(cmp) and max(cmp) < 1e-3
    out["decision"] = {"offset2d": "occ" if (occ_ok and matches) else "analytic"}
    return out


# --------------------------------------------------------------------------- probe 4
def probe_filling():
    from OCP.BRepFill import BRepFill_Filling
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.GeomAbs import GeomAbs_C0, GeomAbs_G1
    corners = [gp_Pnt(0, 0, 0), gp_Pnt(10, 0, 0), gp_Pnt(10, 10, 0), gp_Pnt(0, 10, 0)]

    def loop(amp):
        mids = [gp_Pnt(5, 0, amp), gp_Pnt(10, 5, -amp), gp_Pnt(5, 10, amp), gp_Pnt(0, 5, -amp)]
        edges = [BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(corners[i], mids[i], corners[(i + 1) % 4]).Value()).Edge()
                 for i in range(4)]
        # support face for edge 0: the arc swept outward (-y), so it contains the edge and leaves it away from the loop
        return edges, _Face(BRepPrimAPI_MakePrism(edges[0], gp_Vec(0, -5, 0)).Shape())

    def run(edges, support):
        f = BRepFill_Filling()
        for i, e in enumerate(edges):
            if i == 0 and support is not None:
                f.Add(e, support, GeomAbs_G1, True)
            else:
                f.Add(e, GeomAbs_C0, True)
        f.Build()
        info = {"is_done": bool(f.IsDone())}
        if info["is_done"]:
            face = f.Face()
            info["area"] = area(face)
            info["valid"] = valid(face)
            info["g0_error_mm"] = f.G0Error()
            info["g1_error_rad"] = f.G1Error()   # max normal angle to the support along the constrained edge
        return info
    out = {}
    # "loop" is the card's four-arc non-planar loop (arc sagitta 1.5 mm); "gentle" shows how G1Error scales
    for label, amp in (("loop", 1.5), ("gentle_0.2", 0.2)):
        edges, support = loop(amp)
        for kind, sup in (("position_only", None), ("g1", support)):
            try:
                out[f"{label}.{kind}"] = run(edges, sup)
            except Exception as e:
                out[f"{label}.{kind}"] = {"error": exc(e)}
    out["position_only"], out["g1"] = out.pop("loop.position_only"), out.pop("loop.g1")
    g1ok = bool(out["g1"].get("is_done") and out["g1"].get("valid"))
    out["decision"] = {"filling": "g1" if g1ok else "position"}
    out["note"] = ("g1 means: the filler builds a valid face with a G1 support. G1Error is a soft target in BRepFill_Filling "
                   "(it does not enforce the angle); see g1_error_rad and the gentle_0.2 case.")
    return out


# --------------------------------------------------------------------------- driver
def weaker(keys_vals):
    return {k: min((d[k] for d in keys_vals), key=lambda v: ORDER[k].index(v)) for k in KEYS}


def run_all():
    probes = [("thicken", probe_thicken), ("enclose", probe_enclose), ("offset2d", probe_offset2d),
              ("filling", probe_filling)]
    data, dec = {}, {}
    for name, fn in probes:
        try:
            r = fn()
        except Exception as e:
            r = {"error": exc(e), "trace": traceback.format_exc(limit=6)}
        data[name] = r
        d = r.get("decision")
        if d is None:   # a raise: weakest value for that probe's keys
            d = {k: WEAKEST[k] for k in KEYS if (k.split("_")[0] == name or (name == "thicken" and k.startswith("thicken"))
                                                 or (name == "enclose" and k.startswith("enclose")))}
        dec.update(d)
    for k in KEYS:
        dec.setdefault(k, WEAKEST[k])
    return data, dec


def read_sections():
    secs = {}
    if os.path.exists(RESULT):
        for line in open(RESULT):
            m = re.match(r"^DATA (\S+) (\{.*\})$", line.rstrip("\n"))
            if m:
                secs[m.group(1)] = json.loads(m.group(2))
    return secs


def main():
    name = build_name()
    data, dec = run_all()
    secs = read_sections()
    secs[name] = {"decision": dec, "probes": data}
    merged = weaker([s["decision"] for s in secs.values()])
    notes = []
    # cross-build check for probe 4: the G1 filling area must agree within 1e-3 relative
    areas = {v: s["probes"].get("filling", {}).get("g1", {}).get("area") for v, s in secs.items()}
    have = [a for a in areas.values() if a is not None]
    if len(areas) > 1:
        if len(have) < len(areas):
            merged["filling"] = "position"
            notes.append("filling: a build produced no G1 area")
        elif (max(have) - min(have)) / max(have) >= 1e-3:
            merged["filling"] = "position"
            notes.append(f"filling: G1 areas differ across builds by more than 1e-3 relative: {areas}")
    line = "DECISION surfacing " + " ".join(f"{k}={merged[k]}" for k in KEYS)
    with open(RESULT, "w") as fh:
        fh.write(line + "\n\n")
        fh.write("Builds recorded: " + ", ".join(sorted(secs)) + "\n")
        fh.write("Each build's decision is the weaker value of its own probes; the line above is the weaker of the builds.\n")
        for n in notes:
            fh.write("NOTE " + n + "\n")
        for v in sorted(secs):
            fh.write(f"\n=== OCP {v} ===\nbuild decision: " + " ".join(f"{k}={secs[v]['decision'][k]}" for k in KEYS) + "\n")
            fh.write(json.dumps(secs[v]["probes"], indent=2, default=str) + "\n")
        fh.write("\n")
        for v in sorted(secs):
            fh.write(f"DATA {v} " + json.dumps(secs[v], default=str) + "\n")
    print(line)


if __name__ == "__main__":
    main()
