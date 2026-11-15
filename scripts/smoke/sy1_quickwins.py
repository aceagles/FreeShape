"""Modelling quick wins smoke (slice S1): axis from a round face or a sketch circle, a plane on a sketch curve,
Extrude draft, the Slot tool on a chain, Rib and file version 5, in the real main window.

Gate script of the Onshape Parity 2 plan (helpers in topdown_common.py). The window is a MainWindow(engine=True), as the app starts it: Part Studio tabs rebuild in a worker, and every
commit waits (with a timeout) for the worker before the built state is read. A Part Studio holds a 60 x 40 x 10 block with
a through hole, a small box beside the hole, a cylinder standing on its own and an L bracket's worth of sketches. In
the offscreen window each feature is made through the dialog or the sketcher the UI opens (start_feature, the
dialog's handle_pick and _set, commit_dialog, Sketcher.mouse_press and the slot's typed size):

  * a circular pattern of the small box about the hole's cylinder face;
  * a revolve about the flat round end face of a cylinder, and one about a sketch circle;
  * a plane on a sketch arc at a sketch point, and a sketch drawn on it;
  * an extrude of a square with a 5 degree draft, and again Symmetric;
  * the Slot tool on an arc - line - arc chain, and the one region it makes extruded;
  * a Rib in the corner of an L bracket;
  * a version 4 Part Studio file loaded and saved again as version 5, its other keys unchanged, and
    examples/bracket.fshape (version 1) built to the part hash it had on origin/master.

Not through the real UI: the Document is built with the test helpers (not drawn); a pick is a Pick object handed to
the dialog (no screen click); the chain for the Slot tool is added to the sketch with Sketch.add_* before the slot is
drawn with real mouse events.

    both fs_py scripts/smoke/sy1_quickwins.py
"""
import json
import math
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy1_quickwins_") if __name__ == "__main__" else None   # a worker imports this file again

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QLineEdit  # noqa: E402
from conftest import (EXAMPLES_DIR, FRONT, TOP, add, rect_lines, region_refs, sketch_feature,  # noqa: E402
                      v4_studio_json)
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document, PartStudioDoc  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_rib import bracket, line, rib_sketch  # noqa: E402

# BREP hash of examples/bracket.fshape's one part, built from origin/master at c650fdf (the same on both OCP builds).
BRACKET_HASHES = {"F2/1": "b26d2920374e395928fc731722e3f9a249163712a75ee627979dc07f8810aebb"}

T5 = math.tan(math.radians(5))


class Ev:
    """The mouse event the sketcher reads: no modifier keys."""

    def modifiers(self):
        return Qt.NoModifier


def hashes(doc):
    return {pid: Encoder(meshes=False).brep(p.shape)[1] for pid, p in doc.state.parts.items()}


def parts_by_volume(doc):
    """The parts of a Part Studio, as {rounded volume: part}."""
    return {round(K.volume(p.shape), 3): p for p in doc.state.parts.values()}


def face_index(shape, pred):
    return next(i for i, f in enumerate(K.faces(shape)) if pred(K.face_info(f)))


def is_cyl(i):
    return i["type"] == int(K.GeomAbs_Cylinder)


def last_feature(doc, ftype):
    f = [f for f in doc.features if f["type"] == ftype][-1]
    T.check(doc.errors.get(f["id"]) is None, f"{ftype}: {doc.errors.get(f['id'])}")
    return f


def rot_bbox(lo, hi, o, d, deg):
    """The bounding box of the box lo..hi turned `deg` degrees about the line through o along d."""
    a = np.radians(deg)
    pts = []
    for x in (lo[0], hi[0]):
        for y in (lo[1], hi[1]):
            for z in (lo[2], hi[2]):
                v = np.array([x, y, z], float) - o
                v = v * np.cos(a) + np.cross(d, v) * np.sin(a) + d * (d @ v) * (1 - np.cos(a))
                pts.append(v + o)
    pts = np.array(pts)
    return np.concatenate([pts.min(axis=0), pts.max(axis=0)])


def box_of(shape):
    lo, hi = K.bbox(shape)
    return np.concatenate([np.asarray(lo, float), np.asarray(hi, float)])


def faces_at(shape, z):
    return [f for f in K.faces(shape) if K.face_info(f)["type"] == int(K.GeomAbs_Plane)
            and abs(K.face_info(f)["c"][2] - z) < 1e-6]


