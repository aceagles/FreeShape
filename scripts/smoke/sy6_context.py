"""Context identity and display smoke (slice S6): a Context whose bodies keep their identity, and the controls that
show it, end to end in a real MainWindow(engine=True).

Gate script of the Onshape Parity 2 plan. The window is MainWindow(engine=True), as the app starts it: the Part Studio
rebuilds in a worker that the script waits for after every edit, and the Context bodies must reach the window as
reference parts (they did not, until the engine-mode fix this slice rests on). Every check line carries a mode tag:
[engine] ran in that window; [no window] is about files only and says why.

The Document is built before the window opens: Plate (the edited part), Wheel (a cylinder, one part twice in the
Assembly) and Cap, and an Assembly Asm of Plate, Wheel A at (-25, 5, 20), Wheel B at (25, 5, 40) and Cap C. Every
later step goes through the window: Edit in context (the edit_in_context action), the sketcher's Use tool, the
Hide context instance command, Update context, the Context bar, the View option Select through context.

  1. Edit in context gives ids "instance", bodies <fid>/<instance id>, marked as Context in the window.
  2. With Select through context on a click over a Context body picks the user's own face behind it; where only a
     Context body lies it is picked; with the option off the Context body in front wins.
  3. A sketch circle concentric to a Use projection of wheel B's rim, wheel B hidden by hand and an extrude up to
     B's bottom face; wheel A deleted and the Context updated: all four stay on B, C is not hidden.
  4. B moved up 6 mm and a new twin D inserted where B was: still B (the extrude ends at B's new place, D is shown).
  5. B moved sideways: the Update context moves the projection, the concentric circle with it.
  6. The Context bar: label and status, Update, Go to assembly, the eye (whole Context), the instance list's eyes,
     Select through, the slider (no rebuild of the model).
  7. A Document saved before the run (generated here: positional ids, no ids key, a hidden B, an extrude up to B)
     opens unchanged, and gets instance ids at its first Update context.

Not through the real UI: the Assembly edits (delete, move, insert) are made on the Assembly document and the Hide
context instance command is the window function the right-click menu calls, as the other gate scripts do for edits
that need a modal menu; the sketch is drawn with the Sketch API, the Use projection is made by the sketcher's own
_use_edge on the window's viewport.

    both fs_py scripts/smoke/sy6_context.py
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("sy6_context_") if __name__ == "__main__" else None      # a worker imports this file again

import numpy as np  # noqa: E402
from conftest import TOP, add, block_doc, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

TOL = 1e-6
R_WHEEL = 8.0
A_AT, B_AT, C_AT = (-25.0, 5.0, 20.0), (25.0, 5.0, 40.0), (0.0, 0.0, 60.0)
RED = (0.9, 0.1, 0.1)
ENGINE, FILES = "engine", "no window"


def say(mode, msg):
    T.say(f"ok [{mode}] {msg}")


# ------------------------------------------------------------------------------------------------ the Document
def wheel_doc():
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), R_WHEEL))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=4.0, op="new")
    return doc


def build_document(path, ids):
    """Plate, Wheel and Cap Part Studios and the Assembly Asm (Plate, Wheel A, Wheel B, Cap C), saved to `path`.
    `ids` "instance": a Context of the Plate is added the way Edit in context would, later, by the window, so none
    is added here. Returns (folder, plate tab, wheel tab, asm tab, {"P", "A", "B", "C"} -> instance id)."""
    fd = DF.FolderDocument.create(path, name="Doc")
    plate = fd.add_tab("studio", "Plate")
    plate.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    wheel = fd.add_tab("studio", "Wheel")
    wheel.doc.__dict__.update(wheel_doc().__dict__)
    cap = fd.add_tab("studio", "Cap")
    cap.doc.__dict__.update(block_doc(6, 6, 4).__dict__)
    for t in (plate, wheel, cap):
        fd._attach(t)
    asm = fd.add_tab("assembly", "Asm")
    a = asm.doc

    def source(tab):
        return L.studio_source(tab, f"{tab.doc.features[1]['id']}/1")

    iids = {"P": a.insert(source(plate), T=np.eye(4), name="Plate"),
            "A": a.insert(source(wheel), T=A.translation(A_AT), name="Wheel A"),
            "B": a.insert(source(wheel), T=A.translation(B_AT), name="Wheel B"),
            "C": a.insert(source(cap), T=A.translation(C_AT), name="Cap C")}
    a.refresh(solve=False, notify=False)
    T.check(not a.errors, a.errors)
    return fd, plate, wheel, asm, iids


def zmin(doc, pid):
    return float(K.bbox(doc.state.parts[pid].shape)[0][2])


def bottom_ref(doc, pid):
    part = doc.state.parts[pid]
    bottom = min(K.faces(part.shape), key=lambda f: K.interior_point(f)[2])
    ref = F.make_ref(pid, bottom, "face", part.shape, named=doc.state.named(pid))
    T.check("name" in ref, f"the reference to the bottom face of {pid} carries no element name")
    return ref


def bar_top(doc, eid):
    pid = next(p for p in doc.state.parts if p.startswith(eid + "/"))
    return float(K.bbox(doc.state.parts[pid].shape)[1][2])


# ------------------------------------------------------------------------------------------------ the window
def settle(w):
    T.settle(w, 180)
    T.check(not w.studio_engine.broken, f"the engine failed: {w.studio_engine.broken}")


def add_feature(w, doc, ftype, **params):
    """Add a feature to the window's own document and wait for the worker; fail if the build reports an error."""
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    settle(w)
    T.check(not doc.errors.get(f["id"]), f"{ftype}: {doc.errors.get(f['id'])}")
    return f


