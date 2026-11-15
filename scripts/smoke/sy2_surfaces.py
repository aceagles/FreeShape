"""Surface bodies and thin features smoke (slice S2): surface and thin Extrude, Revolve and Sweep, the Surfaces group in
the Parts list, Split with a surface, surface parts in patterns, exports and files, in the real main window.

Gate script of the Onshape Parity 2 plan (helpers in topdown_common.py). The window is a MainWindow(engine=True), as the
app starts it: each Part Studio tab rebuilds in a worker and every commit waits (with a timeout) for the worker before
the built state is read. Every feature is made through the dialog the UI opens (start_feature, the dialog's _set and
handle_pick, commit_dialog); every check line carries a mode tag: [engine] ran in that window, [no window] is about
files only.

  1. An open three-segment line extruded as a surface: Parts list "Surfaces (1)" / "Surface 1", area 250, volume 0;
     its mass properties show an area and no volume or mass; patterned three times, still surfaces.
  2. A line revolved and an arc swept as surfaces.
  3. Remove on a surface and an up-to end on a surface are refused, with their texts.
  4. An open L extruded thin (one solid, 840 mm3); a quarter arc extruded thin with Mid plane (the case I2.8 failed on);
     a shallow arc swept thin, Mid plane, along an arc-line-arc path (the fender); a wall thicker than the path's
     tightest bend is refused with its text.
  5. A block and a surface: the Parts list shows Parts and Surfaces; STL leaves the surface out; STL of the surface alone
     is refused; the surface picked as a part splits the block into two solids (volumes sum to the block's).
  6. A surface that stops half way is refused ("must pass completely through"), the block stays whole; picking one of
     its faces instead uses the extended face and splits it.
  7. Save, close and reopen the surface studio: the same parts and BREP hashes. examples/bracket.fshape opened in the
     window has the part hash it had on origin/master.
  8. The window's command search finds Extrude, Revolve, Sweep and Split by their surface and thin phrases.

Not through the real UI: the sketches are added to the Document with the test helpers (not drawn); a pick is a Pick
object handed to the dialog (no screen click); Remove on a surface is set on the open dialog's params, because the
dialog hides that button for a surface; the mass properties box is stubbed (it is modal) and its text read.

    both fs_py scripts/smoke/sy2_surfaces.py
"""
import math
import os
import struct
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy2_surfaces_") if __name__ == "__main__" else None    # a worker imports this file again

from conftest import EXAMPLES_DIR, FRONT, TOP, add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402
from freeshape.ui import panels as P  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

ENGINE, FILES = "engine", "no window"
BEND = "The wall is thicker than the path's tightest bend allows"
PASS_MSG = ("The surface must pass completely through the part - make it larger, or pick one of its faces "
            "to use the extended face")
NO_REMOVE = "Remove and Intersect are not available for surfaces"
UP_TO = "A surface extrude needs a Blind, Symmetric or Through all end"
NOTHING = "Nothing to export: surface bodies are not written"
LENGTH = 10 * math.pi + 20                  # the arc-line-arc path: quarter arc of radius 10, a line of 20, an arc back
# BREP hash of examples/bracket.fshape's one part, built from origin/master at c650fdf (the same on both OCP builds).
BRACKET_HASHES = {"F2/1": "b26d2920374e395928fc731722e3f9a249163712a75ee627979dc07f8810aebb"}


def say(mode, msg):
    T.say(f"ok [{mode}] {msg}")


# ------------------------------------------------------------------------------------------------ sketches
def polyline(s):
    """Three segments in the Top plane, along +x, +y, +x: 10 + 5 + 10 = 25 long."""
    pts = [s.add_point(*q) for q in ((0, 0), (10, 0), (10, 5), (20, 5))]
    for a, b in zip(pts, pts[1:]):
        s.add_line(a, b)


def l_shape(s):
    p = [s.add_point(0, 0), s.add_point(20, 0), s.add_point(20, 20)]
    s.add_line(p[0], p[1])
    s.add_line(p[1], p[2])


def quarter_arc(s):
    s.add_arc(s.add_point(0, 0), s.add_point(10, 0), s.add_point(0, 10))


