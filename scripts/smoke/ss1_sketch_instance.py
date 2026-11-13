"""Sketch instance smoke (slice S1): a layout sketch in an Assembly, end to end.

Gate script of the Sketch Instances plan (helpers in topdown_common.py). A Document of a Part Studio "Layout" (a
layout sketch: two construction lines and a free point, plus a block) and an Assembly. In the offscreen window:

  * the Mate connector tool (the mate_connector command and its dialog) puts a connector on a line's midpoint and an
    origin connector owned by the sketch; the Assembly takes the sketch and the block through the controller's insert;
  * the sketch's origin connector is fastened to a fixed Anchor block; a block is mated to the free point (a real
    mouse click on the point, then direct calls) and a second block to the sketch's midpoint connector;
  * editing the sketch (replace_feature, as the sketch editor commits) moves both blocks with the geometry;
  * deleting the free point gives the mate the error "Lost its sketch geometry ..."; undo clears it;
  * a Context captured from the first block lists the sketch as a neighbour, and one from the sketch is refused;
  * a Version is created, the Document reopened (and the Version opened), and every pose is the same.

Deleting the point is done through the Document model (delete the entity in the sketch and replace_feature), not the
UI's delete command, which would open a confirm dialog. Drawing cannot be checked offscreen.

    both fs_py scripts/smoke/ss1_sketch_instance.py
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

TMP = T.isolate("ss1_sketch_instance_")

import numpy as np  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import sketchref  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from tests.test_assembly import face  # noqa: E402

SHIFT = 7.0


def poses(a, ids):
    return {i: np.array(a.T(i), float) for i in ids}


def max_diff(p, q):
    return max(float(np.abs(p[i] - q[i]).max()) for i in p)


def sync(fd, a, app):
    """What the app does after a Part Studio edit: re-read the links (no solve), then the open Assembly refreshes
    and solves (partstudio_engine calls refresh(notify=True) on it)."""
    fd.refresh_links()
    a.refresh()
    app.processEvents()


def run():
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, w = T.new_window(TMP)

    # ---- the Document: Part Studio Layout, an empty Assembly
    fd = DF.FolderDocument.create(os.path.join(TMP, "Doc"), name="Doc")
    st = fd.add_tab("studio", "Layout")
    doc, sid, pid = T.layout_studio()
    st.doc.__dict__.update(doc.__dict__)
    fd._attach(st)
    asm = fd.add_tab("assembly", "Asm")
    fd.save()
    path = fd.path
    fd = w.open_document(path)
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    st, asm = fd.tab(st.id), fd.tab(asm.id)
    doc, a = st.doc, asm.doc
    ents = doc.state.sketches[sid].sketch.entities
    line = next(e for e, v in ents.items() if v["type"] == "line")
    free = next(e for e, v in ents.items() if v["type"] == "point")

    # ---- Part Studio: connector on a line's midpoint, origin connector owned by the sketch (the Mate connector tool)
    w.tabs.setCurrentIndex(w._tab_index(doc))
    app.processEvents()
    T.run_command(w, "mate_connector")
    T.check(w.mode == "dialog" and w.dialog.ftype == "mate_connector", "the Mate connector tool did not open")
    res = doc.state.sketches[sid]
    plane = {"origin": res.plane.origin, "xdir": res.plane.xdir, "normal": res.plane.normal}
    mid_o = sketchref.frame(plane, res.sketch.to_dict(), line, "mid")["o"]
    sxy = w.viewport.cam.to_screen(np.asarray(mid_o, float))
    w.viewport.last_click = (float(sxy[0]), float(sxy[1]))          # where the click was: picks the "mid" role
    w.dialog.handle_pick(Pick("sketch_line", (sid, line)))
    ref = w.dialog.feature["params"]["ref"]
    T.check(ref.get("sketch") == sid and ref.get("entity") == line and ref.get("role") == "mid", ref)
    w.dialog._timer.stop()
    w.commit_dialog()
    cmid = [f for f in doc.features if f["type"] == "mate_connector"][-1]
    T.check(not doc.errors and doc.state.connectors[cmid["id"]].get("sketch") == sid, doc.errors)
    T.check(np.allclose(doc.state.connectors[cmid["id"]]["frame"]["o"], mid_o, atol=1e-9), "mid connector off the midpoint")
    T.say(f"Part Studio: connector on the line midpoint, owned by the sketch (frame at {np.round(mid_o, 3).tolist()})")

    T.run_command(w, "mate_connector")
    w.dialog.feature["params"].update(ref={"origin": True}, owner=sid, name="Sketch origin")
    w.dialog._timer.stop()
    w.commit_dialog()
    cor = [f for f in doc.features if f["type"] == "mate_connector"][-1]
    T.check(not doc.errors and doc.state.connectors[cor["id"]].get("sketch") == sid, doc.errors)
    T.check(np.allclose(doc.state.connectors[cor["id"]]["frame"]["o"], plane["origin"], atol=1e-9), "origin connector off the origin")
    T.say("Part Studio: origin connector owned by the sketch, at the Part Studio origin frame")
    fd.refresh_links()

    # ---- Assembly: insert Anchor (a fixed block), the sketch and two more blocks, through the controller
    w.tabs.setCurrentIndex(w._tab_index(a))
    app.processEvents()
    ctl = w.asm
    ctl.insert_studio_parts(doc.uid, [pid])
    i_anchor = a.instances[-1]["id"]
    i_sk = ctl.insert_studio_sketch(doc.uid, sid)
    ctl.insert_studio_parts(doc.uid, [pid])
    i_b1 = a.instances[-1]["id"]
    ctl.insert_studio_parts(doc.uid, [pid])
    i_b2 = a.instances[-1]["id"]
    T.check(len(a.instances) == 4 and a.is_sketch_instance(i_sk) and a.instance(i_anchor)["fixed"], a.instances)
    T.check(not a.is_sketch_instance(i_b1), "a block counted as a Sketch instance")
    pcs = {c["name"]: c["id"] for c in a.published_connectors(i_sk)}
    T.check(set(pcs) >= {"Sketch origin", cmid["name"]}, f"the sketch instance publishes {sorted(pcs)}")
    T.say(f"Assembly: Anchor (fixed), Sketch instance and two blocks inserted; the sketch publishes {sorted(pcs)}")
    app.processEvents()

    def block_face(iid, sign):
        return a.connector(iid, "face", face(a, iid, lambda f: f.get("n") and f["n"][2] * sign > 0.9))

    # ---- fasten the sketch's origin connector to the Anchor's top face
    ctl.start_mate("fastened")
    d = ctl.mate_dialog
    d.pick_named(i_sk, pcs["Sketch origin"])
    d.mate["b"] = block_face(i_anchor, 1)
    d.accept()
    app.processEvents()
    T.check(len(a.mates) == 1 and not a.errors, a.errors)
    T.check(np.allclose(a.world_frame(a.mates[0]["a"])["o"], a.world_frame(a.mates[0]["b"])["o"], atol=1e-6), "origin not fastened")
    T.say("mate 1: the sketch's origin connector fastened to the Anchor; the sketch moved onto it")

    # ---- mate B1 to the free point: a real click on the point, then the block's face
    ctl.show_scene()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    ctl.start_mate("fastened")
    d = ctl.mate_dialog
    vp = w.viewport
    sk_res = next(sr for fid, sr, _m in vp.sketches if fid == i_sk)
    from freeshape.sketchref import frame as sk_frame
    pl = {"origin": sk_res.plane.origin, "xdir": sk_res.plane.xdir, "normal": sk_res.plane.normal}
    world_pt = np.asarray(sk_frame(pl, sk_res.sketch.to_dict(), free, "point")["o"], float)
    # the sketch's drawn position is its plane moved by the instance's transform
    sxy = vp.cam.to_screen(world_pt)
    vp._hover_xy = (float(sxy[0]), float(sxy[1]))
    vp._do_hover()
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(int(round(sxy[0])), int(round(sxy[1]))))
    app.processEvents()
    c = d.mate.get("a")
    T.check(c and c["kind"] == "sketch" and c["entity"] == free and c["role"] == "point" and c["inst"] == i_sk,
            f"the click on the free point gave {c}")
    d.mate["b"] = block_face(i_b1, -1)
    d.accept()
    app.processEvents()
    m1 = a.mates[-1]
    T.check(not a.errors, a.errors)
    T.check(np.allclose(a.world_frame(m1["a"])["o"], a.world_frame(m1["b"])["o"], atol=1e-4), "B1 not on the free point")
    T.say("mate 2: B1 fastened to the free point (picked with a real click on the point)")

    # ---- B2 on the sketch-owned midpoint connector
    ctl.start_mate("fastened")
    d = ctl.mate_dialog
    d.pick_named(i_sk, pcs[cmid["name"]])
    d.mate["b"] = block_face(i_b2, -1)
    d.accept()
    app.processEvents()
    m2 = a.mates[-1]
    T.check(not a.errors, a.errors)
    T.check(np.allclose(a.world_frame(m2["a"])["o"], a.world_frame(m2["b"])["o"], atol=1e-4), "B2 not on the connector")
    T.say("mate 3: B2 fastened to the sketch-owned midpoint connector")

    ids = [i["id"] for i in a.instances]
    before = poses(a, ids)

    # ---- edit the sketch: the free point and one end of the line move SHIFT mm along the sketch's X
    f = copy.deepcopy(doc.feature(sid))
    sk = f["params"]["sketch"]
    pt_id = sk["entities"][free]["p"]
    end_id = sk["entities"][line]["p2"]
    for q in (pt_id, end_id):
        sk["points"][q] = [sk["points"][q][0] + SHIFT, sk["points"][q][1]]
    doc.replace_feature(f)
    T.check(not doc.errors, doc.errors)
    sync(fd, a, app)
    T.check(not a.errors, a.errors)
    after = poses(a, ids)
    xdir = np.asarray(doc.state.sketches[sid].plane.xdir, float)
    xw = a.T(i_sk)[:3, :3] @ xdir
    for name, iid, want in (("B1", i_b1, SHIFT), ("B2", i_b2, SHIFT / 2)):
        got = after[iid][:3, 3] - before[iid][:3, 3]
        T.check(abs(float(np.linalg.norm(got)) - want) < 1e-4 and np.allclose(got, want * xw, atol=1e-4),
                f"{name} moved {np.round(got, 4).tolist()}, wanted {want:g} mm along {np.round(xw, 3).tolist()}")
    T.check(max_diff({i: before[i] for i in (i_anchor, i_sk)}, {i: after[i] for i in (i_anchor, i_sk)}) < 1e-9,
            "the Anchor or the sketch instance moved")
    T.say(f"sketch edited: B1 moved {SHIFT:g} mm and B2 {SHIFT / 2:g} mm along the sketch's X, Anchor and sketch stayed")
    edited = poses(a, ids)

    # ---- delete the free point (model path: no confirm dialog): the mate says so; undo brings it back
    f = copy.deepcopy(doc.feature(sid))
    sk = f["params"]["sketch"]
    del sk["entities"][free]
    sk["points"].pop(pt_id, None)
    doc.replace_feature(f)
    sync(fd, a, app)
    msg = a.errors.get(m1["id"], "")
    T.check(msg.startswith("Lost its sketch geometry"), f"the mate error is {msg!r}")
    T.check(m1["id"] in [m["id"] for m in a.mates] and not a.errors.get(m2["id"]), (a.errors, "the mate was dropped"))
    T.say(f"free point deleted: mate 2 stays, red with: {msg}")
    doc.undo()
    sync(fd, a, app)
    T.check(free in doc.state.sketches[sid].sketch.entities, "undo did not bring the point back")
    T.check(not a.errors and max_diff(edited, poses(a, ids)) < 1e-4, (a.errors, "undo did not restore the poses"))
    T.say("undo: the point is back, the error cleared, every pose as before")

    # ---- Context from B1 lists the sketch as a neighbour; one from the sketch is refused
    cap = A.capture_context(a, i_b1)
    T.check(i_sk in [n["id"] for n in cap["neighbours"]], [n["id"] for n in cap["neighbours"]])
    refused = False
    try:
        A.capture_context(a, i_sk)
    except Exception as e:                                  # noqa: BLE001
        refused = "belongs to a part" in str(e)
    T.check(refused, "a Context taken from a Sketch instance was not refused")
    T.say(f"Context from B1: neighbours {[n['name'] for n in cap['neighbours']]} include the sketch; from the sketch: refused")

    # ---- Version, reopen: the same poses
    fd.save()
    asm_id = asm.id
    final = poses(a, ids)
    v = fd.create_version("v1", "layout")
    T.check(v["name"] == "v1", v)
    w.folder = None
    fd2 = w.open_document(path)             # the way a user reopens it
    T.check(fd2 is not None and not fd2.errors, fd2 and fd2.errors)
    a2 = fd2.tab(asm_id).doc
    a2.refresh()
    T.check(not a2.errors and [i["id"] for i in a2.instances] == ids, a2.errors)
    d2 = max_diff(final, poses(a2, ids))
    T.check(d2 < 1e-6, f"reopened poses differ by {d2:g}")
    view = fd2.open_version("v1")
    a3 = view.tab(asm_id).doc
    a3.refresh()
    d3 = max_diff(final, poses(a3, ids))
    T.check(not a3.errors and d3 < 1e-6, f"Version poses differ by {d3:g} ({a3.errors})")
    T.check(len(a2.mates) == 3 and a2.is_sketch_instance(i_sk), "mates or the Sketch instance lost on reopen")
    view.close()
    T.say(f"Version v1 created, Document reopened: poses the same (max difference {d2:.1e}; the Version {d3:.1e})")


T.run_smoke("SKETCH INSTANCE", TMP, run)
