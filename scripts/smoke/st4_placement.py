"""Place-by-connector smoke (slice S4): a Derived part placed on a Mate connector, with no typed offset.

Gate script of the Top-Down Design plan (helpers in topdown_common.py). The spike's example without its typed
offset: Part Studio Base holds a plate with two Mate connectors (top face, +X face). Part Studio Lid holds a block,
a Mate connector on the block's top, and a Derived of Base's plate that ticks both connectors and is placed by the
Derived dialog: Location = Lid's connector, Placement base = Base's top connector. The script checks:

  * the plate sits on the connector: the placed connector's frame equals Lid's, to within 1e-6 mm;
  * the block's thickness 4 -> 7 moves the placed part by exactly 3 mm, and the Derived's params hold no numeric
    offset, only connector ids;
  * choosing Base's +X connector as the base changes the placement and leaves the volume unchanged;
  * a fillet on an edge of the placed part keeps resolving by name after each edit.

Driven through the offscreen window: run_command("derived") opens the Derived dialog, whose widgets are set as a
person would, and commit_dialog() commits it. The Mate connectors and the fillet are added through the Document
API (a Mate connector or a fillet needs a viewport pick, which offscreen cannot make).

    both fs_py scripts/smoke/st4_placement.py
"""
import copy
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("st4_placement_")

import numpy as np  # noqa: E402
from conftest import add, line_along, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402


def face_connector(doc, pid, axis, sign, name):
    """A Mate connector on the face of part `pid` whose normal is `sign` along `axis` and which lies at the part's
    far side in that direction."""
    part = doc.state.parts[pid]
    lo, hi = K.bbox(part.shape)
    target = hi[axis] if sign > 0 else lo[axis]
    i = next(i for i, f in enumerate(K.faces(part.shape))
             if (K.face_info(f).get("n") is not None and K.face_info(f)["n"][axis] * sign > 0.9
                 and abs(K.face_info(f)["c"][axis] - target) < 1e-6))
    ref = A._ref_signature(part.shape, "face", i, doc.state.named(pid))
    fr = A.connector_frame(part.shape, "face", i)
    return add(doc, "mate_connector", name=name, ref=ref, kind="face", x_ref=A.connector_x_ref(fr), flip=False)


def frame(c):
    fr = c["frame"]
    return [np.array(fr[k], float) for k in ("o", "z", "x")]


def frame_error(a, b):
    """Largest difference in mm between two frames: origins, and the tips of unit Z and X axes."""
    return max(float(np.abs(p - q).max()) for p, q in zip(a, b))


def bbox(shape):
    lo, hi = K.bbox(shape)
    return np.array([lo, hi])


def set_depth(studio, depth):
    """Set the depth of the studio's first extrude, as an edit."""
    f = copy.deepcopy(next(f for f in studio.features if f["type"] == "extrude"))
    f["params"]["depth"] = depth
    studio.replace_feature(f)
    T.check(not studio.errors, studio.errors)


def numbers_in(x):
    """Every number found anywhere inside a params value."""
    if isinstance(x, bool):
        return []
    if isinstance(x, (int, float)):
        return [x]
    if isinstance(x, dict):
        return [n for v in x.values() for n in numbers_in(v)]
    if isinstance(x, (list, tuple)):
        return [n for v in x for n in numbers_in(v)]
    return []


