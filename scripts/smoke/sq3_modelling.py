"""Modelling quick-wins smoke (slice S3, gate I3.13): registered commands and dialog values, offscreen.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). It builds its own models and walks the
slice claim one clause per step. Where a command has no headless equivalent (Mass properties, Search tools, the
Assembly mate tool) it drives an offscreen MainWindow with every modal call (QMessageBox, BomDialog) stubbed; the
rest drives the feature runners the dialogs call, with the Part Studio dialog values (`dialogs.SCHEMAS`) checked
where a clause is about a dialog field. Steps: Delete part; Move with scale; linear pattern second direction;
two-distance chamfer; extrude up to a vertex; sketch polygon, Extend, radius/diameter toggle, Normal; aluminium in
Mass properties and the bill of materials; suppressed instance in the solve, the bill of materials and the export;
Search tools starting a mate from an Assembly tab.

One line per step, ending `ok`; a failed step prints why and the script exits 1 with no `SQ3 OK`.

    both fs_py scripts/smoke/sq3_modelling.py
"""
import copy
import os
import shutil
import sys
import tempfile
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

TMP = tempfile.mkdtemp(prefix="sq3_modelling_")
os.environ["FREESHAPE_DATA_DIR"] = os.path.join(TMP, "appdata")     # nothing may touch a real user folder
os.environ["XDG_DATA_HOME"] = os.path.join(TMP, "xdg")
os.environ["HOME"] = os.path.join(TMP, "home")
for _d in ("appdata", "xdg", "home"):
    os.makedirs(os.path.join(TMP, _d))

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from conftest import (add, block_doc, edge_refs, line_along, only_part, rect_lines, region_refs,  # noqa: E402
                      sketch_feature)
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import measure as M  # noqa: E402
from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui import assembly_ui, dialogs  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from test_assembly import box, cyl, face, make, mate, src  # noqa: E402
from test_naming_blends import block as chamfer_block  # noqa: E402
from test_naming_blends import set_depth  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402


# ---------------------------------------------------------------------- helpers
class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def near(a, b, tol=1e-6, what=""):
    if abs(a - b) > tol:
        raise Fail(f"{what}: expected {b}, got {a}")


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
            shutil.rmtree(TMP, ignore_errors=True)
            sys.exit(1)
        STEPS.append(name)
        print(f"{name} ... ok", flush=True)
        return fn
    return deco


APP = QApplication.instance() or QApplication([])
S = {}          # state shared between steps


def size(shape):
    lo, hi = K.bbox(shape)
    return np.asarray(hi) - np.asarray(lo)


def window():
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    APP.processEvents()
    return w


def run_command(w, name):
    """Run a Search tools entry by its label, as the palette does."""
    cmd = next((c for c in w._palette_commands() if c.name == name), None)
    check(cmd is not None, f"no Search tools entry {name!r}")
    cmd.action()
    APP.processEvents()


