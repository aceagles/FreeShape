"""Drawings and export smoke (slice S1, gate I1.12): the export functions the UI calls, read back from the files.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). In a temporary directory it builds a
drawing and a two-part Part Studio, exports them through the same functions the UI's export commands call, and
reads every file back, one clause of the slice claim per step:

  * a drawing with a view at twice the sheet scale exports a DXF whose linear and diameter dimensions read the
    model's true sizes (20 and 6) with the dimension's own `dimlfac`, in sheet and full-size mode;
  * an angular dimension reads 30 degrees, and 150 in the quadrants next to the 30 degree angle;
  * a tolerance on a dimension is a real tolerance in the DXF (`dimtol`/`dimlim`, `dimtp`/`dimtm`), not scaled;
  * a cylinder seen side-on has a centre line on the CENTER layer;
  * the drawing also exports as SVG (true size in mm) and PNG (pixels at the dpi);
  * a two-part Part Studio exports a STEP that re-imports with both names and colours, a coarse and a fine STL
    whose triangle counts differ, an ASCII STL, a 3MF and an OBJ that hold both parts by name;
  * `atan2(1, 1)` evaluates to 45; an M16 clearance hole builds at the table diameter; a loft between two ring
    profiles has the volume of the outer loft minus the inner.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SQ1 OK`.

    both fs_py scripts/smoke/sq1_export.py
"""
import os
import shutil
import struct
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")      # the SVG and PNG painters need a Qt application
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

TMP = tempfile.mkdtemp(prefix="sq1_export_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
for _d in ("appdata", "xdg", "home"):
    os.makedirs(os.path.join(TMP, _d))

import ezdxf  # noqa: E402
import numpy as np  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import test_drawing_angular_dim as ANG  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import meshexport as ME  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf  # noqa: E402
from freeshape.expr import parse_number  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui import drawing_ui as DU  # noqa: E402

APP = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------- helpers
class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def near(a, b, tol=1e-6, what=""):
    check(abs(a - b) <= tol, f"{what or 'value'}: got {a}, want {b}")


STEPS = []


def step(name):
    """Run a step function; print `name ... ok`, or the reason and exit 1."""
    def deco(fn):
        try:
            fn()
        except Exception as e:                       # noqa: BLE001 -- any failure fails the gate
            print(f"{name} ... FAILED: {e}")
            if not isinstance(e, Fail):
                traceback.print_exc()
            _cleanup()
            sys.exit(1)
        STEPS.append(name)
        print(f"{name} ... ok", flush=True)
        return fn
    return deco


def _cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


def p(*parts):
    return os.path.join(TMP, *parts)


# ---------------------------------------------------------------------- models
def block():
    """A 20 x 20 x 10 plate with a diameter 6 through hole along z."""
    return K.cut(BRepPrimAPI_MakeBox(20, 20, 10).Shape(), K.cylinder((10, 10, -1), (0, 0, 1), 3.0, 12))


def plate_drawing(sheet_scale, view_scale):
    """Top view of the plate with a horizontal linear dimension (20) and a diameter dimension (6)."""
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    shape = block()
    g.resolver = lambda src: shape
    g.sheet["scale"] = sheet_scale
    g.refresh(notify=False)
    top = g.add_view(*DIRECTIONS["Top"], (150, 150), checkpoint=False)
    top["scale"] = view_scale
    g.refresh(notify=False)
    sp = [x for x in g.snap_points(top) if x[0] == "vertex"]
    E = lambda t: {"kind": "vertex", "p": [float(c) for c in t[1]], "i": t[3]["i"], "n": t[3]["n"]}
    lo = min(sp, key=lambda t: (t[1][1], t[1][0]))
    hi = max([t for t in sp if abs(t[1][1] - lo[1][1]) < 1e-6], key=lambda t: t[1][0])
    lin = g.add_annot({"type": "lin", "view": top["id"], "a": E(lo), "b": E(hi), "orient": "h", "pos": [150, 120]})
    c, _ax, r, _uv, ci = g.circles_in_view(top)[0]
    g.add_annot({"type": "dia", "view": top["id"], "pos": [170, 170],
                 "a": {"kind": "centre", "p": [float(x) for x in c], "r": r, "i": ci,
                       "n": len(g._snapdata()[1])}})
    return g, lin


def dims_of(path):
    return [e for e in ezdxf.readfile(path).modelspace() if e.dxftype() == "DIMENSION"]


def true_size(d):
    """What a CAD program shows: the measurement times the dimension's own linear factor."""
    return float(d.get_measurement()) * d.override().get("dimlfac", 1.0)


def two_part_studio():
    """Part Studio of a 10 x 10 x 10 box 'Base' (red) and a diameter 20 x 20 cylinder 'Lid' (blue)."""
    doc = PartStudioDoc()
    for i in range(2):
        s = Sketch()
        if i == 0:
            pts = [s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 10), s.add_point(0, 10)]
            for a, b in zip(pts, pts[1:] + pts[:1]):
                s.add_line(a, b)
        else:
            s.add_circle(s.add_point(60, 0), 10)
        s.solve()
        sk = doc.make_feature("sketch")
        sk["params"]["sketch"] = s.to_dict()
        doc.add_feature(sk)
        ex = doc.make_feature("extrude")
        ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                            depth=20.0, op="new")
        doc.add_feature(ex)
    check(not doc.errors, f"two-part studio did not build: {doc.errors}")
    a, b = list(doc.state.parts)
    doc.set_part_override(a, name="Base", color=(1.0, 0.0, 0.0))
    doc.set_part_override(b, name="Lid", color=(0.0, 0.0, 1.0))
    check(len(doc.state.parts) == 2, "expected two parts")
    return doc


