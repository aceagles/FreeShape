"""Mate connector smoke (slice S3): connectors in Part Studios, in the published output, in Derived and in Assemblies.

Gate script of the Top-Down Design plan (helpers in topdown_common.py). A Document of Part Studios Skeleton, Base
(derives Skeleton's Layout sketch, extrudes it into a filleted plate with a hole) and Lid (a block, plus Base's plate
and one of its connectors brought in with Derived), and two Assemblies of Base's plate and Lid's block. The Document
is built headless, saved and opened with open_document, so edits reach the Assemblies. Then, in the offscreen window:

  * the Mate connector tool (the mate_connector palette command, then a pick) puts an edge connector on Base's plate
    and a face connector on Lid's block; Lid's Derived brings Base's connector in;
  * Assembly Asm mates the two studio connectors; moving Base's hole into a corner fillet leaves the connector frame,
    the mate and the mated instance where they were;
  * Lid publishes the derived connector at the source's frame;
  * deleting Lid's connector turns the mate red, saying what is missing;
  * "Mate connector at origin" (Part Studio) and "Add mate connector to instance origin" (Assembly) are run from their
    context-menu entries; a mate on two instance origins puts the origins together.

The connector in the hole-into-fillet check is an EDGE connector: a face-centre connector legitimately moves about
0.02 mm when a hole becomes a fillet (see tests/test_connector_frames.py). The Mate connector dialog is driven by
the same call a viewport click makes (handle_pick); drawing cannot be checked offscreen. A Derived dialog needs a
Version pick, so the Derived feature is added through the Document API.

    both fs_py scripts/smoke/st3_connectors.py
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

TMP = T.isolate("st3_connectors_")

import numpy as np  # noqa: E402
from conftest import add, block_doc, edge_refs, line_along, only_part, region_refs, sketch_feature  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.ui import assembly_ui as AU  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, move_hole, plate_studio  # noqa: E402

LAYOUT = "F1"


def fvec(fr):
    return {k: np.array(fr[k], float) for k in ("o", "z", "x")}


def show(tab_doc, w, app):
    """Make `tab_doc`'s tab the current one."""
    w.tabs.setCurrentIndex(w._tab_index(tab_doc))
    app.processEvents()
    T.check(w.doc is tab_doc or getattr(w, "asm", None) is not None, "tab did not open")


def tool_connector(w, doc, kind, key):
    """Add a connector with the Mate connector tool: the palette command, a pick, OK. Returns the feature."""
    T.run_command(w, "mate_connector")
    T.check(w.mode == "dialog" and w.dialog.ftype == "mate_connector", "the Mate connector tool did not open")
    w.dialog.handle_pick(Pick(kind, key))
    T.check(w.dialog.feature["params"].get("ref"), "the pick gave the connector no reference")
    w.dialog._timer.stop()      # a preview timer left running would fire after the next tab switch
    w.commit_dialog()
    f = [f for f in doc.features if f["type"] == "mate_connector"][-1]
    T.check(not doc.errors, doc.errors)
    T.check(f["id"] in doc.state.connectors, "the connector was not published into the model state")
    return f


def side_edge(part):
    """Index of the top edge along Y at x = 0: its straight part and midpoint stay put when a hole cuts a corner."""
    for i, e in enumerate(K.edges(part.shape)):
        info = K.edge_info(e)
        if (info["type"] == int(K.GeomAbs_Line) and abs(abs(info["d"][1]) - 1) < 1e-6
                and abs(info["c"][0]) < 1e-6 and abs(info["c"][2] - H) < 1e-6):
            return i
    raise T.Fail("no edge along Y at x = 0 on the top of the plate")


def bottom_face(part):
    return next(i for i, f in enumerate(K.faces(part.shape)) if (K.face_info(f).get("n") or [0, 0, 0])[2] < -0.9)