def line_at_radius(s):
    s.add_line(s.add_point(0, 10), s.add_point(20, 10))


def small_arc(s):
    s.add_arc(s.add_point(0, 0), s.add_point(3, 0), s.add_point(0, 3))


def l_path(s):
    a, b, c = s.add_point(0, 0), s.add_point(0, 40), s.add_point(30, 40)
    s.add_line(a, b)
    s.add_line(b, c)


SHALLOW = (15.0, 4.0)                       # chord 30, rise 4


def arc_geometry():
    half, rise = SHALLOW
    r = (half ** 2 + rise ** 2) / (2 * rise)
    a = math.asin(half / r)
    return r, a, -r * math.sin(a) / a


def shallow_arc(s):
    """An arc along y with chord 30 and rise 4 towards +x, its centroid on the path."""
    r, a, cx = arc_geometry()
    s.add_arc(s.add_point(cx, 0), s.add_point(cx + r * math.cos(a), -r * math.sin(a)),
              s.add_point(cx + r * math.cos(a), r * math.sin(a)))


def tangent_path(s):
    """Arc down about (10, 0), a line of 20, an arc up about (30, 0): tangent throughout, square to the Top plane."""
    p0, p1, p2, p3 = s.add_point(0, 0), s.add_point(10, -10), s.add_point(30, -10), s.add_point(40, 0)
    s.add_arc(s.add_point(10, 0), p0, p1)
    s.add_line(p1, p2)
    s.add_arc(s.add_point(30, 0), p2, p3)


def tight_path(s):
    """A quarter arc of radius 4, then a line: a bend a wall 10 thick cannot follow."""
    p0, p1, p2 = s.add_point(0, 0), s.add_point(4, -4), s.add_point(14, -4)
    s.add_arc(s.add_point(4, 0), p0, p1)
    s.add_line(p1, p2)


def s_curve(s):
    s.add_spline([s.add_point(*q) for q in ((20, -5), (15, 5), (25, 15), (20, 25))])


def wall_line(s):
    s.add_line(s.add_point(20, -5), s.add_point(20, 25))


def ents(doc, sid, kind):
    return [i for i, e in doc.state.sketches[sid].sketch.entities.items() if e["type"] == kind]


# ------------------------------------------------------------------------------------------------ the window
def settle(w):
    T.settle(w, 180)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")


def open_studio(w, build):
    """A new Part Studio tab. `build(doc)` adds the sketches and features; the tab is shown and settled."""
    doc = PartStudioDoc()
    build(doc)
    T.check(not doc.errors, doc.errors)
    w._add_tab(doc)
    settle(w)
    T.check(w.doc is doc, "the Part Studio tab did not open")
    return doc


def block(doc, x0=0.0, y0=0.0, w=40.0, d=20.0, h=10.0):
    sid = sketch_feature(doc, lambda s: rect_lines(s, x0, y0, w, d), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=h, op="new")


def feature(w, ftype, **values):
    """Open the tool, set its rows in the order given ("pick" and "activate" hand a Pick to the dialog or choose
    its field). Returns (dialog, feature); `commit` finishes it."""
    w.start_feature(ftype)
    dlg = w.dialog
    T.check(dlg is not None and dlg.ftype == ftype, f"the {ftype} tool did not open")
    for k, v in values.items():
        if k == "pick":
            dlg.handle_pick(v)
        elif k == "activate":
            dlg.activate(dlg.fields[v])
        else:
            dlg._set(k, v)
            if k == "creation" and v != "solid":
                app_events()
                dlg.activate(dlg.fields["curves"])         # the picks that follow are curves
    return dlg, dlg.feature


def app_events():
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def commit(w, dlg, f, error=None, what=""):
    T.commit_open_dialog(w)
    settle(w)
    got = w.doc.errors.get(f["id"])
    if got and got != error:
        got = f"{got} (params {f['params']})"
    if error is None:
        T.check(not got, f"{what}: {got}")
    else:
        T.check(got == error, f"{what}: expected the error {error!r}, got {got!r}")
    return f