def run():
    app, w = T.new_window(TMP)
    fd = DF.FolderDocument.create(os.path.join(TMP, "Doc"), name="Doc")
    base = fd.add_tab("studio", "Base")
    sid = sketch_feature(base.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    plate = add(base.doc, "extrude", regions=region_refs(base.doc, sid), depth=5, op="new")["id"] + "/1"
    top = face_connector(base.doc, plate, 2, 1, "Top")["id"]
    side = face_connector(base.doc, plate, 0, 1, "Side")["id"]
    T.check(not base.doc.errors, base.doc.errors)
    lid = fd.add_tab("studio", "Lid")
    block = add(lid.doc, "extrude", regions=region_refs(lid.doc, sketch_feature(
        lid.doc, lambda s: rect_lines(s, 100, 100, 30, 20))), depth=4, op="new")["id"] + "/1"
    spot = face_connector(lid.doc, block, 2, 1, "Spot")["id"]
    T.check(not lid.doc.errors, lid.doc.errors)
    fd.save()
    path = fd.path
    fd = w.open_document(path)              # the way a user opens it: the window adopts every tab
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    base, lid = fd.tab(base.id), fd.tab(lid.id)
    w.tabs.setCurrentIndex(w._tab_index(lid.doc))
    app.processEvents()
    T.check(w.doc is lid.doc, "Lid is not the current tab")

    # ---- the Derived dialog: Base's plate and both connectors, placed on Lid's connector by Base's top connector
    T.run_command(w, "derived")
    d = w.dialog
    T.check(d is not None, "the Derived command opened no dialog")
    src = d.widgets["source"]
    src.setCurrentIndex(src.findData(base.id))
    from PySide6.QtCore import Qt
    for name in ("parts", "connectors"):
        lst = d.widgets[name]
        for i in range(lst.count()):
            lst.item(i).setCheckState(Qt.Checked)
    loc = d.widgets["location"]
    loc.setCurrentIndex(loc.findData(spot))
    T.check(loc.currentData() == spot, "Lid's connector is not offered as Location")
    d.widgets["base"].setCurrentIndex(d.widgets["base"].findData(top))
    T.check(d.widgets["base"].currentData() == top, "Base's top connector is not offered as Placement base")
    w.commit_dialog()
    T.check(not lid.doc.errors, lid.doc.errors)
    der = next(f for f in lid.doc.features if f["type"] == "derived")
    fid = der["id"]
    pid = f"{fid}/1"
    T.check(der["params"]["placement"] == {"location": spot, "base": top}, der["params"].get("placement"))

    def placed():
        return lid.doc.state.parts[pid].shape

    # ---- placed: the plate's top connector sits on Lid's connector
    got = frame(lid.doc.state.connectors[f"{fid}:{top}"])
    err = frame_error(got, frame(lid.doc.state.connectors[spot]))
    T.check(err < 1e-6, f"placed connector off its location by {err:g} mm")
    T.check(abs(bbox(placed())[1][2] - 4.0) < 1e-6, bbox(placed()))          # the plate's top face is at the block's top
    T.say(f"placed: plate sits on the connector with the frame error under 1e-6 mm (measured {err:.1e})")

    # ---- a fillet on an edge of the placed part
    part = lid.doc.state.parts[pid]
    vertical = [e for e in K.edges(part.shape) if line_along((0, 0, 1))(K.edge_info(e))]
    T.check(vertical, "no vertical edge on the placed part")
    edges = [F.make_ref(pid, vertical[0], "edge", part.shape, lid.doc.state.named(pid))]
    fil = add(lid.doc, "fillet", edges=edges, radius=1)
    T.check(not lid.doc.errors, lid.doc.errors)
    vol0 = K.volume(placed())
    T.check(vol0 < 20 * 10 * 5 - 1e-3, f"the fillet removed no material (volume {vol0:g})")

    def fillet_resolves(after):
        T.check(not lid.doc.errors, lid.doc.errors)
        T.check(fil["id"] not in lid.doc.state.recovered, f"fillet recovered, not resolved by name, {after}")
        names = lid.doc.state.named(pid)
        T.check(names is not None and names.names, f"placed part has no element names {after}")
        v = K.volume(placed())
        T.check(abs(v - vol0) < 1e-6, f"fillet volume {v:g}, want {vol0:g}, {after}")

    # ---- thickness 4 -> 7: the placed part moves with the connector, and no offset is typed anywhere
    z0 = bbox(placed())
    set_depth(lid.doc, 7.0)
    moved = float(np.linalg.norm(bbox(placed()) - z0, axis=1).max())
    T.check(abs(moved - 3.0) < 1e-6, f"placed part moved {moved:.6f} mm, want 3")
    T.check(abs(bbox(placed())[1][2] - 7.0) < 1e-6, bbox(placed()))
    der = next(f for f in lid.doc.features if f["id"] == fid)
    pl = der["params"]["placement"]
    T.check(set(pl) == {"location", "base"} and all(isinstance(v, str) for v in pl.values()), pl)
    T.check(not numbers_in({k: v for k, v in der["params"].items() if k != "parts"}), f"numbers in params {der['params']}")
    T.check(not any("offset" in k or k in ("dx", "dy", "dz", "translate") for k in der["params"]), der["params"])
    fillet_resolves("after thickness 4 -> 7")
    T.say(f"thickness 4 -> 7: placed part moved {moved:.3f} mm, no numeric offset in the Derived's params")

    # ---- base connector changed: Base's +X connector lands on Lid's connector instead
    before = bbox(placed())
    w.start_feature("derived", edit_fid=fid)
    d = w.dialog
    T.check(d.widgets["base"].currentData() == top, "the dialog did not show the stored base")
    d.widgets["base"].setCurrentIndex(d.widgets["base"].findData(side))
    w.commit_dialog()
    T.check(not lid.doc.errors, lid.doc.errors)
    T.check(next(f for f in lid.doc.features if f["id"] == fid)["params"]["placement"] == {"location": spot, "base": side},
            "the base was not stored")
    got = frame(lid.doc.state.connectors[f"{fid}:{side}"])
    err = frame_error(got, frame(lid.doc.state.connectors[spot]))
    T.check(err < 1e-6, f"+X connector off its location by {err:g} mm")
    after = bbox(placed())
    T.check(float(np.abs(after - before).max()) > 1.0, "the placement did not change")
    fillet_resolves("after the base connector changed")        # also checks the volume is unchanged
    T.say(f"base connector changed: placement changed (bbox moved {float(np.abs(after - before).max()):.1f} mm), "
          f"volume unchanged ({K.volume(placed()):.3f} mm3)")

    T.say("fillet on placed part: resolved by name after each edit (thickness 4 -> 7, base connector changed)")


T.run_smoke("ST4", TMP, run)