def show(w, doc):
    w.tabs.setCurrentIndex(w._tab_index(doc))
    settle(w)
    T.check(w.doc is doc, "the tab did not open")


def edit_in_context(w, asm, iid):
    show(w, asm.doc)
    w.asm.selected_instance_ids = lambda: [iid]
    T.run_action(w, "edit_in_context")
    settle(w)


def update(w, doc, fid):
    """Update context through the window (the Context bar's button calls the same function)."""
    show(w, doc)
    w.update_context(fid)
    settle(w)
    T.check(not doc.errors, doc.errors)


def new_window(app_w=None):
    app, w = T.new_engine_window(TMP)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    return app, w


def open_folder(w, path):
    fd = w.open_document(path)
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    settle(w)
    return fd


def context_fid(doc):
    return next(f["id"] for f in doc.features if f["type"] == "context")


# ------------------------------------------------------------------------------------------------ the checks
def run_new_context(app, w):
    path = os.path.join(TMP, "Doc")
    fd, plate, wheel, asm, iids = build_document(path, "instance")
    fd.save()
    fd = open_folder(w, path)
    plate, wheel, asm = (fd.tab(t.id) for t in (plate, wheel, asm))
    doc, a = plate.doc, asm.doc

    # ---- 1. Edit in context
    edit_in_context(w, asm, iids["P"])
    T.check(w.doc is doc, "Edit in context did not open the Plate")
    cid = context_fid(doc)
    T.check(doc.feature(cid)["params"].get("ids") == "instance", doc.feature(cid)["params"])
    pid = {k: f"{cid}/{iids[k]}" for k in "ABC"}
    T.check(not doc.errors, doc.errors)
    own = {i for i, p in doc.state.parts.items() if not p.reference}
    refs = {i for i, p in doc.state.parts.items() if p.reference}
    T.check(refs == set(pid.values()), f"Context bodies {sorted(refs)}, expected {sorted(pid.values())}")
    T.check(len(own) == 1, f"the Plate's own parts {sorted(own)}")
    T.check(w.viewport.reference_ids == refs, f"the viewport marks {sorted(w.viewport.reference_ids)}")
    T.check(abs(zmin(doc, pid["B"]) - B_AT[2]) < TOL, zmin(doc, pid["B"]))
    say(ENGINE, f"Edit in context: ids instance, three Context bodies {sorted(pid.values())} shown in the window")

    # ---- 2. Select through context
    import numpy as np
    from freeshape.ui.camera import basis
    from test_select_other_studio import at
    vp = w.viewport
    vp.cam.set_state((np.array([0.0, 10, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    vp.select_filter = vp.user_filter = None
    x, y = at(w, B_AT[0], B_AT[1], 10)
    xa, ya = at(w, A_AT[0], A_AT[1], A_AT[2] + 4)
    T.check(0 < x < vp.width() and 0 < y < vp.height() and 0 < xa < vp.width() and 0 < ya < vp.height(),
            f"the test spots {(x, y)} {(xa, ya)} are off the {vp.width()} x {vp.height()} view")

    def picked(px, py):
        pk = vp.pick(px, py, kinds={"face"})
        return pk.key[0] if pk is not None else None
    w.set_select_through_context(True, save=False)
    T.check(vp.select_through and vp.pick_through_ids == refs, vp.pick_through_ids)
    got = picked(x, y)
    T.check(got in own, f"select through on: the click over wheel B picked {got}, not the Plate's own face")
    say(ENGINE, f"select through on: a click over wheel B picks the Plate's own face ({got})")
    got = picked(xa, ya)
    T.check(got == pid["A"], f"select through on: a click where only wheel A lies picked {got}")
    say(ENGINE, "select through on: a click where only wheel A lies still picks that Context body")
    w.set_select_through_context(False, save=False)
    got = picked(x, y)
    T.check(got == pid["B"], f"select through off: the click over wheel B picked {got}, expected the Context body")
    say(ENGINE, "select through off: the Context body in front wins, as before")
    w.set_select_through_context(True, save=False)

    # ---- 3. sketch Use of B's rim + concentric circle, B hidden, extrude up to B, delete A, update
    s_b = doc.state.parts[pid["B"]]
    seams = K.seam_edge_indices(s_b.shape)
    rims = [i for i, e in enumerate(K.edges(s_b.shape)) if i not in seams and "r" in K.edge_info(e)]
    rim_i = max(rims, key=lambda i: K.edge_info(K.edges(s_b.shape)[i])["c"][2])
    w.start_sketch()
    T.check(w.mode == "sketch", "the sketch did not start")
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher
    sid = w.sk_ctx["feature"]["id"]
    rim = s._use_edge(w.viewport, Pick("edge", (pid["B"], rim_i)))
    sk = s.sk
    T.check(rim in sk.entities and sk.entities[rim]["src"]["part"] == pid["B"], "the Use of wheel B's rim made no entity")
    cx, cy = sk.P(sk.entities[rim]["c"])
    T.check(abs(cx - B_AT[0]) < TOL and abs(cy - B_AT[1]) < TOL, (cx, cy))
    conc = sk.add_circle(sk.add_point(cx, cy + 1), 3.0)
    sk.add_constraint("concentric", conc, rim)
    T.check(sk.solve(), "the sketch did not solve")
    w.finish_sketch()
    settle(w)
    T.check(not doc.errors, doc.errors)

    def centres():
        k = doc.state.sketches[sid].sketch
        return k.P(k.entities[rim]["c"]), k.P(k.entities[conc]["c"])
    pr, pc = centres()
    T.check(np.allclose(pr, B_AT[:2], atol=TOL) and np.allclose(pc, B_AT[:2], atol=TOL), (pr, pc))
    say(ENGINE, "sketch: Use of wheel B's rim and a concentric circle sit at wheel B")

    w.hide_context_instances([pid["B"]])
    settle(w)
    T.check(doc.hidden_parts == {pid["B"]}, doc.hidden_parts)
    ssid = sketch_in_window(w, doc, lambda k: rect_lines(k, 2, 2, 2, 2))
    eid = add_feature(w, doc, "extrude", regions=region_refs(doc, ssid), end="up_to_face", op="new",
                      up_to={"kind": "face", "ref": bottom_ref(doc, pid["B"])})["id"]
    T.check(abs(bar_top(doc, eid) - B_AT[2]) < TOL, bar_top(doc, eid))
    say(ENGINE, "wheel B hidden by hand and an extrude up to its bottom face (ends at z 40)")

    a.delete_instances([iids["A"]])
    show(w, doc)
    T.check(w.folder.context_status(plate.id, cid) == "out of date", w.folder.context_status(plate.id, cid))
    update(w, doc, cid)
    T.check(w.folder.context_status(plate.id, cid) == "current", w.folder.context_status(plate.id, cid))
    T.check(pid["A"] not in doc.state.parts and pid["B"] in doc.state.parts, sorted(doc.state.parts))

    def on_b():
        T.check(doc.hidden_parts == {pid["B"]}, f"hidden is {doc.hidden_parts}, expected only {pid['B']}")
        T.check(pid["C"] not in doc.hidden_parts and pid["C"] in doc.state.parts, "C is hidden or gone")
        T.check(abs(bar_top(doc, eid) - doc_b_z[0]) < TOL, f"the extrude ends at {bar_top(doc, eid)}")
        ref = doc.feature(eid)["params"]["up_to"]["ref"]
        T.check(ref["part"] == pid["B"], f"the extrude's face is on {ref['part']}")
        T.check(eid not in getattr(doc.state, "recovered", {}), "the extrude was recovered by signature, not by name")
        ent = doc.feature(sid)["params"]["sketch"]["entities"][rim]
        T.check(ent["src"]["part"] == pid["B"], f"the projection is on {ent['src']['part']}")
    doc_b_z = [B_AT[2]]
    on_b()
    pr, pc = centres()
    T.check(np.allclose(pr, B_AT[:2], atol=TOL) and np.allclose(pc, B_AT[:2], atol=TOL), (pr, pc))
    T.check(not np.allclose(pr, A_AT[:2], atol=1e-3), "the projection jumped to wheel A's old place")
    say(ENGINE, "wheel A deleted + Update context: hidden flag, extrude and projected rim are still on wheel B")

    # ---- 4. B moves up 6 and a new twin D sits in B's old place
    a.move_instance(iids["B"], A.translation((0, 0, 6)), solve=False)      # a move is relative to where it is
    src = a.instance(iids["B"])["source"]
    iids["D"] = a.insert(src, T=A.translation(B_AT), name="Wheel D")
    a.refresh(solve=False, notify=False)
    update(w, doc, cid)
    pid["D"] = f"{cid}/{iids['D']}"
    T.check(abs(zmin(doc, pid["B"]) - (B_AT[2] + 6)) < TOL and abs(zmin(doc, pid["D"]) - B_AT[2]) < TOL,
            (zmin(doc, pid["B"]), zmin(doc, pid["D"])))
    T.check(pid["D"] not in doc.hidden_parts, "the new twin D is hidden")
    doc_b_z[0] = B_AT[2] + 6
    on_b()
    say(ENGINE, "twin D now in B's old place: hidden flag and extrude stayed on B (extrude ends at z 46), D is shown")

    # ---- 5. B moves sideways: the projection and the concentric circle follow
    a.move_instance(iids["B"], A.translation((10, 0, 0)), solve=False)
    T.check(w.folder.context_status(plate.id, cid) == "out of date", "moving B did not mark the Context out of date")
    update(w, doc, cid)
    pr, pc = centres()
    want = (B_AT[0] + 10, B_AT[1])
    T.check(np.allclose(pr, want, atol=TOL) and np.allclose(pc, want, atol=TOL), f"centres {pr} {pc}, want {want}")
    on_b()
    say(ENGINE, f"wheel B moved 10 mm: the projected rim and the concentric circle followed to {want}")

    run_bar(app, w, plate, asm, doc, cid, iids, pid)
    return fd


def sketch_in_window(w, doc, build, plane=TOP):
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=plane, sketch=s.to_dict())
    doc.add_feature(f)
    settle(w)
    T.check(not doc.errors.get(f["id"]), doc.errors.get(f["id"]))
    return f["id"]


def run_bar(app, w, plate, asm, doc, cid, iids, pid):
    show(w, doc)
    bar, vp = w.context_bar, w.viewport
    bar.refresh()
    T.check(not bar.isHidden(), "the Context bar is not shown over a Part Studio with a Context")
    T.check(bar.label.text() == "Context of Asm" and bar.status.text() == "current",
            f"label {bar.label.text()!r}, status {bar.status.text()!r}")
    say(ENGINE, "bar: shown, label 'Context of Asm', status 'current'")
    a = asm.doc
    a.move_instance(iids["C"], A.translation((0, 0, 10)), solve=False)
    bar.refresh()
    T.check(bar.status.text() == "out of date" and bar.update_btn.property("outOfDate") is True, bar.status.text())
    bar.update_context()
    settle(w)
    T.check(bar.status.text() == "current" and not doc.errors, (bar.status.text(), doc.errors))
    T.check(abs(zmin(doc, pid["C"]) - 70) < TOL, zmin(doc, pid["C"]))
    say(ENGINE, "bar: status goes 'out of date' when C moves; Update context makes it current, C is at z 70")

    T.check(bar.go_to_assembly() is True and w.doc is a, "Go to assembly did not open the Assembly")
    show(w, doc)
    say(ENGINE, "bar: Go to assembly opens the Assembly tab")

    bar.set_context_hidden(True)
    settle(w)
    T.check(cid in doc.hidden_features and bar.eye.isChecked(), "the eye did not hide the Context")
    shown = {p for p, _m, _c in vp.parts}
    T.check(not (shown & {pid["B"], pid["C"], pid["D"]}), f"hidden Context bodies still drawn: {sorted(shown)}")
    T.check(doc.hidden_parts == {pid["B"]}, "hiding the whole Context changed the hand-hidden instances")
    bar.set_context_hidden(False)
    settle(w)
    shown = {p for p, _m, _c in vp.parts}
    T.check(cid not in doc.hidden_features and {pid["C"], pid["D"]} <= shown and pid["B"] not in shown,
            f"after showing the Context: {sorted(shown)}")
    say(ENGINE, "bar: the eye hides every Context body and shows them again; B stays hidden by hand")

    bar.show_instances()
    T.check(set(bar.popup.rows) == {pid["B"], pid["C"], pid["D"]}, sorted(bar.popup.rows))
    T.check(bar.popup.rows[pid["B"]].isChecked() and not bar.popup.rows[pid["C"]].isChecked(), "popup eyes")
    bar.set_instance_hidden(pid["C"], True)
    settle(w)
    T.check(doc.hidden_parts == {pid["B"], pid["C"]}, doc.hidden_parts)
    T.check(pid["C"] not in {p for p, _m, _c in vp.parts}, "the hidden instance C is still drawn")
    bar.set_instance_hidden(pid["C"], False)
    settle(w)
    T.check(doc.hidden_parts == {pid["B"]} and pid["C"] in {p for p, _m, _c in vp.parts}, doc.hidden_parts)
    bar.popup.hide()
    say(ENGINE, "bar: the instance list hides and shows one body (C) without touching the others")

    bar.set_select_through(False)
    T.check(vp.select_through is False and not bar.through.isChecked(), "Select through off did not reach the viewport")
    bar.set_select_through(True)
    T.check(vp.select_through is True and bar.through.isChecked(), "Select through on did not reach the viewport")
    say(ENGINE, "bar: the Select through checkbox switches the viewport option both ways")

    settle(w)
    version = vp._batch_version
    bar.set_alpha(40)
    T.check(abs(vp.context_alpha - 0.4) < 1e-9 and bar.slider.value() == 40, vp.context_alpha)
    bar.set_alpha(0)
    T.check(vp.context_alpha == 0.0, vp.context_alpha)
    T.check(vp._batch_version == version and w.studio_engine.idle(),
            f"the slider rebuilt the model (batch {version} -> {vp._batch_version})")
    bar.set_alpha(100)
    say(ENGINE, "bar: the slider sets the Context alpha (0.4, 0) and the model batch is not rebuilt")


def run_old_file(app, w, path):
    """7. A Document saved before this run: positional ids, no ids key."""
    fd, plate, wheel, asm, iids = build_document(path, None)
    doc, a = plate.doc, asm.doc
    key = doc.store_context(A.capture_context(a, iids["P"]))
    ctx = add(doc, "context", assembly=a.uid, instance=iids["P"], capture=key)       # no "ids": as saved before I6.1
    cid = ctx["id"]
    b = f"{cid}/2"                                                                  # B is the second neighbour
    T.check("ids" not in doc.feature(cid)["params"] and abs(zmin(doc, b) - B_AT[2]) < TOL, "old Context rig")
    doc.hidden_parts = {b}
    doc.set_part_colors({b: RED})
    sid = sketch_feature(doc, lambda k: rect_lines(k, 2, 2, 2, 2))
    eid = add(doc, "extrude", regions=region_refs(doc, sid), end="up_to_face", op="new",
              up_to={"kind": "face", "ref": bottom_ref(doc, b)})["id"]
    T.check(not doc.errors and abs(bar_top(doc, eid) - B_AT[2]) < TOL, doc.errors)
    fd.save()
    before = {i: (K.volume(p.shape), tuple(np.round(K.bbox(p.shape)[0], 6))) for i, p in doc.state.parts.items()}
    fd2 = DF.FolderDocument.open(fd.path)
    d2 = fd2.tab(plate.id).doc
    again = {i: (K.volume(p.shape), tuple(np.round(K.bbox(p.shape)[0], 6))) for i, p in d2.state.parts.items()}
    T.check(not d2.errors and again == before and "ids" not in d2.feature(cid)["params"], d2.errors)
    T.check(d2.hidden_parts == {b} and sorted(d2.state.parts) == sorted(doc.state.parts), d2.hidden_parts)
    say(FILES, "an old file (positional ids, no ids key) reopens with the same parts and flags "
               "(file read only: nothing in a window is needed to load it)")
    fd2 = None

    fd = open_folder(w, fd.path)
    plate, asm = fd.tab(plate.id), fd.tab(asm.id)
    doc, a = plate.doc, asm.doc
    show(w, doc)
    refs = {i for i, p in doc.state.parts.items() if p.reference}
    T.check(refs == {f"{cid}/{k}" for k in (1, 2, 3)}, f"the old Context's bodies are {sorted(refs)}")
    T.check("ids" not in doc.feature(cid)["params"] and doc.hidden_parts == {b}, "the old Context was converted at load")
    T.check(abs(zmin(doc, b) - B_AT[2]) < TOL and abs(bar_top(doc, eid) - B_AT[2]) < TOL, "B moved in the window")
    T.check(w.viewport.reference_ids == refs - {b}, w.viewport.reference_ids)       # B is hidden: not drawn
    say(ENGINE, "old file in the window: Context bodies still <fid>/1..3, marked as Context, nothing converted")

    a.delete_instances([iids["A"]])
    update(w, doc, cid)
    pb = f"{cid}/{iids['B']}"
    T.check(doc.feature(cid)["params"].get("ids") == "instance", doc.feature(cid)["params"])
    T.check(doc.hidden_parts == {pb} and set(doc.part_overrides) == {pb}, (doc.hidden_parts, set(doc.part_overrides)))
    T.check(b not in doc.state.parts and pb in doc.state.parts and abs(zmin(doc, pb) - B_AT[2]) < TOL, sorted(doc.state.parts))
    T.check(abs(bar_top(doc, eid) - B_AT[2]) < TOL and doc.feature(eid)["params"]["up_to"]["ref"]["part"] == pb,
            "the extrude left B")
    T.check(f"{cid}/{iids['C']}" not in doc.hidden_parts, "C took over B's hidden flag")
    say(ENGINE, "first Update context after deleting A converts the old Context: ids instance, hidden flag, "
                "colour and extrude moved to B's new id")


def run():
    app, w = new_window()
    try:
        run_new_context(app, w)
    finally:
        T.close_engine_window(w)
    app, w = new_window()
    try:
        run_old_file(app, w, os.path.join(TMP, "Old"))
    finally:
        T.close_engine_window(w)
    T.say("CONTEXT SMOKE OK")


if __name__ == "__main__":
    try:
        run()
    except T.Fail as e:
        print(f"CONTEXT SMOKE FAILED: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        print("CONTEXT SMOKE FAILED: unexpected error", file=sys.stderr, flush=True)
        sys.exit(1)
