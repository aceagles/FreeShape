"""Derived smoke (slice S1): three Part Studios tied by Derived features, one unsaved edit at the top.

Gate script of the Top-Down Design plan (helpers in topdown_common.py). A Document with Part Studios Skeleton
(a Layout sketch and a block), Base, Lid and Pin. In an offscreen `MainWindow(engine=True)` the script makes the
Derived features through the command search (`run_command_with`): Base derives the Layout sketch and extrudes it
into a plate; Lid derives the Layout sketch and Base's plate (one Derived takes one source, so two); Pin derives
Skeleton's block pinned to Version v1 of this Document. It then checks the claim of the slice:

  * one unsaved edit to Skeleton reaches Base and Lid, each with one plate, for both tab orders S,B,L and S,L,B,
    with no cycle error (the tab orders run on the process engine, as an engine opens tabs in the order given);
  * a fillet on a derived edge is found by name after the source changes (recovered stays false);
  * a source whose part is removed turns the Derived red with the message;
  * two Part Studios that derive each other are reported as a loop, and the call returns;
  * a Derived pinned to v1 keeps v1 after the source edit, reports v2 as an update, and follows v2 once updated;
  * a tab whose worker was killed and restarted still receives an upstream edit;
  * two fillets on the two pieces of a severed solid both survive the split.

Steps that need a viewport pick (the extrude of a derived sketch region, the fillet's edge, the severing hole) use
the Document API. Nothing drawn can be checked offscreen.

    both fs_py scripts/smoke/st1_derived.py

Entry points that start a ProcessEngine need the `__main__` guard: a worker imports this file again.
"""
import copy
import os
import signal
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

import topdown_common as T  # noqa: E402

TMP = T.isolate("st1_derived_") if __name__ == "__main__" else None

from conftest import add, line_along, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.engine import LinkCycleError, ProcessEngine  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

LAYOUT = "F1"           # Skeleton's Layout sketch
BLOCK = "F2/1"          # Skeleton's block
NAMES = ("Skeleton", "Base", "Lid", "Pin")


def layout_sketch(width):
    s = Sketch()
    rect_lines(s, 0, 0, width, 10)
    s.solve()
    return s.to_dict()


def set_width(doc, width):
    """An edit to Skeleton's Layout sketch, as the sketch editor would commit it."""
    f = copy.deepcopy(doc.feature(LAYOUT))
    f["params"]["sketch"] = layout_sketch(width)
    doc.replace_feature(f)
    T.check(not doc.errors, doc.errors)


def set_depth(doc, fid, depth):
    f = copy.deepcopy(doc.feature(fid))
    f["params"]["depth"] = depth
    doc.replace_feature(f)


def volumes(doc):
    return sorted(round(K.volume(p.shape), 4) for p in doc.state.parts.values())


def derived_of(doc):
    return [f for f in doc.features if f["type"] == "derived"]


def derived_regions(doc, key):
    """Region refs of a derived sketch. The window's `doc.state` holds no derived sketches, so build in-process."""
    st = doc.state_before(len(doc.features))
    T.check(key in st.sketches, f"no derived sketch {key}: {list(st.sketches)}")
    return [F.region_ref(st, key, r) for r in st.sketches[key].regions]


def build_document(path):
    """Skeleton (Layout sketch + block 20 x 10 x 5), empty Base, Lid and Pin, and Version v1. Returns the tab ids."""
    fd = DF.FolderDocument.create(path, name="Doc", registry=R.default_registry())
    sk = fd.add_tab("studio", "Skeleton")
    T.check(sketch_feature(sk.doc, lambda s: rect_lines(s, 0, 0, 20, 10)) == LAYOUT, "Layout is not F1")
    add(sk.doc, "extrude", regions=region_refs(sk.doc, LAYOUT), depth=5, op="new")
    T.check(list(sk.doc.state.parts) == [BLOCK], list(sk.doc.state.parts))
    for n in NAMES[1:]:
        fd.add_tab("studio", n)
    fd.create_version("v1", "first")
    ids = {t.name: t.id for t in fd.tabs}
    fd.close()
    return ids