def _stl_tris(path):
    return struct.unpack("<I", open(path, "rb").read()[80:84])[0]


def _circle_wire(x, y, z, r):
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r)).Edge()).Wire()


def _disc(z, r, hole=None):
    mf = BRepBuilderAPI_MakeFace(_circle_wire(0, 0, z, r))
    if hole:
        mf.Add(K._as_wire(_circle_wire(0, 0, z, hole).Reversed()))
    return mf.Face()


STUDIO = two_part_studio()


# ---------------------------------------------------------------------- DXF
@step("DXF: view at twice the sheet scale, linear and diameter dimensions read 20 and 6")
def _():
    for sheet, full in ((1.0, False), (0.5, True), (0.5, False)):
        g, _ = plate_drawing(sheet, 2.0)
        path = p(f"scale_{sheet}_{full}.dxf")
        export_dxf(g, path, full_size=full)
        dims = dims_of(path)
        check(len(dims) == 2, f"sheet {sheet} full {full}: {len(dims)} dimensions, want 2")
        lin = [d for d in dims if d.dxf.dimtype & 7 in (0, 1)]
        dia = [d for d in dims if d.dxf.dimtype & 7 == 3]
        check(len(lin) == 1 and len(dia) == 1, "want one linear and one diameter dimension")
        near(true_size(lin[0]), 20, what=f"linear (sheet {sheet}, full {full})")
        near(true_size(dia[0]), 6, what=f"diameter (sheet {sheet}, full {full})")


@step("DXF: view on the sheet scale keeps the sheet's dimlfac")
def _():
    g, _ = plate_drawing(0.5, 0.5)
    path = p("same_scale.dxf")
    export_dxf(g, path, full_size=False)
    for d in dims_of(path):
        near(d.override().get("dimlfac"), 2.0, 1e-12, "dimlfac")


@step("DXF: angular dimension reads 30 degrees")
def _():
    g, top, _ = ANG.setup(30)
    g.sheet["scale"] = 0.5
    g.add_annot(ANG.ang_spec(g, top, 30, ANG.inside_pos(g, top)))
    for full in (False, True):
        path = p(f"ang30_{full}.dxf")
        export_dxf(g, path, full_size=full)
        dims = dims_of(path)
        check(len(dims) == 1 and dims[0].dxf.dimtype & 7 == 2, "want one angular (type 2) dimension")
        near(dims[0].get_measurement(), 30, what="angle")
        check("dimlfac" not in dims[0].override().dimstyle_attribs, "an angle must not carry dimlfac")


@step("DXF: angular dimension in the quadrant beside the 30 degree angle reads 150")
def _():
    g, top, _ = ANG.setup(30)
    a = g.add_annot(ANG.ang_spec(g, top, 30, ANG.inside_pos(g, top)))
    below = g.to_paper(top, g.project(top, np.array([40.0, -3.0, ANG.H])))
    a["pos"] = [float(c) for c in below]
    path = p("ang150.dxf")
    export_dxf(g, path)
    dims = dims_of(path)
    check(len(dims) == 1 and dims[0].dxf.dimtype & 7 == 2, "want one angular dimension")
    near(dims[0].get_measurement(), 150, 1e-5, "angle")