def pick_line(doc, sid, kind="line"):
    return Pick("sketch_line", (sid, ents(doc, sid, kind)[0]))


def rows(w):
    lst = w.left.parts
    out = []
    for i in range(lst.count()):
        it = lst.item(i)
        out.append((it.data(P.ROLE_KIND), it.text()))
    return out


def surfaces(doc):
    return [p for p in doc.state.parts.values() if K.is_surface(p.shape)]


def solids(doc):
    return [p for p in doc.state.parts.values() if not K.is_surface(p.shape)]


def area(shape):
    return K.mass_props(shape)["area"]


def near(a, b, rel=1e-4):
    return abs(a - b) <= rel * max(1.0, abs(b))


def hashes(doc):
    return {p.name: Encoder(meshes=False).brep(p.shape)[1] for p in doc.state.parts.values()}


def drop(w, f):
    """Remove a feature that failed on purpose, and wait."""
    w.doc.delete_features([f["id"]])
    settle(w)


# ------------------------------------------------------------------------------------------------ the checks
def surface_extrude_and_patterns(app, w):
    holder = {}

    def build(doc):
        holder["sid"] = sketch_feature(doc, polyline, TOP)
    doc = open_studio(w, build)
    sid = holder["sid"]
    dlg, f = feature(w, "extrude", creation="surface", op="new", pick=pick_line(doc, sid), end="blind", depth=10)
    commit(w, dlg, f, what="surface extrude")
    (p,) = doc.state.parts.values()
    T.check(p.name == "Surface 1", f"the surface part is named {p.name!r}")
    T.check(K.is_surface(p.shape) and not K.solids(p.shape), "the extrude is not a surface body")
    T.check(near(area(p.shape), 250.0), f"surface area {area(p.shape)}, wanted 250")
    T.check(K.volume(p.shape) == 0.0, f"surface volume {K.volume(p.shape)}")
    T.check([r[1] for r in rows(w)] == ["Surfaces (1)", "Surface 1"], f"the Parts list shows {rows(w)}")
    T.check(rows(w)[0][0] == "header", f"the Surfaces row is not a header: {rows(w)}")
    say(ENGINE, "open three-segment line extruded as a surface: listed under Surfaces (1) as Surface 1, "
                "area 250 mm2, volume 0")
    surf = p.id

    # ---- mass properties show an area and no volume or mass
    from PySide6.QtWidgets import QMessageBox
    shown = []
    real = QMessageBox.exec
    QMessageBox.exec = lambda self: shown.append(self.text()) or 0
    try:
        w._mass_props(surf)
    finally:
        QMessageBox.exec = real
    T.check(len(shown) == 1, f"mass properties showed {len(shown)} boxes")
    text = shown[0]
    T.check("Surface 1" in text and "Surface area: 250" in text, text)
    T.check("Volume" not in text and "Mass" not in text and "g/cm" not in text, text)
    say(ENGINE, "mass properties of a surface part: an area (250 mm2), no volume, no mass")

    # ---- a linear pattern of the surface part
    dlg, f = feature(w, "linear_pattern", op="new", dir_std="X", spacing=40.0, count=3)
    dlg.activate(dlg.fields["parts"])
    dlg.handle_pick(Pick("part", (surf,)))
    commit(w, dlg, f, what="linear pattern of a surface")
    surfs = surfaces(doc)
    T.check(len(surfs) == 3 and len(doc.state.parts) == 3, f"the pattern left {[p.name for p in doc.state.parts.values()]}")
    xs = sorted(round(float(K.bbox(q.shape)[0][0]), 4) for q in surfs)
    T.check(xs == [0.0, 40.0, 80.0], f"pattern copies start at x = {xs}")
    T.check(rows(w)[0] == ("header", "Surfaces (3)") and len(rows(w)) == 4, f"the Parts list shows {rows(w)}")
    say(ENGINE, "linear pattern of a surface part: three surface parts, 40 mm apart, listed under Surfaces (3)")

    # ---- only surfaces: nothing to export as a mesh
    stl = os.path.join(TMP, "only.stl")
    try:
        doc.export(stl)
    except K.KernelError as e:
        T.check(str(e).startswith(NOTHING), f"export of a studio of surfaces said {e!s}")
    else:
        raise T.Fail("exporting a studio of only surfaces to STL did not raise")
    say(ENGINE, "STL of a studio of only surfaces: refused with 'Nothing to export: surface bodies are not written'")
    return doc