def window_phase(path, ids):
    """Everything the window can do. Returns {"plate": id of Base's plate part}."""
    app, w = T.new_engine_window(TMP)
    try:
        fd = w.open_document(path)
        T.check(fd is not None and not fd.errors, fd and fd.errors)
        T.settle(w, 240)
        doc = {n: fd.tab(ids[n]).doc for n in NAMES}

        def goto(name):
            w.tabs.setCurrentIndex(w._tab_index(doc[name]))
            T.settle(w, 60)
            T.check(w.doc is doc[name], f"the window did not switch to {name}")

        # ---- Base: Derived of the Layout sketch (command search), then a plate from its region (Document API)
        goto("Base")
        T.run_command_with(w, "derived", {"source": ids["Skeleton"], "sketches": [LAYOUT]})
        T.settle(w)
        (d_base,) = derived_of(doc["Base"])
        T.check(d_base["params"]["source"]["tab"] == ids["Skeleton"] and not doc["Base"].errors, doc["Base"].errors)
        plate = add(doc["Base"], "extrude", regions=derived_regions(doc["Base"], f"{d_base['id']}:{LAYOUT}"),
                    depth=3, op="new")
        T.settle(w)
        plate_id = f"{plate['id']}/1"

        # ---- Lid: Derived of the Layout sketch, and Derived of Base's plate
        goto("Lid")
        T.run_command_with(w, "derived", {"source": ids["Skeleton"], "sketches": [LAYOUT]})
        T.settle(w)
        T.run_command_with(w, "derived", {"source": ids["Base"], "parts": [plate_id]})
        T.settle(w)
        T.check(len(derived_of(doc["Lid"])) == 2 and not doc["Lid"].errors, doc["Lid"].errors)

        # ---- Pin: Derived of Skeleton's block, pinned to Version v1 of this Document (the dialog's Version combo)
        goto("Pin")
        T.run_command_with(w, "derived", {"source": ids["Skeleton"], "parts": [BLOCK], "document": "v1"})
        T.settle(w)
        (d_pin,) = derived_of(doc["Pin"])
        src = d_pin["params"]["source"]
        T.check(src.get("version") == "v1" and "document" not in src, src)
        T.check(not doc["Pin"].errors, doc["Pin"].errors)
        T.check(volumes(doc["Pin"]) == [1000.0], volumes(doc["Pin"]))

        # ---- diamond: one unsaved edit to Skeleton
        T.check(len(doc["Base"].state.parts) == 1 and len(doc["Lid"].state.parts) == 1,
                (list(doc["Base"].state.parts), list(doc["Lid"].state.parts)))
        T.check(volumes(doc["Base"]) == volumes(doc["Lid"]) == [600.0], (volumes(doc["Base"]), volumes(doc["Lid"])))
        set_width(doc["Skeleton"], 30)
        T.settle(w)
        for n in ("Skeleton", "Base", "Lid"):
            errs = doc[n].errors
            T.check(not errs and "ircular" not in str(errs), (n, errs))
        T.check(not w.studio_engine.live.cycle, "the window reports a link cycle")
        T.check(volumes(doc["Base"]) == volumes(doc["Lid"]) == [900.0], (volumes(doc["Base"]), volumes(doc["Lid"])))
        T.say("window: Skeleton edited, unsaved: Base=1 Lid=1 plate 900 mm3 in both")

        # ---- pin v1: Pin kept v1; v2 is offered; after the update Pin follows v2
        T.check(volumes(doc["Pin"]) == [1000.0], f"the pinned Derived changed: {volumes(doc['Pin'])}")
        T.say("pin v1: unchanged after source edit")
        fd.create_version("v2", "wider")
        pin_f = copy.deepcopy(derived_of(doc["Pin"])[0])
        st = fd.resolver.link_status(pin_f["params"]["source"])
        T.check(st["pinned"] == "v1" and st["latest"] == "v2" and st["update_available"] and not st["error"], st)
        T.check([(u["pinned"], u["latest"], u["tab"]) for u in L.cross_link_updates(fd)] ==
                [("v1", "v2", ids["Pin"])], L.cross_link_updates(fd))
        T.say(f"update available: {st['latest']}")
        T.check(fd.resolver.update_link(pin_f["params"]["source"]) == "v2", "update_link did not return v2")
        doc["Pin"].replace_feature(pin_f)
        T.settle(w)
        T.check(not doc["Pin"].errors and volumes(doc["Pin"]) == [1500.0], (doc["Pin"].errors, volumes(doc["Pin"])))
        T.check(not fd.resolver.link_status(pin_f["params"]["source"])["update_available"], "still an update")
        T.say("after update: follows v2")

        # ---- restart: Base's worker is killed; an edit to Base reopens it; an upstream edit then reaches it
        se, base = w.studio_engine, doc["Base"]
        pid = se.in_engine(base, lambda e, tab: e.pid(tab))
        os.kill(pid, signal.SIGKILL)
        end = time.monotonic() + 30
        while se.in_engine(base, lambda e, tab: e.errored(tab)) is None and time.monotonic() < end:
            time.sleep(0.05)
        T.check(se.in_engine(base, lambda e, tab: e.errored(tab)), "the killed worker did not error the tab")
        set_depth(base, plate["id"], 4)
        T.settle(w)
        T.check(se.in_engine(base, lambda e, tab: e.pid(tab)) != pid, "Base was not reopened on a new worker")
        T.check(volumes(base) == [1200.0], volumes(base))
        set_width(doc["Skeleton"], 40)                       # Base and Lid are not touched
        T.settle(w)
        T.check(not base.errors and volumes(base) == [1600.0], (base.errors, volumes(base)))
        T.check(not doc["Lid"].errors and volumes(doc["Lid"]) == [1600.0], (doc["Lid"].errors, volumes(doc["Lid"])))
        T.say("restart: upstream edit reached the restarted tab")
        fd.save()
        fd.close()
        w.folder = None
    finally:
        T.close_engine_window(w)
    return {"plate": plate_id}