@step("DXF: a tolerance on a dimension is a real tolerance, not scaled by the view")
def _():
    for kind, tol, want in (
            ("sym", {"type": "sym", "up": 0.1, "dp": 2}, dict(dimtol=1, dimlim=0, dimtp=0.1, dimtm=0.1)),
            ("dev", {"type": "dev", "up": 0.2, "lo": -0.1}, dict(dimtol=1, dimlim=0, dimtp=0.2, dimtm=0.1)),
            ("lim", {"type": "lim", "up": 0.2, "lo": -0.1}, dict(dimtol=0, dimlim=1, dimtp=0.2, dimtm=0.1))):
        g, lin = plate_drawing(0.5, 2.0)
        lin["tol"] = tol
        path = p(f"tol_{kind}.dxf")
        export_dxf(g, path)
        d = [x for x in dims_of(path) if x.dxf.dimtype & 7 in (0, 1)][0]
        ov = d.override()
        for key, val in want.items():
            near(float(ov.get(key) or 0), val, 1e-9, f"{kind} {key}")
        near(true_size(d), 20, what=f"{kind} measurement")
    g, _lin = plate_drawing(0.5, 2.0)                                  # no tolerance: none written
    path = p("tol_none.dxf")
    export_dxf(g, path)
    ov = [x for x in dims_of(path) if x.dxf.dimtype & 7 in (0, 1)][0].override()
    check(not ov.get("dimtol") and not ov.get("dimlim"), "a dimension without a tolerance has one in the DXF")


@step("DXF: a cylinder seen side-on has a centre line on the CENTER layer")
def _():
    shape = K.cut(BRepPrimAPI_MakeBox(60.0, 40.0, 10.0).Shape(), K.cylinder((15, 20, -1), (0, 0, 1), 4.0, 12.0))
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: shape
    g.refresh(notify=False)
    g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)    # looks along y: the hole axis is in the view
    path = p("centre.dxf")
    export_dxf(g, path)
    lines = [e for e in ezdxf.readfile(path).modelspace().query("LINE") if e.dxf.layer == "CENTER"]
    check(any(abs(e.dxf.start.x - e.dxf.end.x) < 1e-6 and abs(e.dxf.start.y - e.dxf.end.y) > 1 for e in lines),
          f"no vertical CENTER line along the hole axis ({len(lines)} CENTER lines)")


# ---------------------------------------------------------------------- SVG and PNG
@step("SVG: the drawing exports at true size in mm")
def _():
    g, _ = plate_drawing(1.0, 1.0)
    path = p("d.svg")
    DU.export_svg(g, path)
    root = ET.parse(path).getroot()
    W, H = g.sheet_size()
    check(root.tag.endswith("svg"), root.tag)
    check(root.get("width") == f"{W:g}mm" and root.get("height") == f"{H:g}mm",
          f"size {root.get('width')} x {root.get('height')}, want {W:g}mm x {H:g}mm")
    check(any(el.tag.split("}")[1] in ("path", "line", "polyline") for el in root.iter()), "no drawn geometry")


@step("PNG: the drawing exports at the dpi asked, with content")
def _():
    g, _ = plate_drawing(1.0, 1.0)
    path = p("d.png")
    DU.export_png(g, path, dpi=96)
    img = QImage(path)
    check(not img.isNull(), "PNG does not load")
    W, H = g.sheet_size()
    check(abs(img.width() - W / 25.4 * 96) <= 1 and abs(img.height() - H / 25.4 * 96) <= 1,
          f"{img.width()} x {img.height()} px for a {W:g} x {H:g} mm sheet at 96 dpi")
    colours = {img.pixel(x, y) for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}
    check(len(colours) > 1, "the PNG is blank")


# ---------------------------------------------------------------------- Part Studio export
@step("STEP: re-imports with both names and colours")
def _():
    path = p("studio.step")
    check(STUDIO.export(path) == "named", "flat STEP fallback was taken")
    got = K.import_step_with_colors(path)
    check(len(got) == 2, f"{len(got)} solids, want 2")
    by = {n: c for _s, n, c, _f in got}
    check(sorted(by) == ["Base", "Lid"], f"names {sorted(by, key=str)}")
    for name, want in (("Base", (1, 0, 0)), ("Lid", (0, 0, 1))):
        check(by[name] is not None and all(abs(x - y) <= 1 / 255 for x, y in zip(by[name], want)),
              f"{name} colour {by[name]}, want {want}")