def reopen_surface_studio(app, w, doc):
    path = os.path.join(TMP, "surfaces.fshape")
    before = hashes(doc)
    names = sorted(before)
    doc.save(path)
    T.check(os.path.exists(path), "the studio was not saved")
    T.check(not doc.dirty, "the saved studio is still marked changed (closing it would ask)")
    w.close_tab(w._tab_index(doc))
    settle(w)
    w.open_any(path)
    settle(w)
    back = w.doc
    T.check(back is not doc and back.path == path and not back.errors, f"the reopened studio: {back.errors}")
    T.check(sorted(p.name for p in back.state.parts.values()) == names,
            f"the reopened parts {sorted(p.name for p in back.state.parts.values())}, saved {names}")
    after = hashes(back)
    T.check(after == before, f"reopened BREP hashes {after}, saved {before}")
    T.check(len(surfaces(back)) == 3, "the reopened parts are not all surfaces")
    T.check(rows(w)[0] == ("header", "Surfaces (3)"), f"the reopened Parts list shows {rows(w)}")
    say(ENGINE, f"save, reopen, compare: {len(after)} surface parts with the same names and BREP hashes")


def example_hash(app, w):
    """Built by the kernel directly, as sy1_quickwins does: a part the window decoded from its worker's answer does not
    re-encode to the same bytes (the window shows the same one part; its volume is compared instead)."""
    path = os.path.join(EXAMPLES_DIR, "bracket.fshape")
    ex = PartStudioDoc.load(path)
    ex.rebuild()
    T.check(not ex.errors, ex.errors)
    got = hashes_by_id(ex)
    T.check(got == BRACKET_HASHES, f"bracket.fshape part hashes {got}, on origin/master {BRACKET_HASHES}")
    say(FILES, "example bracket.fshape (version 1): part hash equals the one from origin/master")
    w.open_any(path)
    settle(w)
    T.check(not w.doc.errors, w.doc.errors)
    (shown,), (built,) = w.doc.state.parts.values(), ex.state.parts.values()
    T.check(near(K.volume(shown.shape), K.volume(built.shape), 1e-9) and
            [r[1] for r in rows(w)] == ["Parts (1)", "Bracket"], f"the window shows {rows(w)}, volume {K.volume(shown.shape)}")
    say(ENGINE, f"example bracket.fshape opened in the window: one part, volume {K.volume(shown.shape):.1f} mm3 as built")


def hashes_by_id(doc):
    return {pid: Encoder(meshes=False).brep(p.shape)[1] for pid, p in doc.state.parts.items()}


def revolve_and_sweep(app, w):
    holder = {}

    def build(doc):
        holder["rev"] = sketch_feature(doc, line_at_radius, TOP)
        holder["prof"] = sketch_feature(doc, small_arc, TOP)
        holder["path"] = sketch_feature(doc, l_path, FRONT)
    doc = open_studio(w, build)
    dlg, f = feature(w, "revolve", creation="surface", op="new", pick=pick_line(doc, holder["rev"]),
                     axis_std="X", end="one", angle=180)
    commit(w, dlg, f, what="surface revolve")
    (p,) = doc.state.parts.values()
    T.check(p.name == "Surface 1" and K.is_surface(p.shape), f"the revolve made {p.name!r}")
    T.check(near(area(p.shape), math.pi * 10 * 20), f"revolved area {area(p.shape)}, wanted {math.pi * 200:.3f}")
    say(ENGINE, f"line revolved 180 degrees as a surface: half a cylinder, area {area(p.shape):.1f} mm2")
    dlg, f = feature(w, "sweep", creation="surface", op="new", pick=pick_line(doc, holder["prof"], "arc"))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(pick_line(doc, holder["path"]))
    commit(w, dlg, f, what="surface sweep")
    T.check([q.name for q in doc.state.parts.values()] == ["Surface 1", "Surface 2"], list(doc.state.parts))
    sw = list(doc.state.parts.values())[1]
    T.check(K.is_surface(sw.shape) and len(K.faces(sw.shape)) == 2, "the swept arc is not a two-face surface")
    T.check(rows(w)[0] == ("header", "Surfaces (2)"), f"the Parts list shows {rows(w)}")
    say(ENGINE, "arc swept along an L path as a surface: Surface 2, two faces, listed under Surfaces (2)")


