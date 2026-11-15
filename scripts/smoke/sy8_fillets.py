"""Variable fillet and fillet failures smoke (slice S8), in the real main window.

Gate script of the Onshape Parity 2 plan (helpers in topdown_common.py). The window is a MainWindow(engine=True),
as the app starts it: Part Studio tabs rebuild in a worker, and every commit waits (with a timeout) for the worker
before the built state is read. Blocks and the pocket are set up before their tab opens; every fillet is then made
through the Fillet dialog (start_feature, the dialog's handle_pick and _set, the Vertices rows' number boxes,
commit_dialog):

  1. A variable fillet on the front top edge of a 40 x 20 x 10 block, radius 2 at one end vertex and 6 at the
     other, measured on the end faces at both ends; then the same with the radii swapped.
  2. An oversized variable fillet (15 at both ends): the message names a percentage ("about N percent of these
     radii"); that percentage, entered through the dialog on the same feature, builds.
  3. A fillet of radius 5 on the floor edge of a blind hole of radius 3 (an edge beside a concave cylinder smaller
     than the radius): the message names the edge, the error carries edge references across the engine wire, and
     the window's highlight list (MainWindow.error_hl_refs and viewport.feature_hl) holds that edge. Fixing the
     radius clears the highlight.
  3b. A fillet on a cylinder's seam edge alone (a failure that is not about size): no smaller radius is
     suggested, the message says where, and the failing edge is in the highlight list.
  4. The three golden constant fillets (tests/golden/features.json): their volumes equal the golden values.
  5. A constant fillet through the dialog still saves no fillet_type or vertex_radii.
  6. Save, reopen and compare: the variable fillet file loads, its parameters are unchanged and it builds to the
     same volume, as a Part Studio tab in engine mode.

Not through the real UI: a pick is a Pick object handed to the dialog (no screen click); the blocks, the pocket and
the three golden models are built with the test helpers before their tab opens (the golden models are rebuilt in
the worker, which is the build that is measured).

    both fs_py scripts/smoke/sy8_fillets.py
"""
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy8_fillets_") if __name__ == "__main__" else None   # a worker imports this file again

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QLineEdit  # noqa: E402
from conftest import (FREEFORM, TOP, add, block_doc, edge_refs, only_part, rect_lines, region_refs,  # noqa: E402
                      sketch_feature)
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_fillet_variable import end_offsets, front_top_edge  # noqa: E402
from test_golden_features import VERTICAL, _top_edges  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "features.json")
TOL = 1e-3          # mm, measured on faces of a 40 mm block


def say(mode, msg):
    T.say(f"ok [{mode}] {msg}")


def settle(w):
    T.settle(w, 120)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")


def open_tab(app, w, doc):
    """Open `doc` as a Part Studio tab (a worker builds it) and wait for the build."""
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    settle(w)
    T.check(w.doc is doc, "the Part Studio tab did not open")
    return doc


def block():
    return block_doc(40, 20, 10)


def fillets(doc):
    return [f for f in doc.features if f["type"] == "fillet"]


def edge_pick(part):
    i = next(i for i, e in enumerate(K.edges(part.shape)) if front_top_edge(K.edge_info(e)))
    return Pick("edge", (part.id, i))


def vertex_pick(part, pos):
    pts = K.vertex_points(part.shape)
    return Pick("vertex", (part.id, next(i for i, q in enumerate(pts) if np.linalg.norm(np.asarray(q) - pos) < 1e-6)))


def row_box(d, i):
    f = d.fields["vertices"]
    box = f.list.itemWidget(f.list.item(i)).findChild(QLineEdit)
    T.check(box is not None, f"vertex row {i} has no number box")
    return box


def type_in(d, i, text):
    ed = row_box(d, i)
    ed.setText(text)
    ed.textEdited.emit(text)


def commit(w):
    T.commit_open_dialog(w)
    settle(w)