def side(face):
    lo, hi = K.bbox(face)
    return (np.asarray(hi) - np.asarray(lo))[:2]


def extrude_square(w, doc, sid, **values):
    """The Extrude tool on the one region of sketch `sid`, as a new part; `values` set the dialog's fields."""
    before = set(doc.state.parts)
    w.start_feature("extrude")
    T.check(w.dialog.ftype == "extrude", "the Extrude tool did not open")
    dlg = w.dialog
    dlg._set("op", "new")
    dlg.fields["profiles"].toggle(("region", region_refs(doc, sid)[0]))
    for k, v in values.items():
        dlg._set(k, v)
    commit(w)
    last_feature(doc, "extrude")
    new = set(doc.state.parts) - before
    T.check(len(new) == 1, f"the extrude made {len(new)} parts, not 1")
    return doc.state.parts[next(iter(new))]


def commit(w):
    """Commit the open dialog and wait (bounded) until the engine worker has answered and the window shows its result."""
    T.commit_open_dialog(w)
    T.settle(w, 120)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")


def finish(w):
    """Finish the open sketch and wait for the engine."""
    w.finish_sketch()
    T.settle(w, 120)


def sketching(w, plane):
    """Start a sketch on `plane` in the window and return its sketcher."""
    w.start_sketch()
    T.check(w.mode == "sketch", "the sketch did not start")
    w.sk_ctx["feature"]["params"]["plane"] = plane
    w._enter_sketcher(animate=False)
    return w.sketcher


def click(s, vp, u, v):
    x, y = s.scr(vp, (u, v))
    s.mouse_move(vp, x, y, Ev())
    s.mouse_press(vp, x, y, Ev())


def type_size(s, text):
    ed = s._editor
    T.check(isinstance(ed, QLineEdit), "the tool opened no size box")
    ed.clear()
    QTest.keyClicks(ed, text)
    QTest.keyClick(ed, Qt.Key_Return)
    T.check(s._editor is None, "the size box stayed open")


def run():
    app, w = T.new_engine_window(TMP)
    try:
        body(app, w)
    finally:
        T.close_engine_window(w)