def refusals(app, w):
    holder = {}

    def build(doc):
        holder["sid"] = sketch_feature(doc, polyline, TOP)
    doc = open_studio(w, build)
    sid = holder["sid"]
    dlg, f = feature(w, "extrude", creation="surface", op="new", pick=pick_line(doc, sid), depth=10)
    from PySide6.QtWidgets import QToolButton
    ops = [b.text() for spec, wd in dlg.rows if spec["t"] == "ops" and spec["key"] == "op" and wd.isVisibleTo(dlg)
           for b in wd.findChildren(QToolButton)]
    T.check(ops == ["New", "Add"], f"the Operation row of a surface extrude offers {ops}")
    dlg.feature["params"]["op"] = "remove"           # the dialog hides the button; the feature must still refuse
    commit(w, dlg, f, error=NO_REMOVE, what="Remove with a surface")
    drop(w, f)
    say(ENGINE, f"Remove with a surface: dialog offers only New and Add; forced, refused with '{NO_REMOVE}'")
    dlg, f = feature(w, "extrude", creation="surface", op="new", pick=pick_line(doc, sid), end="up_to_next", depth=10)
    commit(w, dlg, f, error=UP_TO, what="up-to end on a surface")
    drop(w, f)
    T.check(not doc.state.parts, f"a refused extrude left parts {list(doc.state.parts)}")
    say(ENGINE, f"up-to end on a surface extrude: refused, naming the ends it allows ('{UP_TO}')")


def thin_features(app, w):
    holder = {}

    def build(doc):
        holder["l"] = sketch_feature(doc, l_shape, TOP)
        holder["arc"] = sketch_feature(doc, quarter_arc, TOP)
        holder["prof"] = sketch_feature(doc, shallow_arc, TOP)
        holder["path"] = sketch_feature(doc, tangent_path, FRONT)
        holder["line"] = sketch_feature(doc, lambda s: s.add_line(s.add_point(0, 0), s.add_point(0, 2)), TOP)
        holder["tight"] = sketch_feature(doc, tight_path, FRONT)
    doc = open_studio(w, build)
    dlg, f = feature(w, "extrude", creation="thin", op="new", pick=pick_line(doc, holder["l"]),
                     thickness1=2, thickness2=0, depth=10)
    commit(w, dlg, f, what="thin extrude of an L")
    p = list(doc.state.parts.values())[-1]
    T.check(len(K.solids(p.shape)) == 1 and not K.is_surface(p.shape), "the thin extrude is not one solid")
    T.check(near(K.volume(p.shape), 840.0), f"thin L volume {K.volume(p.shape)}, wanted 840")
    say(ENGINE, f"open L extruded thin (2 mm wall, outside of the corner): one solid, {K.volume(p.shape):.0f} mm3")

    # ---- extra check 1: Mid plane on an arc profile (I2.8 failed here: "Path edges must connect end to end")
    dlg, f = feature(w, "extrude", creation="thin", op="new", pick=pick_line(doc, holder["arc"], "arc"),
                     mid_plane=True, thickness1=2, depth=10)
    commit(w, dlg, f, what="thin extrude Mid plane on an arc")
    p = list(doc.state.parts.values())[-1]
    want = 10 * math.pi * 10                      # quarter annulus of radii 9 and 11, 10 deep
    T.check(len(K.solids(p.shape)) == 1 and near(K.volume(p.shape), want, 1e-3),
            f"arc thin Mid plane volume {K.volume(p.shape)}, wanted {want:.3f}")
    say(ENGINE, f"thin extrude with Mid plane on an ARC profile: one solid, {K.volume(p.shape):.2f} mm3 "
                f"(a quarter ring 2 mm thick, 10 mm deep)")

    # ---- the fender: a shallow arc swept thin along an arc-line-arc path
    dlg, f = feature(w, "sweep", creation="thin", op="new", pick=pick_line(doc, holder["prof"], "arc"))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(pick_line(doc, holder["path"]))
    dlg._set("mid_plane", True)
    dlg._set("thickness1", 2)
    commit(w, dlg, f, what="thin sweep along arc-line-arc")
    p = list(doc.state.parts.values())[-1]
    r, a, _ = arc_geometry()
    want = 2 * r * a * 2 * LENGTH
    T.check(len(K.solids(p.shape)) == 1 and abs(K.volume(p.shape) - want) < 0.03 * want,
            f"thin sweep volume {K.volume(p.shape):.1f}, wanted {want:.1f}")
    say(ENGINE, f"fender: shallow arc swept thin (Mid plane, 2 mm) along an arc-line-arc path: one solid, "
                f"{K.volume(p.shape):.0f} mm3 (wanted {want:.0f} within 3%)")
    n = len(doc.state.parts)

    # ---- extra check 3: a wall thicker than the path's tightest bend allows
    dlg, f = feature(w, "sweep", creation="thin", op="new", pick=pick_line(doc, holder["line"]))
    dlg.activate(dlg.fields["path"])
    dlg.handle_pick(pick_line(doc, holder["tight"]))
    dlg._set("thickness1", 10)
    dlg._set("thickness2", 0)
    commit(w, dlg, f, error=BEND, what="thin sweep thicker than the bend")
    drop(w, f)
    T.check(len(doc.state.parts) == n, "the refused thin sweep left a part")
    say(ENGINE, f"thin sweep with a 10 mm wall round a 4 mm bend: refused, '{BEND}'")