def variable_fillet(w, r0, r1, default=None):
    """The Fillet dialog on the block's front top edge: Type "Variable radius", a number at each end vertex."""
    doc = w.doc
    part = only_part(doc)
    w.start_feature("fillet")
    d = w.dialog
    T.check(w.mode == "dialog" and d.ftype == "fillet", "the Fillet dialog did not open")
    d.handle_pick(edge_pick(part))
    T.check("fillet_type" in d.widgets, f"the Fillet dialog has no Type choice: {sorted(d.widgets)}")
    d._set("fillet_type", "variable")
    T.check(d.fields["vertices"].isVisibleTo(d), "the Vertices list is not shown for Variable radius")
    if default is not None:
        d._set("radius", default)
    d.activate(d.fields["vertices"])
    d.handle_pick(vertex_pick(part, (0, 0, 10)))
    d.handle_pick(vertex_pick(part, (40, 0, 10)))
    T.check(len(d.fields["vertices"].items) == 2, f"expected 2 vertex rows, got {len(d.fields['vertices'].items)}")
    type_in(d, 0, str(r0))
    type_in(d, 1, str(r1))
    commit(w)
    (fe,) = fillets(doc)
    return fe


def pocket_doc():
    """A 40 x 30 x 10 block with a blind hole of radius 3 drilled up from the bottom: its floor edge is the circle
    at z = 6, where a concave cylinder (the wall) meets the floor."""
    doc = PartStudioDoc()
    sb = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 30), TOP)
    add(doc, "extrude", regions=region_refs(doc, sb), depth=10, op="new")
    hole = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(20, 15)), TOP)
    add(doc, "hole", sketch=hole, standard="custom", diameter=6.0, end="blind", depth=6.0, style="simple", tip=False)
    return doc


def cylinder_doc():
    """A radius 10, height 20 cylinder: its one seam edge cannot be rounded on its own."""
    doc = PartStudioDoc()
    sc = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, sc), depth=20, op="new")
    return doc


def floor_edge(part):
    for i, e in enumerate(K.edges(part.shape)):
        info = K.edge_info(e)
        if info["type"] != int(K.GeomAbs_Line) and abs(info["c"][2] - 6) < 1e-6:
            return i
    raise T.Fail("the pocket has no floor edge at z = 6")


def run():
    app, w = T.new_engine_window(TMP)
    try:
        body(app, w)
    finally:
        T.close_engine_window(w)