def order_phase(path, ids):
    """One unsaved edit to Skeleton for each tab order on the process engine: Base and Lid rebuild once each."""
    reg = R.default_registry()
    fd = DF.FolderDocument.open(path, registry=reg)
    files = {t.name: t.file for t in fd.tabs}
    fd.close()
    width = 50
    for order in (("Skeleton", "Base", "Lid"), ("Skeleton", "Lid", "Base")):
        eng = ProcessEngine()
        try:
            for n in order:
                eng.open_tab(os.path.join(path, files[n]), tab=ids[n])
            for n in ("Base", "Lid"):
                T.check(not eng.outputs(ids[n]).errors, (order, n, eng.outputs(ids[n]).errors))
            T.check((ids["Lid"], ids["Base"]) in eng.link_edges() and (ids["Base"], ids["Skeleton"]) in eng.link_edges(),
                    eng.link_edges())
            before = eng.rebuild_counts()
            eng.submit_edit(ids["Skeleton"], {"op": "set_param", "feature": LAYOUT, "key": "sketch",
                                              "value": layout_sketch(width)})
            after = eng.rebuild_counts()
            delta = {n: after.get(ids[n], 0) - before.get(ids[n], 0) for n in ("Base", "Lid")}
            T.check(delta == {"Base": 1, "Lid": 1}, (order, delta))
            T.check(eng.rebuild_order() == [ids["Base"], ids["Lid"]], (order, eng.rebuild_order()))
            vols = {n: sorted(round(K.volume(K.shape_from_brep(p.brep)), 4) for p in eng.outputs(ids[n]).parts)
                    for n in ("Base", "Lid")}
            want = [float(width * 10 * 4)]
            T.check(vols["Base"] == want and want[0] in vols["Lid"], (order, vols, want))
            for n in ("Skeleton", "Base", "Lid"):
                errs = eng.outputs(ids[n]).errors
                T.check(not errs and "ircular" not in str(errs), (order, n, errs))
        finally:
            eng.close()
        width += 10
    T.say("diamond: Base=1 Lid=1, plate equal, no cycle error for tab orders S,B,L and S,L,B after one unsaved "
          "Skeleton edit")