def block_and_surface(app, w):
    holder = {}

    def build(doc):
        block(doc)
        holder["sid"] = sketch_feature(doc, s_curve, TOP)
    doc = open_studio(w, build)
    dlg, f = feature(w, "extrude", creation="surface", op="new", pick=pick_line(doc, holder["sid"], "spline"),
                     end="symmetric", depth=30)
    commit(w, dlg, f, what="S-shaped surface")
    (surf,), (solid,) = surfaces(doc), solids(doc)
    T.check([r[1] for r in rows(w)] == ["Parts (1)", "Part 1", "Surfaces (1)", "Surface 1"], f"the Parts list shows {rows(w)}")
    T.check(rows(w)[0][0] == "header" and rows(w)[2][0] == "header", rows(w))
    say(ENGINE, "a block and an S-shaped surface: the Parts list shows Parts (1) then Surfaces (1)")

    # ---- STL leaves the surface out; STL of the surface alone is refused
    stl = os.path.join(TMP, "mixed.stl")
    doc.export(stl)
    T.check(doc.last_export_skipped == ["Surface 1"], f"the export skipped {doc.last_export_skipped}")
    with open(stl, "rb") as fh:
        tris = struct.unpack("<I", fh.read(84)[80:84])[0]
    T.check(tris == 12, f"the STL has {tris} triangles, a block has 12")
    try:
        doc.export(os.path.join(TMP, "surface.stl"), part_ids=[surf.id])
    except K.KernelError as e:
        T.check(str(e).startswith(NOTHING), str(e))
    else:
        raise T.Fail("STL of a surface part alone did not raise")
    say(ENGINE, "STL export of a block and a surface: 12 triangles, the surface left out (skipped: Surface 1); "
                "the surface alone is refused")

    # ---- the surface part as the tool: two solids, the surface goes
    before = K.volume(solid.shape)
    dlg, f = feature(w, "split")
    dlg.activate(dlg.fields["parts"])
    dlg.handle_pick(Pick("part", (solid.id,)))
    dlg.activate(dlg.fields["tools"])
    dlg.handle_pick(Pick("part", (surf.id,)))
    T.check(dlg.feature["params"]["tools"][0]["kind"] == "surface", dlg.feature["params"]["tools"])
    commit(w, dlg, f, what="split with a surface")
    two = solids(doc)
    T.check(len(two) == 2 and not surfaces(doc), f"the split left {[p.name for p in doc.state.parts.values()]}")
    vols = sorted(K.volume(p.shape) for p in two)
    T.check(near(sum(vols), before, 1e-6) and vols[0] > 100.0, f"split volumes {vols}, block {before}")
    T.check([r[1] for r in rows(w)][0] == "Parts (2)", f"the Parts list shows {rows(w)}")
    say(ENGINE, f"block split by an S-shaped surface: two solids ({vols[0]:.0f} and {vols[1]:.0f} mm3, "
                f"sum {sum(vols):.0f}), the surface used up")