def run():
    app, w = T.new_window(TMP)

    # ---- the Document, built headless: Skeleton (Layout sketch), Base (plate, fillets, hole), Lid (block), 2 Assemblies
    fd = DF.FolderDocument.create(os.path.join(TMP, "Doc"), name="Doc")
    sk = fd.add_tab("studio", "Skeleton")
    sk.doc.__dict__.update(plate_studio()[0].__dict__)
    base = fd.add_tab("studio", "Base")
    lid = fd.add_tab("studio", "Lid")
    lid.doc.__dict__.update(block_doc(40, 20, 3).__dict__)
    for t in (sk, lid):
        fd._attach(t)
    drv = add(base.doc, "derived", source=L.studio_source(sk), parts=[], sketches=[LAYOUT], connectors=[])
    add(base.doc, "extrude", regions=region_refs(base.doc, f"{drv['id']}:{LAYOUT}"), depth=H, op="new")
    add(base.doc, "fillet", edges=edge_refs(only_part(base.doc), line_along([0, 0, 1]), expect=4), radius=5.0)
    hole_sk = sketch_feature(base.doc, lambda s: s.add_point_entity(s.add_point(8.0, 8.0)))
    add(base.doc, "hole", sketch=hole_sk, standard="custom", diameter=4.0, end="through", style="simple")
    T.check(not base.doc.errors, base.doc.errors)
    asm_ids = {}
    plate_id = only_part(base.doc).id
    block_id = f"{lid.doc.features[1]['id']}/1"
    for name in ("Asm", "Asm2"):
        asm = fd.add_tab("assembly", name)
        asm_ids[name] = asm.id
        asm.doc.insert(L.studio_source(base, plate_id), name="Base")
        asm.doc.insert(L.studio_source(lid, block_id), name="Lid")
        asm.doc.refresh(solve=False, notify=False)
        T.check(not asm.doc.errors, asm.doc.errors)
    fd.save()
    path = fd.path
    fd = w.open_document(path)
    T.check(fd is not None and not fd.errors, fd and fd.errors)
    sk, base, lid = (fd.tab(t.id) for t in (sk, base, lid))
    asm, asm2 = fd.tab(asm_ids["Asm"]), fd.tab(asm_ids["Asm2"])
    plate_id = only_part(base.doc).id

    # ---- the Mate connector tool: an edge connector on Base's plate, a face connector on Lid's block
    show(base.doc, w, app)
    cb = tool_connector(w, base.doc, "edge", (plate_id, side_edge(only_part(base.doc))))
    show(lid.doc, w, app)
    cl = tool_connector(w, lid.doc, "face", (block_id, bottom_face(lid.doc.state.parts[block_id])))
    T.check(base.doc.state.connectors[cb["id"]]["part"] == plate_id, "Base's connector has the wrong owner")

    # ---- Lid brings Base's connector in with Derived (added through the Document API: its dialog needs a Version)
    dl = add(lid.doc, "derived", source=L.studio_source(base), parts=[plate_id], sketches=[], connectors=[cb["id"]])
    T.check(not lid.doc.errors, lid.doc.errors)
    key = f"{dl['id']}:{cb['id']}"

    # ---- Assembly Asm: Base fixed, Lid mated to Base's connector by Lid's own connector
    show(asm.doc, w, app)
    a = asm.doc
    ip, il = (i["id"] for i in a.instances)
    a.set_fixed(ip, True)
    m = a.make_mate("fastened")
    m["a"] = a.named_connector(ip, cb["id"])
    m["b"] = a.named_connector(il, cl["id"])
    a.add_mate(m)
    T.check(not a.errors, a.errors)
    T_before = np.array(a.T(il), float)
    frame_before = fvec(base.doc.state.connectors[cb["id"]]["frame"])
    T.check(np.allclose(fvec(a.mates[0]["a"]["frame"])["o"], frame_before["o"], atol=1e-9), "mate end is not the connector")

    # ---- Base: the hole moves into a corner fillet

    shape_before = only_part(base.doc).shape
    move_hole(base.doc, hole_sk, (2.5, 2.5))
    fd.refresh_links()
    app.processEvents()
    T.check(not only_part(base.doc).shape.IsSame(shape_before), "the edit did not change the plate")
    frame_after = fvec(base.doc.state.connectors[cb["id"]]["frame"])
    d_o = float(np.linalg.norm(frame_after["o"] - frame_before["o"]))
    x_dot = float(frame_after["x"] @ frame_before["x"])
    T.check(d_o < 1e-6 and x_dot > 0.999, f"origin moved {d_o:g}, X dot {x_dot:g}")
    T.say(f"connector frame after hole-into-fillet: origin moved {d_o:.1e} mm, X dot {x_dot:.6f} (edge connector)")

    T.check(not a.errors, a.errors)
    T.check(not lid.doc.errors and not base.doc.errors, (lid.doc.errors, base.doc.errors))
    T.check(a.mates[0]["id"] not in a.errors, "the mate went red")
    moved = float(np.abs(np.array(a.T(il), float) - T_before).max())
    T.check(moved < 1e-6, f"the mated instance moved by {moved:g}")
    T.say(f"mate on studio connectors: green before and after, transform unchanged (max change {moved:.1e})")

    # ---- Derived: Lid publishes Base's connector at the same frame
    got = lid.doc.state.connectors[key]
    now = base.doc.state.connectors[cb["id"]]["frame"]
    T.check(got["part"] == f"{dl['id']}/1", got["part"])
    T.check(all(np.allclose(got["frame"][k], now[k], atol=1e-9) for k in ("o", "z", "x")), "frame differs from the source")
    pub = fd.resolver.resolve_connectors(L.studio_source(lid, part=f"{dl['id']}/1"))
    T.check(key in [c["id"] for c in pub], [c["id"] for c in pub])
    T.say("derived connector: published by Lid at the same frame")

    # ---- Lid's own connector deleted: the mate goes red and says so
    lid.doc.delete_features({cl["id"]})
    fd.refresh_links()
    msg = a.errors.get(m["id"], "")
    T.check("no longer publishes" in msg and cl["name"] in msg, msg or "the mate stayed green")
    T.say(f"connector deleted: mate red with the message: {msg}")

    # ---- Mate connector at origin: the Part Studio entry, then two instance origins in Asm2
    show(base.doc, w, app)
    T.run_menu_action(lambda: w._tree_context("origin:None", w.pos()), "mate_connector_at_origin", [MW])
    T.check(w.mode == "dialog" and w.dialog.ftype == "mate_connector", "the origin entry did not open the dialog")
    T.check(w.dialog.feature["params"]["owner"] == plate_id, "the one part was not made the owner")
    w.commit_dialog()
    fo = [f for f in base.doc.features if f["type"] == "mate_connector"][-1]
    co = base.doc.state.connectors[fo["id"]]
    T.check(not base.doc.errors and co["part"] == plate_id and np.allclose(co["frame"]["o"], 0), co)

    show(asm2.doc, w, app)
    b = asm2.doc
    ip, il = (i["id"] for i in b.instances)
    for iid in (ip, il):
        T.run_menu_action(lambda iid=iid: w.asm.instance_menu(iid, w.pos()), "add_connector_instance_origin", [AU])
    T.check(len(b.connectors) == 2, b.connectors)
    b.set_fixed(ip, True)
    shift = np.eye(4)
    shift[:3, 3] = [30, -20, 15]
    b.instance(il)["T"] = shift.tolist()
    m2 = b.make_mate("fastened")
    m2["a"] = b.named_connector(ip, next(c["id"] for c in b.connectors if c["inst"] == ip))
    m2["b"] = b.named_connector(il, next(c["id"] for c in b.connectors if c["inst"] == il))
    m2["flip"] = True               # the default opposes the two Z axes; flip aligns them
    b.add_mate(m2)
    T.check(not b.errors, b.errors)
    oa, ob = (b.world_frame(m2[s])["o"] for s in ("a", "b"))
    gap = float(np.linalg.norm(np.array(oa) - np.array(ob)))
    T.check(gap < 1e-6, f"origins {gap:g} mm apart")
    T.say(f"connector at origin: studio origin owned by plate; mate on two instance origins solved, origins coincident "
          f"({gap:.1e} mm apart)")


T.run_smoke("ST3", TMP, run)