def in_process_phase(path, ids, plate_id):
    """The checks that read the in-process Documents' element names and errors: the file as the window saved it."""
    fd = DF.FolderDocument.open(path, registry=R.default_registry())
    sk, base, lid = (fd.tab(ids[n]).doc for n in ("Skeleton", "Base", "Lid"))
    lid.rebuild()
    T.check(not (sk.errors or base.errors or lid.errors), (sk.errors, base.errors, lid.errors))
    d_plate = next(f for f in derived_of(lid) if f["params"]["parts"])
    pid = f"{d_plate['id']}/1"
    part = lid.state.parts[pid]
    vertical = [e for e in K.edges(part.shape) if line_along((0, 0, 1))(K.edge_info(e))]
    edges = [F.make_ref(pid, vertical[0], "edge", part.shape, lid.state.named(pid))]
    T.check(edges[0].get("name") and ";DRV" in edges[0]["name"], f"the picked edge has no derived name: {edges[0]}")
    fil = add(lid, "fillet", edges=edges, radius=1)
    vol = K.volume(lid.state.parts[pid].shape)
    set_width(sk, 60)
    fd.refresh_links()
    T.check(not lid.errors and not base.errors, (lid.errors, base.errors))
    T.check(fil["id"] not in lid.state.recovered, f"the fillet was recovered, not resolved by name: {lid.state.recovered}")
    wide = K.volume(lid.state.parts[pid].shape)
    T.check(wide > vol * 1.3, (vol, wide))
    T.say(f"fillet on derived edge: resolved by name (recovered false), plate volume {vol:.0f} -> {wide:.0f} mm3")

    # ---- the source loses the plate: Lid's Derived of it is red, with the message
    base.delete_features([plate_id.split("/")[0]])
    fd.refresh_links()
    msg = lid.errors.get(d_plate["id"], "")
    T.check("no longer published" in msg, lid.errors)
    T.say(f"source removed: derived red with the message {msg!r}")
    fd.close()


def loop_phase():
    def derived_feature(fid, tab):
        return {"id": fid, "type": "derived", "name": "Derived", "suppressed": False,
                "params": {"source": {"kind": "studio", "tab": tab, "part": None, "studio_name": tab},
                           "parts": [], "sketches": [], "connectors": []}}

    eng = ProcessEngine()
    try:
        a, b = eng.new_tab("studio", "A", tab="A"), eng.new_tab("studio", "B", tab="B")
        eng.submit_edit(a, {"op": "add_feature", "feature": derived_feature("DA", "B")})
        start = time.monotonic()
        try:
            eng.submit_edit(b, {"op": "add_feature", "feature": derived_feature("DB", "A")})
            raise T.Fail("the second Derived closed a loop and submit_edit did not raise LinkCycleError")
        except LinkCycleError:
            pass
        n = time.monotonic() - start
        T.check(n < 10, f"the loop took {n:.1f} s")
        for tab, fid in ((a, "DA"), (b, "DB")):
            msg = eng.outputs(tab).errors.get(fid, "")
            T.check("Circular link" in msg and "A" in msg and "B" in msg, (tab, eng.outputs(tab).errors))
        T.say(f"loop: reported in {n:.1f} s (both Derived red, naming the loop)")
    finally:
        eng.close()


def severed_phase():
    from test_severed_solid_refs import block, fillet, pieces, rounds, sever_before
    doc, plane = block()
    fils = [fillet(doc, 0, 0), fillet(doc, 60, 30)]
    sever_before(doc, fils[0]["id"], plane)
    T.check(not doc.errors, doc.errors)
    left, right = pieces(doc)
    T.check(len(rounds(doc, left)) == 1 and len(rounds(doc, right)) == 1, "a fillet was lost in the split")
    T.say("severed: two fillets survive the split")


def run():
    path = os.path.join(TMP, "Doc")
    ids = build_document(path)
    out = window_phase(path, ids)
    order_phase(path, ids)
    in_process_phase(path, ids, out["plate"])
    loop_phase()
    severed_phase()


if __name__ == "__main__":
    T.run_smoke("ST1", TMP, run)