def short_surface(app, w):
    holder = {}

    def build(doc):
        block(doc)
        holder["sid"] = sketch_feature(doc, wall_line, TOP)
    doc = open_studio(w, build)
    dlg, f = feature(w, "extrude", creation="surface", op="new", pick=pick_line(doc, holder["sid"]),
                     end="blind", depth=5)
    commit(w, dlg, f, what="short surface")
    (surf,), (solid,) = surfaces(doc), solids(doc)
    before = K.volume(solid.shape)
    dlg, f = feature(w, "split")
    dlg.activate(dlg.fields["parts"])
    dlg.handle_pick(Pick("part", (solid.id,)))
    dlg.activate(dlg.fields["tools"])
    dlg.handle_pick(Pick("part", (surf.id,)))
    commit(w, dlg, f, error=PASS_MSG, what="surface that stops half way")
    T.check(len(solids(doc)) == 1 and len(surfaces(doc)) == 1, f"the refused split left {list(doc.state.parts)}")
    T.check(near(K.volume(solids(doc)[0].shape), before, 1e-9), "the refused split changed the block")
    drop(w, f)
    say(ENGINE, "a surface that stops half way (5 of 10 mm): refused, 'must pass completely through the part', "
                "block whole")

    # ---- the same surface picked by one of its faces: the extended face cuts
    dlg, f = feature(w, "split")
    dlg.activate(dlg.fields["parts"])
    dlg.handle_pick(Pick("part", (solid.id,)))
    dlg.activate(dlg.fields["tools"])
    dlg.handle_pick(Pick("face", (surf.id, 0)))
    T.check(dlg.feature["params"]["tools"][0]["kind"] == "face", dlg.feature["params"]["tools"])
    commit(w, dlg, f, what="split with one face of the short surface")
    T.check(len(solids(doc)) == 2, f"the extended face made {len(solids(doc))} solids")
    say(ENGINE, "the same short surface picked by one of its faces: a face tool, extended, splits the block in two")


def command_search(app, w):
    from freeshape.ui.palette import search_commands
    open_studio(w, lambda doc: None)
    cmds = w._palette_commands()
    for text, name in (("surface extrude", "Extrude"), ("thin extrude", "Extrude"), ("surface revolve", "Revolve"),
                       ("thin revolve", "Revolve"), ("surface sweep", "Sweep"), ("thin sweep", "Sweep"),
                       ("split with surface", "Split")):
        found = [c.name for c in search_commands(cmds, text)]
        T.check(name in found, f"the command search for {text!r} finds {found[:6]}, not {name}")
        say(ENGINE, f"command search {text!r} finds {name}")


def run():
    app, w = T.new_engine_window(TMP)
    try:
        w.resize(1200, 800)
        w.show()
        app.processEvents()
        T.check(w.studio_engine is not None, "the window is not in engine mode")
        settle(w)
        doc = surface_extrude_and_patterns(app, w)
        reopen_surface_studio(app, w, doc)
        revolve_and_sweep(app, w)
        refusals(app, w)
        thin_features(app, w)
        block_and_surface(app, w)
        short_surface(app, w)
        example_hash(app, w)
        command_search(app, w)
    finally:
        T.close_engine_window(w)


if __name__ == "__main__":                      # the engine workers import this file: only the main process runs it
    T.run_smoke("SURFACES SMOKE", TMP, run)