def body(app, w):
    # ---- 1. a variable fillet, radius 2 at one end and 6 at the other, measured at both ends
    doc = open_tab(app, w, block())
    fe = variable_fillet(w, 2, 6)
    T.check(doc.errors.get(fe["id"]) is None, f"variable fillet 2 / 6: {doc.errors.get(fe['id'])}")
    T.check(fe["params"].get("fillet_type") == "variable" and len(fe["params"]["vertex_radii"]) == 2,
            f"the dialog saved {fe['params']}")
    off = end_offsets(doc)
    T.check(np.allclose(off[0], [2, 2], atol=TOL),
            f"variable fillet 2 / 6: expected 2 mm at x = 0, measured {off[0]}")
    T.check(np.allclose(off[40], [6, 6], atol=TOL),
            f"variable fillet 2 / 6: expected 6 mm at x = 40, measured {off[40]}")
    say("engine", f"variable fillet through the dialog: {off[0][0]:.3f} mm at one end vertex, "
                  f"{off[40][0]:.3f} mm at the other")
    saved_doc, saved_vol = doc, K.volume(only_part(doc).shape)
    T.check(saved_vol < 8000, "the variable fillet removed no material")

    doc = open_tab(app, w, block())
    fe = variable_fillet(w, 6, 2)
    T.check(doc.errors.get(fe["id"]) is None, f"variable fillet 6 / 2: {doc.errors.get(fe['id'])}")
    off = end_offsets(doc)
    T.check(np.allclose(off[0], [6, 6], atol=TOL) and np.allclose(off[40], [2, 2], atol=TOL),
            f"swapped radii: expected 6 at x = 0 and 2 at x = 40, measured {off}")
    T.check(abs(K.volume(only_part(doc).shape) - saved_vol) < 1e-6 * saved_vol,
            "swapped radii: the mirrored fillet removes a different volume")
    say("engine", f"swapped radii mirror it: {off[0][0]:.3f} mm at x = 0, {off[40][0]:.3f} mm at x = 40, same volume")

    # ---- 2. an oversized variable fillet: the message names a percentage, and that percentage builds
    doc = open_tab(app, w, block())
    fe = variable_fillet(w, 15, 15, default=15)
    err = doc.errors.get(fe["id"])
    m = re.search(r"about (\d+) percent of these radii", err or "")
    T.check(m is not None, f"oversized variable fillet: no percentage in the message: {err!r}")
    pct = int(m.group(1))
    T.check(0 < pct < 100, f"oversized variable fillet: the percentage {pct} is not between 0 and 100")
    say("engine", f"oversized variable fillet (15 mm at both ends) fails and says: {err}")
    w.start_feature("fillet", edit_fid=fe["id"])
    d = w.dialog
    r = round(0.15 * pct, 6)
    d._set("radius", r)
    type_in(d, 0, str(r))
    type_in(d, 1, str(r))
    commit(w)
    T.check(doc.errors.get(fe["id"]) is None, f"{pct} percent ({r} mm) does not build: {doc.errors.get(fe['id'])}")
    T.check(K.volume(only_part(doc).shape) < 8000, f"{pct} percent built a part that lost no material")
    say("engine", f"{pct} percent of the radii ({r:g} mm) builds")

    # ---- 3. a fillet that fails for its shape: the message names the edge, the highlight list is set
    doc = open_tab(app, w, pocket_doc())
    part = only_part(doc)
    w.start_feature("fillet")
    d = w.dialog
    d.handle_pick(Pick("edge", (part.id, floor_edge(part))))
    d._set("radius", 5.0)
    commit(w)
    (fe,) = fillets(doc)
    err = doc.errors.get(fe["id"])
    T.check(err, "a 5 mm fillet on the floor of a 3 mm radius hole built")
    T.check(re.search(r"edge near \(", err), f"the message does not name the edge: {err!r}")
    say("engine", f"fillet beside a concave cylinder smaller than the radius fails and names the edge: {err}")
    refs = doc.error_refs.get(fe["id"])
    T.check(refs and all(r["kind"] == "edge" for r in refs),
            f"the error refs did not cross the engine wire: {doc.error_refs}")
    say("engine", f"the failing edge reference reached the window ({len(refs)} edge)")
    hl = w.error_hl_refs()
    T.check(hl == list(refs), f"error_hl_refs {hl} is not the error's refs {refs}")
    feature_hl = (w.viewport.feature_hl or {}).get("edges")
    T.check(feature_hl and sum(len(v) for v in feature_hl.values()) == 1,
            f"the viewport highlight list is not set to the failing edge: {w.viewport.feature_hl}")
    say("engine", "the failing edge is in the viewport's highlight list")
    w.start_feature("fillet", edit_fid=fe["id"])
    w.dialog._set("radius", 2.0)
    commit(w)
    T.check(doc.errors.get(fe["id"]) is None, f"2 mm on the pocket floor: {doc.errors.get(fe['id'])}")
    T.check(w.error_hl_refs() == [] and not (w.viewport.feature_hl or {}).get("edges"),
            "the highlight stays after the fillet is fixed")
    say("engine", "fixing the radius builds it and clears the highlight")

    # ---- 3b. a fillet that fails for a reason other than size: no smaller radius is suggested
    doc = open_tab(app, w, cylinder_doc())
    part = only_part(doc)
    seam = sorted(K.seam_edge_indices(part.shape))
    T.check(len(seam) == 1, f"the cylinder has {len(seam)} seam edges, not 1")
    w.start_feature("fillet")
    w.dialog.handle_pick(Pick("edge", (part.id, seam[0])))
    w.dialog._set("radius", 2.0)
    commit(w)
    (fe,) = fillets(doc)
    err = doc.errors.get(fe["id"])
    T.check(err, "a fillet on a cylinder's seam edge alone built")
    T.check("the largest that works here" not in err, f"a non-size failure suggests a smaller radius: {err!r}")
    T.check("seam" in err and re.search(r"edge near \(", err), f"the message does not say where: {err!r}")
    say("engine", f"fillet on a cylinder's seam edge fails with no smaller radius suggested and says where: {err}")
    refs = doc.error_refs.get(fe["id"])
    T.check(refs and all(r["kind"] == "edge" for r in refs),
            f"the seam failure's edge refs did not reach the window: {doc.error_refs}")
    feature_hl = (w.viewport.feature_hl or {}).get("edges")
    T.check(w.error_hl_refs() == list(refs) and feature_hl and sum(len(v) for v in feature_hl.values()) >= 1,
            f"the non-size failure's edge is not in the viewport highlight list: {w.viewport.feature_hl}")
    say("engine", f"the non-size failure's edge reached the window and is in the highlight list ({len(refs)} edge)")

    # ---- 4. the three golden constant fillets
    with open(GOLDEN) as fh:
        golden = json.load(fh)
    for name, pred, expect, radius in (("fillet_vertical_edges", VERTICAL, 4, 3.0),
                                       ("fillet_top_loop", _top_edges, 4, 2.0),
                                       ("fillet_all_edges", lambda i: True, 12, 1.0)):
        g = block_doc(40, 20, 10)
        add(g, "fillet", edges=edge_refs(only_part(g), pred, expect=expect), radius=radius)
        g = open_tab(app, w, g)
        T.check(not g.errors, f"golden {name}: {g.errors}")
        got, want = K.volume(only_part(g).shape), golden[name]["F2/1"]["volume"]
        T.check(abs(got - want) <= FREEFORM * want, f"golden {name}: volume {got!r}, golden {want!r}")
        say("engine", f"golden {name} builds to the golden volume {want:.4f} (got {got:.4f})")

    # ---- 5. a constant fillet through the dialog saves what it saved before
    doc = open_tab(app, w, block())
    w.start_feature("fillet")
    w.dialog.handle_pick(edge_pick(only_part(doc)))
    commit(w)
    (fe,) = fillets(doc)
    T.check(doc.errors.get(fe["id"]) is None, f"constant fillet: {doc.errors.get(fe['id'])}")
    T.check("fillet_type" not in fe["params"] and "vertex_radii" not in fe["params"],
            f"a constant fillet saved {sorted(fe['params'])}")
    say("engine", "a constant fillet through the dialog saves no fillet_type or vertex_radii")

    # ---- 6. save, reopen, compare
    path = os.path.join(TMP, "variable.fshape")
    saved_doc.save(path)
    again = PartStudioDoc.load(path)
    (a,), (b,) = fillets(saved_doc), fillets(again)
    T.check(a["params"] == b["params"], f"the variable fillet changed on reload: {a['params']} vs {b['params']}")
    say("no window", "the saved file reloads with the same fillet type and vertex radii")
    again = open_tab(app, w, again)
    T.check(not again.errors, f"the reopened file: {again.errors}")
    v2 = K.volume(only_part(again).shape)
    T.check(abs(v2 - saved_vol) < 1e-6 * saved_vol, f"the reopened file's volume {v2!r} is not {saved_vol!r}")
    off = end_offsets(again)
    T.check(np.allclose(off[0], [2, 2], atol=TOL) and np.allclose(off[40], [6, 6], atol=TOL),
            f"the reopened file measures {off}, not 2 and 6")
    say("engine", "the reopened file rebuilds in a worker to the same volume and the same 2 and 6 mm ends")

    T.say("FILLETS SMOKE OK")


if __name__ == "__main__":
    try:
        run()
    except T.Fail as e:
        print(f"FILLETS SMOKE FAILED: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        print("FILLETS SMOKE FAILED: unexpected error", file=sys.stderr, flush=True)
        sys.exit(1)