@step("STL: coarse and fine triangle counts differ")
def _():
    coarse, fine = p("coarse.stl"), p("fine.stl")
    STUDIO.export(coarse, options={"defl": 0.2, "angle": 0.5})
    STUDIO.export(fine, options={"defl": 0.05, "angle": 0.3})
    c, f = _stl_tris(coarse), _stl_tris(fine)
    check(0 < c < f, f"coarse {c} triangles, fine {f}")
    check(os.path.getsize(fine) == 84 + 50 * f, "fine STL is not well-formed binary")


@step("STL: ASCII option writes an ASCII file")
def _():
    path = p("ascii.stl")
    STUDIO.export(path, options={"ascii": True})
    text = open(path, "rb").read()
    check(text[:5] == b"solid" and b"facet normal" in text and b"endsolid" in text, "not ASCII STL")


@step("3MF: holds both parts by name")
def _():
    path = p("studio.3mf")
    STUDIO.export(path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    check({"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= names, f"package parts {sorted(names)}")
    ns = {"m": ME.CORE_NS}
    objs = root.findall(".//m:object", ns)
    check(sorted(o.get("name") for o in objs) == ["Base", "Lid"], [o.get("name") for o in objs])
    check(len(root.findall(".//m:build/m:item", ns)) == 2, "want two build items")
    check(all(o.findall(".//m:triangle", ns) for o in objs), "an object has no triangles")


@step("OBJ: holds both parts by name")
def _():
    path = p("studio.obj")
    STUDIO.export(path)
    lines = open(path).read().splitlines()
    check(sorted(ln[2:] for ln in lines if ln.startswith("o ")) == ["Base", "Lid"], "object names")
    nv = sum(1 for ln in lines if ln.startswith("v "))
    faces = [list(map(int, ln.split()[1:])) for ln in lines if ln.startswith("f ")]
    check(faces and all(1 <= i <= nv for f in faces for i in f), "face indices out of range")


# ---------------------------------------------------------------------- expressions, holes, loft
@step("expression: atan2(1, 1) evaluates to 45")
def _():
    near(parse_number("atan2(1, 1)", strict=True), 45, 1e-9, "atan2(1, 1)")


@step("hole: M16 normal clearance hole builds at the table diameter")
def _():
    doc = PartStudioDoc()
    s = Sketch()
    pts = [s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        s.add_line(a, b)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"].update(plane={"kind": "std", "name": "Top"}, sketch=s.to_dict())
    doc.add_feature(sk)
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                        depth=10.0, op="new")
    doc.add_feature(ex)
    pid = next(iter(doc.state.parts))
    hs = Sketch()
    hs.add_point_entity(hs.add_point(30, 20))
    hs.solve()
    hf = doc.make_feature("sketch")
    hf["params"].update(plane={"kind": "std", "name": "Top"}, sketch=hs.to_dict())
    doc.add_feature(hf)
    h = doc.make_feature("hole")
    h["params"].update(sketch=hf["id"], standard="clearance", size="M16", fit="normal", end="through", style="simple")
    doc.add_feature(h)
    check(not doc.errors, f"build errors: {doc.errors}")
    want = F.CLEARANCE["M16"][1]
    near(want, 17.5, 1e-9, "table M16 normal")
    radii = [K.face_info(f).get("r", 0) for f in K.faces(doc.state.parts[pid].shape)]
    check(any(abs(r - want / 2) < 1e-6 for r in radii), f"no bore of diameter {want}; radii {radii}")


@step("loft: between two ring profiles is the outer loft minus the inner")
def _():
    for ruled in (True, False):
        body = K.loft([_disc(0, 20, 10), _disc(25, 15, 5)], ruled)
        outer = K.loft([_disc(0, 20), _disc(25, 15)], ruled)
        inner = K.loft([_disc(0, 10), _disc(25, 5)], ruled)
        check(len(K.solids(body)) == 1 and K.is_valid(body), f"ruled={ruled}: not one valid solid")
        want = K.volume(outer) - K.volume(inner)
        check(abs(K.volume(body) - want) <= 1e-6 * want, f"ruled={ruled}: volume {K.volume(body)}, want {want}")


print(f"SQ1 OK: {len(STEPS)} steps")
_cleanup()