# ---------------------------------------------------------------------- Part Studio
def two_parts():
    """A 40x20x10 block and a 10x10x10 block beside it: (doc, first part id, second part id)."""
    doc = block_doc(40, 20, 10)
    sid = sketch_feature(doc, lambda s: rect_lines(s, 60, 0, 10, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    a, b = list(doc.state.parts)
    return doc, a, b


@step("Delete part removes one of two parts and a later fillet still builds")
def _():
    w = window()
    check("Delete part" in [c.name for c in search_commands(w._palette_commands(), "delete part")],
          "Search tools does not find Delete part")
    check(F.FEATURE_LABELS["delete_part"] == "Delete part" and "delete_part" in F.RUNNERS, "not registered")
    doc, a, b = two_parts()
    add(doc, "delete_part", parts=[b])
    check(list(doc.state.parts) == [a], f"parts after delete: {list(doc.state.parts)}")
    edges = edge_refs(doc.state.parts[a], line_along((0, 0, 1)), expect=4)
    add(doc, "fillet", edges=edges, radius=2.0)
    check(not doc.errors, doc.errors)
    nf = len(K.faces(doc.state.parts[a].shape))
    check(nf == 10, f"filleted block has {nf} faces, expected 10")


@step("Move with scale 2 doubles a part's bounding box")
def _():
    check(any(s["key"] == "scale" for s in dialogs.SCHEMAS["move"]), "Move dialog has no scale field")
    doc = block_doc(10, 20, 30)
    before = size(only_part(doc).shape)
    check(np.allclose(before, (10, 20, 30)), before)
    add(doc, "move", parts=list(doc.state.parts), scale=2.0)
    check(not doc.errors, doc.errors)
    after = size(only_part(doc).shape)
    check(np.allclose(after, 2 * before, atol=1e-6), f"box after scale 2: {after}, expected {2 * before}")


X = {"kind": "std", "name": "X"}
Y = {"kind": "std", "name": "Y"}


def top_names(doc):
    out = []
    for p in doc.state.parts.values():
        nm = doc.state.named(p.id)
        for f in K.faces(p.shape):
            if np.allclose(K.face_info(f).get("n", (9, 9, 9)), (0, 0, 1), atol=1e-6):
                out.append(nm.name_of(f))
    return out


def pattern_doc(**kw):
    doc = block_doc(w=10.0, d=10.0, h=5.0)
    pid = next(iter(doc.state.parts))
    add(doc, "linear_pattern", parts=[pid], direction=X, count=3, spacing=30, op="new", **kw)
    return doc


@step("linear pattern with a second direction is a 3 by 2 grid with unique instance names")
def _():
    doc = pattern_doc(dir2=Y, count2=2, spacing2=40, dir2_on=True)
    check(not doc.errors, doc.errors)
    check(len(doc.state.parts) == 6, f"{len(doc.state.parts)} instances, expected 6")
    centres = set()
    for p in doc.state.parts.values():
        c = K.face_info(K.faces(p.shape)[0])["c"]
        centres.add((round(c[0]), round(c[1])))
    xs, ys = sorted({c[0] for c in centres}), sorted({c[1] for c in centres})
    check(len(centres) == 6 and [x - xs[0] for x in xs] == [0, 30, 60] and [y - ys[0] for y in ys] == [0, 40],
          f"grid centres {sorted(centres)}")
    names = top_names(doc)
    check(len(names) == 6 and len(set(names)) == 6, f"instance names not unique: {names}")


@step("linear pattern in one direction keeps its names")
def _():
    base = pattern_doc()
    one = pattern_doc(dir2=Y, count2=1, spacing2=40, dir2_on=True)       # a second direction of count 1
    check(len(base.state.parts) == 3, len(base.state.parts))
    check(sorted(top_names(base)) == sorted(top_names(one)),
          f"names changed: {sorted(top_names(base))} vs {sorted(top_names(one))}")


def front_top_edge(i):
    return (i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6 and abs(i["c"][1]) < 1e-6)


def setbacks(doc):
    """(setback on the top face, setback on the front face) the chamfer cut back, in mm, for a 10 mm tall block."""
    part = only_part(doc)
    top = front = None
    for f in K.faces(part.shape):
        n = K.face_info(f).get("n")
        if n is None:
            continue
        lo, hi = K.bbox(f)
        if n[2] > 0.999:
            top = lo[1]
        elif n[1] < -0.999:
            front = 10.0 - hi[2]
    return top, front


@step("two-distance chamfer cuts 2 mm on one face and 4 mm on the other")
def _():
    check(any(s["key"] == "distance2" for s in dialogs.SCHEMAS["chamfer"]), "Chamfer dialog has no second distance")
    doc, _ = chamfer_block()
    add(doc, "chamfer", edges=edge_refs(only_part(doc), front_top_edge, expect=1),
        chamfer_type="two", distance=2.0, distance2=4.0)
    check(not doc.errors, doc.errors)
    top, front = setbacks(doc)
    check(abs(top - 2.0) < 1e-6 and abs(front - 4.0) < 1e-6 or abs(top - 4.0) < 1e-6 and abs(front - 2.0) < 1e-6,
          f"setbacks on the two faces: {top}, {front}; expected 2 and 4")
    check(abs(top - front) > 1.0, "both faces cut the same")


def vertex_at_top(doc, a):
    top = max(K.vertex_points(a.shape), key=lambda q: q[2])
    return F.make_vertex_ref(a.id, top, a.shape, doc.state.named(a.id))


@step("extrude up to a vertex ends exactly at it and follows it when the upstream part changes")
def _():
    doc = Document()
    sa = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    fa = add(doc, "extrude", regions=region_refs(doc, sa), depth=17.0, op="new")
    a = next(iter(doc.state.parts.values()))
    sb = sketch_feature(doc, lambda s: rect_lines(s, 50, 0, 10, 10))
    fb = add(doc, "extrude", regions=region_refs(doc, sb), end="upto", op="new", depth=3,
             upto=vertex_at_top(doc, a))
    other = list(doc.state.parts.values())[-1]
    lo, hi = K.bbox(other.shape)
    near(hi[2], 17.0, 1e-6, "top of the part extruded up to the vertex")
    near(lo[2], 0.0, 1e-6, "bottom of that part")
    edited = copy.deepcopy(doc.feature(fa["id"]))
    edited["params"]["depth"] = 22
    doc.replace_feature(edited)
    check(not doc.errors.get(fb["id"]), doc.errors)
    near(K.bbox(list(doc.state.parts.values())[-1].shape)[1][2], 22.0, 1e-6, "top after the upstream part grew")


# ---------------------------------------------------------------------- Sketch
def dist_to_line(sk, a, b, c):
    A, B, C = sk.P(a), sk.P(b), sk.P(c)
    d = B - A
    return abs(d[0] * (C - A)[1] - d[1] * (C - A)[0]) / np.linalg.norm(d)


@step("sketch: a six-sided polygon is fully defined with one dimension")
def _():
    sk = Sketch()
    lines, circ = OPS.polygon(sk, (0, 0), (10, 0), 6, centre_pid="origin")      # centred on the origin
    check(sk.status["ok"] and sk.status["dof"] == 2, sk.status)                 # size and rotation left
    sk.add_constraint("horizontal", lines[0])                                  # a relation, not a dimension
    sk.add_constraint("diameter", circ, value=20)                              # the one dimension
    check(sk.solve(), "sketch did not solve")
    check(sk.status["dof"] == 0, f"degrees of freedom {sk.status['dof']}, expected 0")
    dims = [c for c in sk.constraints if c["type"] in ("diameter", "radius", "distance", "hdistance", "vdistance")]
    check(len(dims) == 1, f"{len(dims)} dimensions, expected 1")
    side = float(np.linalg.norm(sk.P(sk.entities[lines[0]]["p1"]) - sk.P(sk.entities[lines[0]]["p2"])))
    near(side, 10.0, 1e-6, "side of a hexagon of diameter 20")


@step("sketch: Extend ends a line on its neighbour")
def _():
    s = _sketcher()
    sk = s.sk
    a, b = sk.add_point(0, 0), sk.add_point(5, 0)
    h = sk.add_line(a, b)
    c, d = sk.add_point(10, -5), sk.add_point(10, 5)
    v = sk.add_line(c, d)
    s.set_tool("extend")
    n = len(s.undo_stack)
    s.mouse_move(VP, 445, 300, _Ev())
    s.mouse_press(VP, 445, 300, _Ev())                      # near the end point at x = 4.5 mm
    near(sk.P(b)[0], 10.0, 1e-6, "x of the extended end")
    near(sk.P(b)[1], 0.0, 1e-6, "y of the extended end")
    check(any(k["type"] == "point_on" and list(k["refs"]) == [b, v] for k in sk.constraints),
          "extended end is not constrained onto the neighbour")
    check(len(s.undo_stack) == n + 1, "Extend is not one undo step")


@step("sketch: a radius dimension toggled to diameter shows twice the value")
def _():
    sk = Sketch()
    c = sk.add_point(3, 4)
    circ = sk.add_circle(c, 5)
    cid = sk.add_constraint("radius", circ, value=5)["id"]
    sk.solve()
    s = _sketcher(sk)
    check(s.toggle_radius_diameter(cid), "toggle refused")
    k = s._constraint(cid)
    check(k["type"] == "diameter", k["type"])
    near(k["value"], 10.0, 1e-9, "diameter shown")
    near(sk.radius(circ), 5.0, 1e-9, "circle radius after toggle")


@step("sketch: a line made normal to a circle passes through its centre")
def _():
    sk = Sketch()
    a, b = sk.add_point(-20, 0), sk.add_point(20, 5)
    line = sk.add_line(a, b)
    for p in (a, b):
        sk.add_constraint("fix", p, value=list(sk.P(p)))
    c = sk.add_point(3, 12)
    circ = sk.add_circle(c, 4)
    sk.add_constraint("radius", circ, value=4)
    sk.solve()
    check(dist_to_line(sk, a, b, c) > 1.0, "circle starts on the line")
    s = _sketcher(sk)
    check(s._apply_constraint("normal", [circ, line]) is True, "Normal refused")
    near(dist_to_line(sk, a, b, c), 0.0, 1e-6, "distance from the centre to the line")


# ---------------------------------------------------------------------- material, mass, BOM
@step("a part set to aluminium reports aluminium mass in Mass properties")
def _():
    w = window()
    doc = w.doc
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    pid = only_part(doc).id
    doc.set_part_override(pid, material="Aluminium 6061")
    seen = []
    orig = QMessageBox.exec
    QMessageBox.exec = lambda self: seen.append(self.text()) or 0            # the modal box, stubbed
    try:
        w._mass_props(pid)
    finally:
        QMessageBox.exec = orig
    check(len(seen) == 1, f"Mass properties showed {len(seen)} boxes")
    expected = M.mass_g(K.volume(only_part(doc).shape), "Aluminium 6061")
    near(expected, 2.70, 1e-9, "10 mm aluminium cube in g")
    check("Mass (Aluminium 6061, 2.7 g/cm³): 2.7 g<br>" in seen[0], seen[0])
    S["studio"] = doc


def asm_with(studio, copies=1):
    """An Assembly of `copies` instances of the single part of `studio`, its material read from the studio."""
    from freeshape import assembly as A
    a = A.AssemblyDoc()
    a.resolver = lambda s: next(iter(studio.state.parts.values()))
    for k in range(copies):
        T = np.eye(4)
        T[0, 3] = 20.0 * k
        a.insert({"kind": "studio", "uid": "u1", "part": next(iter(studio.state.parts))}, T=T)
    return a


@step("aluminium part reports aluminium mass in the Assembly's Mass properties and the bill of materials")
def _():
    w = window()
    a = asm_with(S["studio"])
    w._add_tab(a)
    (row,) = a.bom()
    check(row["material"] == "Aluminium 6061", row["material"])
    near(row["mass"], 2.70, 1e-9, "BOM mass in g")
    csvs = []
    orig_exec = assembly_ui.BomDialog.exec
    assembly_ui.BomDialog.exec = lambda self: csvs.append(self.csv()) or 0
    try:
        run_command(w, "Bill of materials")
    finally:
        assembly_ui.BomDialog.exec = orig_exec
    check(len(csvs) == 1 and "Aluminium 6061" in csvs[0] and "2.70" in csvs[0], csvs)
    shown = []
    orig_info = QMessageBox.information
    QMessageBox.information = staticmethod(lambda parent, title, text, *x: shown.append(text) or 0)
    try:
        run_command(w, "Mass properties")
    finally:
        QMessageBox.information = orig_info
    check(len(shown) == 1 and "Aluminium 6061: 2.7 g<br>" in shown[0] and "Total: 2.7 g</b>" in shown[0], shown)


# ---------------------------------------------------------------------- suppress
def top(a, i):
    return a.connector(i, "face", face(a, i, lambda f: f.get("n") and f["n"][2] > 0.9))


def bot(a, i):
    return a.connector(i, "face", face(a, i, lambda f: f.get("n") and f["n"][2] < -0.9))


def suppress_scene():
    """plate (fixed), a block and an arm each mated planar to the plate."""
    plate = K.cut(box(60, 40, 10), cyl(4, 12, (15, 20, -1)))
    lib = {"plate": plate, "block": box(20, 10, 8), "arm": K.cut(box(50, 12, 6), cyl(4, 8, (6, 6, -1)))}
    a = make(lib)
    ip, im, ic = a.insert(src("plate")), a.insert(src("block")), a.insert(src("arm"))
    m1 = mate(a, "planar", top(a, ip), bot(a, im))
    m2 = mate(a, "planar", top(a, ip), bot(a, ic))
    a.instance(im)["name"] = "MidBlock"
    return a, ip, im, ic, m1, m2


@step("a suppressed instance drops out of the solve, the bill of materials and the export, then comes back")
def _():
    a, ip, im, ic, m1, m2 = suppress_scene()
    check(sum(r["qty"] for r in a.bom()) == 3, "three instances before")
    full, part, again = (os.path.join(TMP, n) for n in ("full.step", "part.step", "again.step"))
    a.export(full)
    check("MidBlock" in open(full, errors="ignore").read(), "full export lacks MidBlock")
    a.instance(ic)["T"][2][3] += 30                                         # knock the arm off its mate
    a.set_instance_suppressed(im, True)
    check(a.instance(im)["suppressed"] is True, "flag not set")
    check({i["id"] for i in a.visible_instances()} == {ip, ic}, "still visible")
    check(a.mate_inactive(m1) and not a.mate_inactive(m2), "mate activity")
    check(m1 not in a._active_mates() and m2 in a._active_mates(), "suppressed instance's mate is in the solve")
    near(np.array(a.instance(ic)["T"], float)[2, 3], 10.0, 1e-6, "arm height: the other mate solved")
    check(sum(r["qty"] for r in a.bom()) == 2 and all("MidBlock" not in r["instances"] for r in a.bom()),
          "bill of materials still lists it")
    a.export(part)
    check("MidBlock" not in open(part, errors="ignore").read(), "export still contains MidBlock")
    a.set_instance_suppressed(im, False)
    check(not a.instance_suppressed(im), "still suppressed")
    check({i["id"] for i in a.visible_instances()} == {ip, im, ic}, "not visible again")
    check(sum(r["qty"] for r in a.bom()) == 3, "bill of materials did not get it back")
    check(m1 in a._active_mates(), "mate not back in the solve")
    a.export(again)
    check("MidBlock" in open(again, errors="ignore").read(), "export lacks MidBlock after unsuppress")


# ---------------------------------------------------------------------- Search tools
@step("Search tools finds a mate and starts it from an Assembly tab")
def _():
    w = window()
    a = asm_with(S["studio"], copies=2)
    w._add_tab(a)
    hits = [c.name for c in search_commands(w._palette_commands(), "revolute")]
    check(hits[:1] == ["Revolute"], f"Search tools for 'revolute' gave {hits}")
    w.open_command_search()
    check(not w._palette.isHidden(), "Search tools did not open")
    w._palette.close_palette()
    run_command(w, "Revolute")
    check(w.mode == "dialog" and w.asm.mate_dialog.mate["type"] == "revolute",
          f"mate tool not started: mode {w.mode}")
    w.asm.mate_dialog.reject()
    check(w.mode == "idle", f"mode after cancel {w.mode}")


print(f"SQ3 OK: {len(STEPS)} steps")
shutil.rmtree(TMP, ignore_errors=True)