def body(app, w):

    # ---- the Document: a block with a through hole, a small box, a pin (cylinder), three circle sketches, an arc
    doc = Document()
    sb = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 60, 40), TOP)
    add(doc, "extrude", regions=region_refs(doc, sb), depth=10, op="new")
    hole = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(45, 10)), TOP)
    add(doc, "hole", sketch=hole, standard="custom", diameter=6.0, end="through", style="simple")
    small = sketch_feature(doc, lambda s: rect_lines(s, 10, 10, 6, 6), TOP)
    add(doc, "extrude", regions=region_refs(doc, small), depth=4, op="new")
    pin_sk = sketch_feature(doc, lambda s: s.add_circle(s.add_point(-30, 0), 5), TOP)
    add(doc, "extrude", regions=region_refs(doc, pin_sk), depth=10, op="new")
    rev1 = sketch_feature(doc, lambda s: s.add_circle(s.add_point(-10, 5), 2), FRONT)   # profile for the end-face axis
    ids = {}

    def two_circles(s):
        ids["axis_circle"] = s.add_circle(s.add_point(80, 0), 4)
    axis_sk = sketch_feature(doc, two_circles, FRONT)                                   # the axis: normal to Front
    rev2 = sketch_feature(doc, lambda s: s.add_circle(s.add_point(100, 5), 2), TOP)     # profile, 20 from that axis
    arc_ids = {}

    def arc(s):
        c, a, m, b = s.add_point(0, 60), s.add_point(10, 60), s.add_point(10 / math.sqrt(2), 60 + 10 / math.sqrt(2)), \
            s.add_point(0, 70)
        arc_ids["arc"] = s.add_arc(c, a, b)
        arc_ids["pt"] = s.add_point_entity(m)
    arc_sk = sketch_feature(doc, arc, TOP)
    sq1 = sketch_feature(doc, lambda s: rect_lines(s, 100, 100, 20, 20), TOP)         # for the draft extrudes
    sq2 = sketch_feature(doc, lambda s: rect_lines(s, 140, 100, 20, 20), TOP)
    T.check(not doc.errors, doc.errors)
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    T.settle(w, 120)
    T.check(w.doc is doc, "the Part Studio tab did not open")
    by_vol = parts_by_volume(doc)
    block = max(doc.state.parts.values(), key=lambda p: K.volume(p.shape))
    box = next(p for p in doc.state.parts.values() if abs(K.volume(p.shape) - 6 * 6 * 4) < 1e-3)
    pin = next(p for p in doc.state.parts.values() if abs(K.volume(p.shape) - math.pi * 25 * 10) < 1e-3)
    T.check(len(doc.state.parts) == 3 and block is not box, f"expected block, box and pin, got {sorted(by_vol)}")

    # ---- circular pattern of the small box about the hole's cylinder face (I1.2)
    n0 = len(doc.state.parts)
    w.start_feature("circular_pattern")
    dlg = w.dialog
    dlg.fields["parts"].toggle(("part", box.id))
    fld = dlg.fields["axis_sel"]
    dlg.activate(fld)
    dlg.handle_pick(Pick("face", (block.id, face_index(block.shape, is_cyl))))
    T.check(len(fld.items) == 1 and fld.items[0][1]["kind"] == "face", f"the cylinder face was stored as {fld.items}")
    dlg._set("count", 3)
    dlg._set("angle", 360.0)
    dlg._set("op", "new")
    commit(w)
    last_feature(doc, "circular_pattern")
    T.check(len(doc.state.parts) == n0 + 2, f"the pattern added {len(doc.state.parts) - n0} parts, not 2")
    lo, hi = K.bbox(box.shape)
    boxes = [box_of(p.shape) for p in doc.state.parts.values() if p.id not in (block.id, box.id, pin.id)]
    for deg in (120, 240):
        want = rot_bbox(lo, hi, np.array([45.0, 10.0, 0.0]), np.array([0.0, 0.0, 1.0]), deg)
        T.check(any(np.allclose(b, want, atol=1e-4) for b in boxes),
                f"circular pattern about the hole face: no copy turned {deg} degrees about (45, 10): {boxes}")
    T.say("[engine] circular pattern about a hole's cylinder face: copies at 120 and 240 degrees about its axis")

    # ---- revolve about the flat round end face of a cylinder (I1.2)
    n0 = len(doc.state.parts)
    w.start_feature("revolve")
    dlg = w.dialog
    dlg._set("op", "new")
    dlg.fields["profiles"].toggle(("region", region_refs(doc, rev1)[0]))
    fld = dlg.fields["axis_sel"]
    dlg.activate(fld)
    top = face_index(pin.shape, lambda i: i["type"] == int(K.GeomAbs_Plane) and abs(i["c"][2] - 10) < 1e-6)
    dlg.handle_pick(Pick("face", (pin.id, top)))
    T.check(len(fld.items) == 1 and fld.items[0][1]["kind"] == "face", f"the end face was stored as {fld.items}")
    commit(w)
    last_feature(doc, "revolve")
    new = [p for i, p in doc.state.parts.items() if i not in (block.id, box.id, pin.id)][-1]
    T.check(len(doc.state.parts) == n0 + 1, "the revolve about the end face made no part")
    want = 2 * math.pi * 20 * math.pi * 4
    T.check(abs(K.volume(new.shape) - want) < 1e-4 * want,
            f"revolve about the end face: volume {K.volume(new.shape):.1f}, wanted {want:.1f}")
    T.say(f"[engine] revolve about a cylinder's flat end face: a torus of mean radius 20 mm, volume {K.volume(new.shape):.0f} mm3")

    # ---- revolve about a sketch circle (I1.2)
    n0 = len(doc.state.parts)
    w.start_feature("revolve")
    dlg = w.dialog
    dlg._set("op", "new")
    dlg.fields["profiles"].toggle(("region", region_refs(doc, rev2)[0]))
    fld = dlg.fields["axis_sel"]
    dlg.activate(fld)
    dlg.handle_pick(Pick("sketch_line", (axis_sk, ids["axis_circle"])))
    T.check(fld.items == [("axis", {"kind": "sketch_circle", "sketch": axis_sk, "entity": ids["axis_circle"]})],
            f"the sketch circle was stored as {fld.items}")
    commit(w)
    last_feature(doc, "revolve")
    T.check(len(doc.state.parts) == n0 + 1, "the revolve about the sketch circle made no part")
    ring = list(doc.state.parts.values())[-1]
    want = 2 * math.pi * 20 * math.pi * 4
    T.check(abs(K.volume(ring.shape) - want) < 1e-4 * want,
            f"revolve about the sketch circle: volume {K.volume(ring.shape):.1f}, wanted {want:.1f}")
    T.say(f"[engine] revolve about a sketch circle: a torus of mean radius 20 mm, volume {K.volume(ring.shape):.0f} mm3")

    # ---- a plane on a sketch arc at a sketch point, and a sketch on it (I1.3)
    w.start_feature("plane")
    T.check(w.dialog.ftype == "plane", "the Plane tool did not open")
    dlg = w.dialog
    dlg.handle_pick(Pick("sketch_line", (arc_sk, arc_ids["arc"])))
    dlg.handle_pick(Pick("sketch_point", (arc_sk, arc_ids["pt"])))
    got = F.plane_type(dlg.feature["params"])
    T.check(got == "curve_point", f"the picks were read as plane type {got!r}, not 'curve_point'")
    commit(w)
    pf = last_feature(doc, "plane")
    pl = doc.state.planes[pf["id"]]
    c45 = 10 / math.sqrt(2)
    radial = np.array([1, 1, 0]) / math.sqrt(2)
    T.check(np.allclose(pl.origin, [c45, 60 + c45, 0], atol=1e-6), f"the plane origin is {pl.origin}")
    T.check(abs(float(pl.normal @ radial)) < 1e-9 and abs(float(pl.normal @ [0, 0, 1])) < 1e-9,
            f"the plane normal {pl.normal} is not tangent to the arc at the point")
    s = sketching(w, {"kind": "plane", "fid": pf["id"]})
    s.set_tool("circle")
    click(s, w.viewport, 0, 0)
    click(s, w.viewport, 3, 0)
    T.check(any(e["type"] == "circle" for e in s.sk.entities.values()), "no circle was drawn on the new plane")
    sk_id = w.sk_ctx["feature"]["id"]
    finish(w)
    T.check(doc.feature(sk_id) is not None and not doc.errors, doc.errors)
    skp = doc.state.sketches[sk_id].plane
    T.check(np.allclose(skp.origin, pl.origin, atol=1e-6), f"the sketch plane origin is {skp.origin}")
    T.say("[engine] plane on a sketch arc at a sketch point: through the point, normal to the arc, and a circle sketched on it")

    # ---- extrude a square with a 5 degree draft, and again symmetric (I1.4, I1.5)
    p1 = extrude_square(w, doc, sq1, end="blind", depth=10.0, draft=True, draft_angle=5.0)
    far, near = faces_at(p1.shape, 10.0), faces_at(p1.shape, 0.0)
    T.check(len(far) == 1 and len(near) == 1, "the drafted extrude has no single far and near face")
    T.check(np.allclose(side(far[0]), [18.2503, 18.2503], atol=1e-4 + 2e-6), f"far face {side(far[0])}, wanted 18.2503")
    T.check(np.allclose(side(near[0]), [20, 20], atol=1e-4), f"near face {side(near[0])}, wanted 20")
    T.check(K.is_valid(p1.shape), "the drafted extrude is not valid")
    T.say("[engine] extrude draft 5 degrees: a 20 mm square tapers to 18.2503 mm over 10 mm")
    p2 = extrude_square(w, doc, sq2, end="symmetric", depth=20.0, draft=True, draft_angle=5.0)
    want = 20 - 2 * 10 * T5
    for z in (10.0, -10.0):
        (face,) = faces_at(p2.shape, z)
        T.check(np.allclose(side(face), [want, want], atol=1e-4), f"symmetric draft at z={z:g}: {side(face)}, wanted {want}")
    T.check(len(K.solids(p2.shape)) == 1 and K.is_valid(p2.shape), "the symmetric drafted extrude is not one valid solid")
    T.say(f"[engine] extrude draft symmetric: both ends taper to {want:.4f} mm")

    # ---- the Slot tool on an arc - line - arc chain, then the one region extruded (I1.7)
    s = sketching(w, TOP)
    vp = w.viewport
    sk = s.sk
    p0, p1_ = sk.add_point(200, 0), sk.add_point(240, 0)
    p2, p3 = sk.add_point(250, 10), sk.add_point(190, 10)
    c1, c0 = sk.add_point(240, 10), sk.add_point(200, 10)
    ln = sk.add_line(p0, p1_)
    a1 = sk.add_arc(c1, p1_, p2)
    a0 = sk.add_arc(c0, p3, p0)
    sk.add_constraint("tangent", a1, ln)
    sk.add_constraint("tangent", a0, ln)
    sk.solve()
    length = 40 + 2 * (math.pi * 10 / 2)
    s.set_tool("slot")
    click(s, vp, 220, 0)
    type_size(s, "4")
    sk = s.sk                                   # the tool may replace the sketch object
    T.check(sk.status["ok"], sk.status)
    T.check(all(sk.entities[k]["construction"] for k in (ln, a0, a1)), "the chain was not made construction")
    slot_sk = w.sk_ctx["feature"]["id"]
    finish(w)
    T.check(not doc.errors, doc.errors)
    regions = region_refs(doc, slot_sk)
    T.check(len(regions) == 1, f"the Slot on a chain made {len(regions)} regions, not 1")
    p3_ = extrude_square(w, doc, slot_sk, end="blind", depth=3.0)
    area = length * 4 + math.pi * 4
    T.check(abs(K.volume(p3_.shape) - area * 3) < 1e-2, f"slot extrude volume {K.volume(p3_.shape):.2f}, wanted {area * 3:.2f}")
    T.say(f"[engine] slot on an arc-line-arc chain: one closed region of {area:.1f} mm2, extruded")

    # ---- a rib in the corner of an L bracket (I1.8, I1.9)
    bdoc, plane, _ = bracket()
    rib_ref = rib_sketch(bdoc, plane, line((40, 10), (10, 40)))
    w._add_tab(bdoc)
    app.processEvents()
    T.settle(w, 120)
    T.check(w.doc is bdoc, "the bracket tab did not open")
    bpart = next(iter(bdoc.state.parts.values()))
    before = K.volume(bpart.shape)
    rib_cmd = next((c for c in w._palette_commands() if c.name == "Rib"), None)
    T.check(rib_cmd is not None, "no Rib command in the palette")
    rib_cmd.action()
    T.check(w.mode == "dialog" and w.dialog.ftype == "rib", "the Rib tool did not open")
    dlg = w.dialog
    dlg.fields["path"].toggle(("path", rib_ref))
    dlg.fields["parts"].toggle(("part", bpart.id))
    dlg._set("thickness", 6.0)
    commit(w)
    last_feature(bdoc, "rib")
    after = K.volume(next(iter(bdoc.state.parts.values())).shape)
    T.check(len(bdoc.state.parts) == 1, f"the rib left {len(bdoc.state.parts)} parts")
    T.check(abs(after - before - 0.5 * 30 * 30 * 6) < 1e-3, f"rib added {after - before:.3f} mm3, wanted {0.5 * 30 * 30 * 6}")
    T.say(f"[engine] rib in an L bracket corner: one part, {after - before:.0f} mm3 added")

    # ---- file version 5 (I1.1)
    v4 = v4_studio_json()
    T.check(v4["version"] == 4, f"the fixture is version {v4['version']}")
    path4 = os.path.join(TMP, "v4.fshape")
    with open(path4, "w") as fh:
        json.dump(v4, fh)
    d4 = Document.load(path4)
    T.check(not d4.errors, d4.errors)
    saved = json.loads(d4.to_json())
    T.check(saved["version"] == 5, f"the version 4 file saved as version {saved['version']}")
    T.check({k: v for k, v in saved.items() if k != "version"} == {k: v for k, v in v4.items() if k != "version"},
            "saving the version 4 file changed keys other than version")
    out = os.path.join(TMP, "v4_saved.fshape")
    d4.save(out)
    with open(out) as fh:
        T.check(json.load(fh)["version"] == 5, "the saved file is not version 5")
    T.say("[no window] version 4 file: loads, saves as version 5, every other key unchanged")

    ex = PartStudioDoc.load(os.path.join(EXAMPLES_DIR, "bracket.fshape"))
    ex.rebuild()
    T.check(not ex.errors, ex.errors)
    T.check(hashes(ex) == BRACKET_HASHES, f"bracket.fshape part hashes {hashes(ex)}, on origin/master {BRACKET_HASHES}")
    T.say("[no window] example bracket.fshape (version 1): part hash equals the one from origin/master")


if __name__ == "__main__":                      # the engine workers import this file: only the main process runs it
    T.run_smoke("QUICKWINS SMOKE", TMP, run)
