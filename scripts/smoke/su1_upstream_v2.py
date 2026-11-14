"""Upstream 2.0 merge smoke (card I5.1): one pass through what the merge brought together, in a real MainWindow.

Part of the cumulative smoke gate (see s00_baseline.py for the convention). One offscreen `MainWindow(engine=True)`
(the engine is how the app starts unless FREESHAPE_ENGINE=0: one worker process per Part Studio tab) makes a new
Document through the Home screen's New Document signal and works in it. One line per step, `STEP n <title> ... ok`
or `... FAILED: <why>`; every step runs even after an earlier one failed, and the last line is `SU1 OK` or
`SU1 FAILED: <the failed steps>`. Each step asserts an element by id or name, a value, or a count that the step made.

  1. Sketch on a part face (Base, top face), snap to its front edge twice with the circle tool: the edge is
     projected once (one `helper` construction line, `point_on` constraints on it), a third snap through the
     model-point path still adds no second line.
  2. One extrude over an ellipse, a spline and text regions: every region's part has a full element name map with
     no unnamed or repeated face. Then the ellipse extruded up to the Bar's round face: one end cap (`;:Gend;`),
     and a fillet on a cap edge stored by element name and not found by shape (`state.recovered`).
  3. Split a block with the Right and Front planes: four pieces of 2000 mm3. The seam edge of one piece is
     filleted by name; the block's depth is edited upstream; the fillet is found again by name, four pieces remain.
  4. Two extrudes in a folder named "Pegs": the engine holds the folder, suppressing it takes both parts away
     (in the window and in the engine worker), unsuppressing brings them back.
  5. Two configurations ("Short" 10, "Long" 20) of the variable that sets an extrude depth: the part's volume
     follows the toolbar's configuration box, in the window and in the engine worker; the worker's copy of the tab
     holds both rows and the active one.
  6. Assembly: three instances from the Part Studio, two revolute mates, a countersunk screw (ISO 10642 M4 x 12)
     from the Standard parts pane with quantity 2 (two instances, one bill-of-materials row of 2), a gear relation
     from the fork's dialog (ratio 0.5, moves nothing), a linear instance pattern of one screw (count 3, spacing 15:
     two copies 15 mm apart), one explode step on a spacer (25 mm along +Z).
  7a. Drawing of the Assembly: the parts list has one row per part with Steel and Aluminium as the material and a
      mass, the screw row counts 4 (2 inserted, 2 patterned), the table shows ITEM PART QTY MATERIAL MASS (g), and
      balloons name items of the list.
  7b. Drawing of a plate with two holes: a section view, a dimension between the two holes' rim circles that
      carries element names, measures 24 mm, and is found again by name after the plate is changed upstream.
  8. Create Version "v1", change an extrude depth, open Compare Versions from the palette and compare v1 with
     what is open now: the Part Studio shows one changed feature with "Depth: 14 mm -> 20 mm"; other tabs say
     "not compared".
  9. Add the bundled examples through the Plugins window, build the spur gear plugin feature in a new Part Studio
     tab: the engine worker has one part, no error, the in-process volume, and a stored copy as blobs.
 10. Save the Document, close it, open it again: every tab file's version is Part Studio 4, Assembly 9, Drawing 3;
     tab ids, kinds, feature, instance, mate, relation, pattern, explode and view counts and part volumes are
     unchanged; no tab error, no Document warning, no feature recovered by shape, no lost dimension.

Where the script does not go through the UI:
  * Set up before the window exists: temp folders for data, plugins, autosave and thumbnails; the QMessageBox
    warning, critical and information boxes are stubbed so nothing blocks; a QInputDialog is stubbed in step 4.
  * Part Studio features are made with the feature dialogs (`start_feature`, pick boxes filled with
    `dialog.fields[...].items`, values set with `dialog._set`, `dialog.accept`), but picks are items built with
    `pick_to_item` or `F.region_ref` from the part's own faces and edges, not clicks in the 3D view. Sketches other
    than step 1's are built as a `Sketch` and added with `doc.add_feature`.
  * Step 1 sends mouse events straight to the Sketcher's `mouse_move` and `mouse_press` (screen positions from the
    real camera), replaces `viewport.pick` to return the part edge (no GL in offscreen mode), and presses Finish by
    emitting the dialog's `accepted` signal.
  * Step 4 calls `_new_folder` and `doc.set_folder_suppressed` (what the folder's context menu runs), not the menu.
  * Step 5 fills a `ConfigDialog` and calls its `_ok()` without `exec()`; the toolbar box is driven with its
    `activated` signal.
  * Step 6 makes the two mates with `make_mate`, `connector` and `add_mate` (no mate dialog), inserts the three
    parts with `insert_studio_parts` (what the Insert panel runs), selects the screw from the instance list because
    selecting one in the view runs a mass-properties call that took about a minute offscreen, and sets materials in step 7a on the instance dicts
    (no Set material box). The Standard parts pane, the relation dialog, the pattern dialog and the explode panel
    are the real widgets; the modal pattern and New drawing dialogs are filled by a timer inside their event loop.
  * Step 7 calls `add_section`, `add_annot`, `add_bom_table` and `auto_balloons` on the Drawing, which the
    drawing tools call, and reads view positions from the Drawing model, not from screen coordinates.
  * Step 8 submits a `CreateVersionDialog` and calls the panel's `fill` and `compare` (the panel is opened with the
    palette command, as a user does).
  * Step 9 calls `PluginsDialog(...).install_examples()` without showing the window.
  * Step 10 calls `w.save()`, `w._close_document()` and `w.open_document(path)`, not the File menu or the Home
    screen, and reads each tab file's `version` straight from the Document's store.

    both fs_py scripts/smoke/su1_upstream_v2.py

Entry points that start a ProcessEngine need the `__main__` guard: a worker imports this file again.
"""
import os
import sys
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

SETTLE_S = 240
WATCHDOG_S = 900


class Fail(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def say(msg):
    print(msg, flush=True)


def main():
    import faulthandler
    import shutil
    import tempfile
    import traceback

    faulthandler.dump_traceback_later(WATCHDOG_S, exit=True)          # a hang costs a CI job: stop after this long
    tmp = tempfile.mkdtemp(prefix="su1_upstream_v2_")
    os.environ["FREESHAPE_DATA_DIR"] = os.path.join(tmp, "appdata")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    os.environ["HOME"] = os.path.join(tmp, "home")
    os.environ["FREESHAPE_PLUGINS"] = os.path.join(tmp, "plugins")
    for d in ("appdata", "xdg", "home", "autosave", "thumbs", "plugins"):
        os.makedirs(os.path.join(tmp, d))
    wins, failed = [], []
    try:
        run(tmp, wins, failed)
    except BaseException as e:                         # noqa: BLE001 -- any failure fails the gate
        say(f"SU1 FAILED: {e}")
        if not isinstance(e, Fail):
            traceback.print_exc()
        code = 1
    else:
        if failed:
            say("SU1 FAILED: " + "; ".join(failed))
            code = 1
        else:
            say("SU1 OK")
            code = 0
    finally:
        close_windows(wins)
        shutil.rmtree(tmp, ignore_errors=True)
    return code


def close_windows(wins):
    """Mark every document clean (a dirty one opens a save prompt that hangs), close, delete, shut the workers."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    for w in wins:
        try:
            for d in list(w.docs.values()):
                d.dirty = False
            w.close()
            if app:
                app.processEvents()
            if w.studio_engine is not None:
                w.studio_engine.shutdown()
            w.deleteLater()
            if app:
                app.processEvents()
        except Exception:                              # noqa: BLE001
            pass
    from freeshape.engine import process as P
    P.SPARES.clear()


def run(tmp, wins, failed):
    import traceback

    import numpy as np
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from conftest import rect_lines
    from freeshape import features as F
    from freeshape import kernel as K
    from freeshape.sketch import Sketch
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    from freeshape.ui.dialogs import pick_to_item
    from freeshape.ui.viewport import Pick

    app = QApplication.instance() or QApplication([])  # noqa: F841 -- must stay alive
    MW.MainWindow.autosave_root = staticmethod(lambda: os.path.join(tmp, "autosave"))
    MW.MainWindow.autosave_dir = classmethod(lambda cls: os.path.join(tmp, "autosave"))
    H.thumbs_dir = lambda: os.path.join(tmp, "thumbs")
    for name in ("warning", "critical", "information"):
        setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
    w = MW.MainWindow(engine=True)
    wins.append(w)
    w.settings = QSettings(os.path.join(tmp, "settings.ini"), QSettings.IniFormat)
    w.resize(1200, 800)
    se = w.studio_engine
    S = {}

    # ------------------------------------------------------------------ helpers
    def settle():
        check(se.settle(SETTLE_S), "the engine did not finish a rebuild")
        check(not se.broken, f"the engine failed: {se.broken}")
        app.processEvents()

    def step(n, title):
        def deco(fn):
            t0 = time.monotonic()
            try:
                fn()
            except Exception as e:                     # noqa: BLE001 -- a failed step is reported by name
                say(f"STEP {n} {title} ... FAILED: {e}")
                if not isinstance(e, Fail):
                    traceback.print_exc()
                failed.append(f"step {n} ({title}): {e}")
                leave_modes()
            else:
                say(f"STEP {n} {title} ... ok ({time.monotonic() - t0:.1f}s)")
            return fn
        return deco

    def leave_modes():
        """Back to idle after a failed step, so the next one starts clean."""
        w.viewport.__dict__.pop("pick", None)
        try:
            if w.mode == "sketch":
                w.cancel_sketch()
            elif w.mode == "dialog":
                w.cancel_dialog()
        except Exception:                              # noqa: BLE001
            pass

    def add_sketch(doc, build, plane):
        s = Sketch()
        build(s)
        s.solve()
        f = doc.make_feature("sketch")
        f["params"].update(plane=plane, sketch=s.to_dict())
        doc.add_feature(f)
        settle()
        check(not doc.errors.get(f["id"]), f"sketch: {doc.errors.get(f['id'])}")
        return f["id"]

    def regions(doc, sid, pick=None):
        return [F.region_ref(doc.state, sid, r) for r in doc.state.sketches[sid].regions
                if pick is None or pick(K.face_info(r))]

    def run_dialog(ftype, fields=None, params=None, edit=None):
        """Open the feature dialog (new, or editing feature `edit`), fill its pick boxes and values, accept it.
        Returns the feature."""
        w.start_feature(ftype, edit_fid=edit)
        d = w.dialog
        check(d is not None, f"the {ftype} dialog did not open")
        for k, items in (fields or {}).items():
            d.fields[k].items = list(items)
        d._fields_changed()
        for k, v in (params or {}).items():
            d._set(k, v)
        feat = d.feature
        d.accept()
        settle()
        err = w.doc.errors.get(feat["id"])
        check(not err, f"{ftype}: {err}")
        return w.doc.feature(feat["id"])

    def parts_of(doc, feature):
        return [p for pid, p in doc.state.parts.items() if pid.startswith(feature["id"])]

    def face_names(doc, part):
        nm = doc.state.named(part.id)
        check(nm is not None, f"part {part.id} has no element name map")
        return [nm.name_of(f) for f in K.faces(part.shape)]

    def show(doc):
        """Show a Part Studio tab in the window and let it draw."""
        w.tabs.setCurrentIndex(w._tab_index(doc))
        settle()

    # ------------------------------------------------------------------ the Document and its first Part Studio
    path = os.path.join(tmp, "Upstream")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    settle()
    fd = w.folder
    check(fd is not None and len(fd.tabs) == 1, "the new Document has no Part Studio")
    P = w.doc
    TOP = {"kind": "std", "name": "Top"}
    RIGHT = {"kind": "std", "name": "Right"}

    # a 40 x 40 x 10 base block (x -20..20, y 40..80) and a round bar along X at z 30 (r 6, x -30..30)
    sid = add_sketch(P, lambda s: rect_lines(s, -20, 40, 40, 40), TOP)
    base = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid)]}, {"depth": 10.0, "op": "new"})
    S["base_pid"] = parts_of(P, base)[0].id
    sid = add_sketch(P, lambda s: s.add_circle(s.add_point(0, 30), 6), RIGHT)
    bar = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid)]},
                     {"end": "symmetric", "depth": 60.0, "op": "new"})
    S["bar_pid"] = parts_of(P, bar)[0].id
    check(len(P.state.parts) == 2, f"{len(P.state.parts)} parts, want base and bar")
    say(f"document: {fd.path} with Part Studio 'Part Studio 1' holding Base and Bar")

    # ================================================================== 1. sketch on a part face, snap to an edge twice
    @step(1, "sketch on a part face, snap to a part edge twice, edge projected once")
    def _():
        show(P)
        vp = w.viewport
        part = P.state.parts[S["base_pid"]]
        faces = K.faces(part.shape)
        fi = next(i for i, f in enumerate(faces) if K.face_info(f).get("n") is not None
                  and K.face_info(f)["n"][2] > 0.99 and abs(K.face_info(f)["c"][2] - 10) < 1e-6)
        vp.set_selection([Pick("face", (S["base_pid"], fi))])
        n_before = len(P.features)
        w.start_sketch()
        check(w.mode == "sketch" and w.sketcher is not None, "the sketch did not start on the picked face")
        sk = w.sketcher
        check(abs(sk.plane.origin[2] - 10) < 1e-6 and sk.plane.normal[2] > 0.99, f"plane {sk.plane}")
        vp.normal_to(sk.plane, animate=False)
        vp.fit(animate=False)
        edges = K.edges(part.shape)
        want = sorted([(-20.0, 40.0, 10.0), (20.0, 40.0, 10.0)])      # the front edge of the top face
        ei = None
        for i, e in enumerate(edges):
            pts = np.asarray(K.discretize_edge(e), float)
            if sorted([tuple(np.round(pts[0], 6)), tuple(np.round(pts[-1], 6))]) == want:
                ei = i
        check(ei is not None, "front edge not found")
        vp.pick = lambda x, y, kinds=None: Pick("edge", (S["base_pid"], ei))
        sk.set_tool("circle")

        def px(x_mm):
            sx, sy = vp.cam.to_screen(np.array([x_mm, 40.0, 10.0]))[:2]      # a point of the front edge
            return float(sx), float(sy)

        class Ev:
            def modifiers(self):
                from PySide6.QtCore import Qt
                return Qt.NoModifier

        # snap onto the part edge with the line tool, then again with the circle tool: the same edge both times
        def click(x_mm, first):
            x, y = px(x_mm)
            sk.mouse_move(vp, x, y, Ev())
            kind = sk.snap_info[0] if sk.snap_info else None
            # the first snap projects the edge; the next lands on the line already projected from it
            check(kind == ("medge" if first else "on"), f"snap at x={x_mm} is {sk.snap_info}")
            sk.mouse_press(vp, x, y, Ev())

        def circle_on_edge(x_mm, first):
            """A circle whose centre is snapped onto the part edge, the radius clicked off it."""
            click(x_mm, first)
            x, y = px(x_mm)
            sk.mouse_move(vp, x, y - 40, Ev())
            sk.mouse_press(vp, x, y - 40, Ev())

        sk.set_tool("circle")
        circle_on_edge(-8.0, True)
        circle_on_edge(7.0, False)
        helpers = [(i, e) for i, e in sk.sk.entities.items() if e.get("helper")]
        check(len(helpers) == 1, f"the edge was projected {len(helpers)} times, want 1: {sk.sk.entities}")
        hid = helpers[0][0]
        on = [c for c in sk.sk.constraints if c["type"] == "point_on" and c["refs"][1] == hid]
        check(len(on) == 2, f"{len(on)} points sit on the projected edge, want 2: {sk.sk.constraints} {sk.sk.entities}")
        # and the part-edge path itself, taken a third time as the Use tool and the dimension tool take it
        sk._model_point(np.array(sk.plane.to_local(np.array([0.0, 40.0, 10.0])), float), ("medge", (S["base_pid"], ei, "on")))
        check(len([1 for e in sk.sk.entities.values() if e.get("helper")]) == 1, "a third snap projected the edge again")
        on = [c for c in sk.sk.constraints if c["type"] == "point_on" and c["refs"][1] == hid]
        check(len(on) == 3, f"{len(on)} points sit on the projected edge, want 3")
        circles = [e for e in sk.sk.entities.values() if e["type"] == "circle"]
        check(len(circles) == 2, f"{len(circles)} circles drawn, want 2")
        vp.__dict__.pop("pick", None)                   # the real picking again
        w.dialog.accepted.emit()                        # the Finish button
        settle()
        check(len(P.features) == n_before + 1, "the sketch was not added to the feature list")
        f = P.features[-1]
        saved = f["params"]["sketch"]["entities"]
        check(sum(1 for e in saved.values() if e.get("helper")) == 1, "the saved sketch lost its single projected edge")
        S["step1_sketch"] = f["id"]

    # ================================================================== 2. ellipse, up to a curved face, fillet by name
    @step(2, "ellipse and spline in one extrude, extrude up to the bar, fillet the end cap edge by name")
    def _():
        show(P)
        # (a) open point: one extrude over several regions gives parts that all have an element name map
        def curves(s):
            s.add_ellipse(s.add_point(60, 0), s.add_point(70, 0), 5)
            s.add_spline([s.add_point(x, y) for x, y in [(60, 20), (70, 25), (80, 20), (70, 12)]], closed=True)
            s.add_text(s.add_point(60, 40), "Hi", size=10)
        sid = add_sketch(P, curves, TOP)
        sr = P.state.sketches[sid]
        text_regions = len(sr.regions) - 2             # none when the machine has no font: text_shape then gives nothing
        check(text_regions >= 0, f"{len(sr.regions)} regions, want ellipse and spline and the glyphs")
        say(f"  the extrude over several regions takes {len(sr.regions)} regions ({text_regions} from text)")
        multi = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid)]},
                           {"depth": 5.0, "op": "new"})
        ps = parts_of(P, multi)
        check(len(ps) == len(sr.regions), f"{len(ps)} parts from {len(sr.regions)} regions")
        for p in ps:
            names = face_names(P, p)
            check(all(names) and len(set(names)) == len(names), f"part {p.id} has unnamed or repeated faces: {names}")
        S["multi_parts"] = [p.id for p in ps]

        # the ellipse again, extruded up to the bar's round face
        sid = add_sketch(P, lambda s: s.add_ellipse(s.add_point(0, 0), s.add_point(5, 0), 3), TOP)
        bar = P.state.parts[S["bar_pid"]]
        bar_faces = K.faces(bar.shape)
        ri = max(range(len(bar_faces)), key=lambda i: K.face_info(bar_faces[i])["area"])
        item = pick_to_item(w.viewport, Pick("face", (S["bar_pid"], ri)), {"face"}, P.state)
        check(item is not None and item[1].get("name"), f"the bar's round face has no name: {item}")
        up = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid)], "upto_face": [item]},
                        {"end": "up_to_face", "op": "new"})
        (cap_part,) = parts_of(P, up)
        names = face_names(P, cap_part)
        check(all(names) and len(set(names)) == len(names), f"unnamed or repeated faces: {names}")
        END = ";:Gend;"
        check(sum(END in n for n in names) == 1, f"end cap role {END!r} appears {sum(END in n for n in names)} times")
        nm = P.state.named(cap_part.id)
        ci = next(i for i, f in enumerate(K.faces(cap_part.shape)) if END in (nm.name_of(f) or ""))
        cap = K.faces(cap_part.shape)[ci]
        cap_edges = K.edges(cap)
        check(cap_edges, "the end cap has no edges")
        # pick an edge of the cap in the window's viewport (the engine gives its name), then fillet it
        shape_edges = K.edges(cap_part.shape)
        ei = next(i for i, e in enumerate(shape_edges) if any(e.IsSame(c) for c in cap_edges))
        w.viewport.set_selection([])
        eitem = pick_to_item(w.viewport, Pick("edge", (cap_part.id, ei)), {"edge"}, P.state)
        check(eitem is not None and eitem[1].get("name"), f"the cap edge has no element name: {eitem}")
        fil = run_dialog("fillet", {"edges": [eitem]}, {"radius": 0.4})
        check(fil["params"]["edges"][0].get("name") == eitem[1]["name"], "the fillet did not store the edge by name")
        check(fil["id"] not in P.state.recovered, "the fillet found its edge by shape, not by name")
        S["fillet_name"] = eitem[1]["name"]

    # ================================================================== 3. split with two tools, reference a piece by name
    @step(3, "split a block with two planes, fillet the seam edge of one piece by name, edit upstream")
    def _():
        show(P)
        sid = add_sketch(P, lambda s: rect_lines(s, -10, -10, 20, 20), TOP)
        blk = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid)]}, {"depth": 20.0, "op": "new"})
        (blk_part,) = parts_of(P, blk)
        run_dialog("move", {"parts": [("part", blk_part.id)]}, {"dz": 50.0})
        spl = run_dialog("split", {"parts": [("part", blk_part.id)],
                                   "tools": [("plane", RIGHT), ("plane", {"kind": "std", "name": "Front"})]})
        check(len(spl["params"]["tools"]) == 2, f"split has {len(spl['params']['tools'])} tools")
        pieces = [p for pid, p in P.state.parts.items() if pid.split(".")[0] == blk_part.id]
        check(len(pieces) == 4, f"{len(pieces)} pieces, want 4")
        check(sorted(round(K.volume(p.shape)) for p in pieces) == [2000] * 4, "the pieces are not four equal quarters")
        piece = next(p for p in pieces if all(c > 0 for c in K.mass_props(p.shape)["centroid"][:2]))
        edges = K.edges(piece.shape)
        ei = next(i for i, e in enumerate(edges) if K.edge_info(e).get("type") == int(K.GeomAbs_Line)
                  and abs(K.edge_info(e)["c"][0]) < 1e-6 and abs(K.edge_info(e)["c"][1]) < 1e-6
                  and abs(K.edge_info(e)["c"][2] - 60) < 1e-6)          # the seam: both cuts meet at x = y = 0
        eitem = pick_to_item(w.viewport, Pick("edge", (piece.id, ei)), {"edge"}, P.state)
        check(eitem is not None and eitem[1].get("name"), f"the seam edge has no name: {eitem}")
        S["seam_name"] = eitem[1]["name"]
        fil = run_dialog("fillet", {"edges": [eitem]}, {"radius": 1.0})
        v0 = K.volume(P.state.parts[piece.id].shape)
        check(abs(v0 - (2000 - (1 - np.pi / 4) * 20)) < 1e-3, f"filleted piece volume {v0}")
        # change the block upstream of the split: the named seam is found again, by name
        run_dialog("extrude", {}, {"depth": 30.0}, edit=blk["id"])
        check(fil["id"] not in P.state.recovered, "the fillet found its seam edge by shape, not by its name")
        pieces = [p for pid, p in P.state.parts.items() if pid.split(".")[0] == blk_part.id]
        check(len(pieces) == 4, f"{len(pieces)} pieces after the edit, want 4")
        v1 = K.volume(P.state.parts[piece.id].shape)
        check(abs(v1 - (3000 - (1 - np.pi / 4) * 30)) < 1e-3, f"filleted piece volume after the edit {v1}")

    # ================================================================== 4. two features in a folder, suppress the folder
    @step(4, "two features in a folder, suppress the folder, unsuppress it")
    def _():
        import json
        from PySide6.QtWidgets import QInputDialog
        show(P)
        sa = add_sketch(P, lambda s: rect_lines(s, 100, 0, 10, 10), TOP)
        sb = add_sketch(P, lambda s: rect_lines(s, 130, 0, 10, 10), TOP)
        ea = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sa)]}, {"depth": 5.0, "op": "new"})
        eb = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sb)]}, {"depth": 7.0, "op": "new"})
        n0 = len(P.state.parts)
        real_get = QInputDialog.getText
        QInputDialog.getText = staticmethod(lambda *a, **k: ("Pegs", True))      # the "Name folder" box
        try:
            w._new_folder([ea["id"], eb["id"]])
        finally:
            QInputDialog.getText = real_get
        settle()
        check(len(P.folders) == 1, f"{len(P.folders)} folders, want 1")
        fo, info = next(iter(P.folders.items()))
        check(info["name"] == "Pegs", f"folder name {info['name']!r}")
        check(P.folder_members(fo) == [ea["id"], eb["id"]], f"folder members {P.folder_members(fo)}")
        check(len(P.state.parts) == n0, "making the folder changed the parts")

        def engine_view():
            return se.in_engine(P, lambda e, t: (len(e.outputs(t).parts),
                                                 json.loads(bytes(e.tab_data(t)).decode("utf-8")).get("folders", {})))

        check(engine_view()[0] == n0 and fo in engine_view()[1], "the engine does not hold the folder")
        P.set_folder_suppressed(fo, True)                  # the folder menu's "Suppress all"
        settle()
        check(all(P.feature(i)["suppressed"] for i in (ea["id"], eb["id"])), "the folder's features are not suppressed")
        check(len(P.state.parts) == n0 - 2, f"{len(P.state.parts)} parts with the folder suppressed, want {n0 - 2}")
        check(engine_view()[0] == n0 - 2, "the engine still builds the suppressed folder")
        P.set_folder_suppressed(fo, False)
        settle()
        check(len(P.state.parts) == n0, f"{len(P.state.parts)} parts after unsuppress, want {n0}")
        check(engine_view()[0] == n0, "the engine did not bring the folder back")
        S["folder_id"] = fo

    # ================================================================== 5. two configurations, switch
    @step(5, "two configurations of a variable, switch the active one")
    def _():
        import json
        from PySide6.QtWidgets import QTableWidgetItem
        from freeshape.ui.config_ui import ConfigDialog
        show(P)
        run_dialog("variable", {}, {"name": "len", "expr": "40"})
        sid = add_sketch(P, lambda s: rect_lines(s, 200, 0, 20, 10), TOP)
        spacer = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid)]},
                            {"depth": "#len", "op": "new"})
        (sp,) = parts_of(P, spacer)
        vol = lambda: K.volume(P.state.parts[sp.id].shape)             # noqa: E731
        check(abs(vol() - 200 * 40) < 1e-6, f"spacer volume {vol()}, want {200 * 40}")
        dlg = ConfigDialog(w, P)                          # the Configurations table, filled in and confirmed
        dlg._add_row(None)
        for r, (name, val) in enumerate((("Short", "10"), ("Long", "20"))):
            dlg.table.item(r, 0).setText(name)
            dlg.table.setItem(r, 1, QTableWidgetItem(val))
        dlg._refresh_active(0)
        dlg._ok()
        settle()
        check(P.config_names() == ["Short", "Long"] and P.active_config() == "Short",
              f"configs {P.config_names()}, active {P.active_config()}")
        w._refresh_config_box()
        check(w.config_box.count() == 2, f"the configuration box lists {w.config_box.count()} entries")
        check(abs(vol() - 200 * 10) < 1e-6, f"Short: spacer volume {vol()}, want {200 * 10}")
        w.config_box.setCurrentIndex(1)
        w.config_box.activated.emit(1)                    # choose "Long" in the toolbar box
        settle()
        check(P.active_config() == "Long", f"active configuration {P.active_config()}")
        check(abs(vol() - 200 * 20) < 1e-6, f"Long: spacer volume {vol()}, want {200 * 20}")
        engine_vol = se.in_engine(P, lambda e, t: sum(K.volume(K.shape_from_brep(q.brep))
                                                      for q in e.outputs(t).parts if q.id == sp.id))
        check(abs(engine_vol - 200 * 20) < 1e-6, f"the engine holds volume {engine_vol} for Long")
        saved = json.loads(bytes(se.in_engine(P, lambda e, t: e.tab_data(t))).decode("utf-8"))["configs"]
        check([r["name"] for r in saved["rows"]] == ["Short", "Long"] and saved["active"] == 1, f"engine copy {saved}")
        w.config_box.setCurrentIndex(0)
        w.config_box.activated.emit(0)
        settle()
        check(abs(vol() - 200 * 10) < 1e-6, f"back to Short: spacer volume {vol()}")
        S["spacer_pid"] = sp.id

    # ================================================================== 6. Assembly
    def with_modal(trigger, fill):
        """Call `trigger()`, which opens a modal dialog; `fill(dialog)` runs from a timer inside its event loop and
        must accept it. A dialog that never opens, or a fill that raises, closes by itself after SETTLE_S."""
        box = {"error": None, "seen": False}
        t_end = time.monotonic() + 30

        def poll():
            dlg = QApplication.activeModalWidget()
            if dlg is None:
                if time.monotonic() < t_end:
                    QTimer.singleShot(20, poll)
                return
            box["seen"] = True
            try:
                fill(dlg)
            except Exception as e:                     # noqa: BLE001
                box["error"] = e
            finally:
                if dlg.isVisible():
                    dlg.reject() if box["error"] else dlg.accept()

        QTimer.singleShot(20, poll)
        trigger()
        check(box["seen"], "the dialog never opened")
        if box["error"] is not None:
            raise box["error"]

    @step(6, "Assembly: countersunk screw x2, gear relation, linear pattern, explode step")
    def _():
        from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox
        show(P)
        w.new_assembly()
        settle()
        A = w.doc
        check(A.kind == "assembly", "no Assembly tab opened")
        ctl = w.asm
        uid = P.uid
        plate_pid, spacer_pid = S["base_pid"], S["spacer_pid"]
        for pid in (plate_pid, spacer_pid, spacer_pid):
            ctl.insert_studio_parts(uid, [pid])
        app.processEvents()
        check(len(A.instances) == 3, f"{len(A.instances)} instances, want plate and two spacers")
        plate, sp1, sp2 = [i["id"] for i in A.instances]
        S["asm_instances"] = (plate, sp1, sp2)

        def face_where(iid, pred):
            return next(i for i, f in enumerate(K.faces(A.parts[iid].shape)) if pred(K.face_info(f)))

        up = lambda i: i.get("n") is not None and i["n"][2] > 0.9                  # noqa: E731
        down = lambda i: i.get("n") is not None and i["n"][2] < -0.9               # noqa: E731
        mates = []
        for iid in (sp1, sp2):                     # two spacers each turn on the plate: two revolute mates
            m = A.make_mate("revolute")
            m.update(a=A.connector(plate, "face", face_where(plate, up)), b=A.connector(iid, "face", face_where(iid, down)))
            A.add_mate(m)
            mates.append(m)
        check(len(A.mates) == 2, f"{len(A.mates)} mates")

        # a countersunk screw, quantity 2, from the Standard parts pane of the Insert panel
        if ctl.insert_panel is None:
            ctl.toggle_insert()
        pane = ctl.insert_panel.standard
        ctl.insert_panel.tabs.setCurrentIndex(1)
        n0 = len(A.instances)
        pane.select_type("sp:fasteners/iso10642")
        pane.set_free_size("M4")
        pane.set_free_length(12)
        pane.quantity.setValue(2)
        pane.insert_btn.click()
        app.processEvents()
        screws = [i["id"] for i in A.instances if (i["source"].get("kind") == "standard"
                                                    or "iso10642" in str(i["source"]))]
        check(len(A.instances) == n0 + 2 and len(screws) == 2, f"{len(A.instances) - n0} instances inserted, {len(screws)} screws")
        rows = [r for r in A.bom() if "10642" in r["name"]]
        check(len(rows) == 1 and rows[0]["qty"] == 2 and rows[0]["name"] == "ISO 10642 M4 x 12", f"bom rows {rows}")
        ctl.close_insert()

        # a gear relation between the two revolute mates, with the fork's dialog
        ctl.start_relation("gear")
        d = ctl.relation_dialog
        check(d is not None, "the relation dialog did not open")
        d.pick_mate(mates[0]["id"])
        d.pick_mate(mates[1]["id"])
        d.ratio.setText("0.5")
        before = {i["id"]: [list(r) for r in i["T"]] for i in A.instances}
        d.accept()
        check(len(A.relations) == 1, f"{len(A.relations)} relations")
        r = A.relations[0]
        check(r["type"] == "gear" and r["ratio"] == 0.5 and r["a"]["mate"] == mates[0]["id"]
              and r["b"]["mate"] == mates[1]["id"], f"relation {r}")
        check({i["id"]: [list(q) for q in i["T"]] for i in A.instances} == before, "adding the relation moved a part")

        # a linear instance pattern of one screw: Direction X, count 3, spacing 15
        seed = screws[0]
        ctl.panel.select_instances([seed])             # from the instance list: picking a screw in the view measures it

        def fill_pattern(dlg):
            combos, counts, amounts = dlg.findChildren(QComboBox), dlg.findChildren(QSpinBox), dlg.findChildren(QDoubleSpinBox)
            check(combos and counts and amounts, "the pattern dialog lacks its fields")
            combos[0].setCurrentIndex(0)           # X axis
            counts[0].setValue(3)
            amounts[0].setValue(15.0)

        with_modal(lambda: ctl.pattern_dialog("linear"), fill_pattern)
        check(len(A.patterns) == 1 and A.patterns[0]["type"] == "linear" and A.patterns[0]["count"] == 3,
              f"patterns {A.patterns}")
        copies = A.pattern_copies(seed)
        check(len(copies) == 2, f"{len(copies)} pattern copies, want 2")
        xs = sorted(A.world_T(i)[0, 3] if hasattr(A, "world_T") else np.array(A.instance(i)["T"])[0][3]
                    for i in [seed] + copies)
        check(abs((xs[1] - xs[0]) - 15) < 1e-6 and abs((xs[2] - xs[1]) - 15) < 1e-6, f"pattern x positions {xs}")

        # one explode step: the plate's first spacer moved 25 mm up
        ctl.toggle_explode()
        panel = ctl.explode_panel
        check(panel is not None, "the exploded view panel did not open")
        panel.dir.setCurrentIndex(panel.dir.findText("+Z"))
        panel.dist.setValue(25.0)
        ctl.panel.select_instances([sp1])
        panel._add()
        check(len(A.explode["steps"]) == 1, f"{len(A.explode['steps'])} explode steps")
        st = A.explode["steps"][0]
        check(st["ids"] == [sp1] and abs(st["dist"] - 25.0) < 1e-9 and list(st["dir"]) == [0, 0, 1], f"step {st}")
        dz = A.display_T(sp1, exploded=True)[2, 3] - A.display_T(sp1, exploded=False)[2, 3]
        check(abs(dz - 25.0) < 1e-6, f"exploded offset of the spacer {dz}, want 25")
        check(panel.steps.count() == 1, "the panel does not list the step")
        ctl.close_explode()

    # ================================================================== 7. Drawings
    def new_drawing(preselect=None):
        """Create a Drawing tab through the New drawing dialog (standard views on, defaults otherwise)."""
        n = w.tabs.count()
        with_modal(lambda: w.new_drawing(preselect), lambda dlg: check(dlg.chosen() is not None, "no source chosen"))
        settle()
        check(w.tabs.count() == n + 1 and w.doc.kind == "drawing", "no Drawing tab opened")
        return w.doc

    @step("7a", "Drawing of the Assembly: parts list with material and mass, balloons")
    def _():
        A = next(t.doc for t in fd.tabs if t.kind == "assembly")
        w.tabs.setCurrentIndex(w._tab_index(A))
        settle()
        plate_iid, sp1, sp2 = S["asm_instances"]
        A.instance(plate_iid)["material"] = "Steel"
        A.instance(sp1)["material"] = "Aluminium"
        A.instance(sp2)["material"] = "Aluminium"
        bom = A.bom()
        g = new_drawing()
        check(g.source.get("kind") == "assembly", f"the drawing shows {g.source}")
        check(len(g.bom) == len(bom) and [r["name"] for r in g.bom] == [r["name"] for r in bom],
              f"drawing rows {[r['name'] for r in g.bom]}, assembly rows {[r['name'] for r in bom]}")
        by_name = {r["name"]: r for r in g.bom}
        screw_rows = [r for n, r in by_name.items() if "10642" in n]
        check(len(screw_rows) == 1 and screw_rows[0]["qty"] == 4, f"screw rows {screw_rows} (2 inserted + 2 patterned)")
        mats = sorted(r.get("material") for r in g.bom if r.get("material"))
        check(mats.count("Steel") == 1 and mats.count("Aluminium") == 1, f"materials {mats}")
        check(all(r["mass"] is not None and r["mass"] > 0 for r in g.bom if r.get("material")), "a row with a material has no mass")
        table = g.add_bom_table()
        texts = [it[4] for it in g.items() if it[0] == "text"]
        check(all(h in texts for h in ("ITEM", "PART", "QTY", "MATERIAL", "MASS (g)")), f"parts list headers in {texts}")
        check("Steel" in texts and "Aluminium" in texts, "the parts list does not show the materials")
        iso = next(v for v in g.views if v["type"] == "iso")
        added = g.auto_balloons(iso)
        balloons = [a for a in g.annots if a["type"] == "balloon" and a["view"] == iso["id"]]
        check(added == len(balloons) and added >= 2, f"{added} balloons added, {len(balloons)} stored")
        check({b["item"] for b in balloons} <= {r["item"] for r in g.bom}, "a balloon names an item that is not in the list")
        S["asm_drawing"] = g
        S["asm_bom_table"] = table["id"]

    @step("7b", "Drawing of a holed plate: section view, a dimension between two holes by element name")
    def _():
        show(P)

        def plate(s):
            rect_lines(s, 400, 0, 40, 20)
            s.add_circle(s.add_point(408, 10), 3)
            s.add_circle(s.add_point(432, 10), 3)
        sid = add_sketch(P, plate, TOP)
        big = max(K.face_info(r)["area"] for r in P.state.sketches[sid].regions)
        ext = run_dialog("extrude", {"profiles": [("region", r) for r in regions(P, sid, lambda i: abs(i["area"] - big) < 1e-6)]},
                         {"depth": 10.0, "op": "new"})
        (holed,) = parts_of(P, ext)
        check(len(K.faces(holed.shape)) == 4 + 2 + 2, f"the holed plate has {len(K.faces(holed.shape))} faces")
        g = new_drawing(w.studio_link(P, holed.id, config=P.active_config()))
        front = g.views[0]
        check(front["type"] == "base", f"first view {front['type']}")
        sc = g.view_scale(front)
        lo, hi = g.model_extent(front["d"], front["up"])
        mid_y = front["o"][1] + sc * (lo[1] + hi[1]) / 2           # a cutting line through the plate's mid-height
        x0, x1 = front["o"][0] + sc * lo[0] - 5, front["o"][0] + sc * hi[0] + 5
        sec = g.add_section(front, (x0, mid_y), (x1, mid_y), ((x0 + x1) / 2, mid_y + 60))
        check(sec["type"] == "section" and sec.get("letter"), f"section view {sec}")

        def rim_end(x, y):
            """A dimension end on the bottom rim circle of the hole at (x, y), as picking it in the view gives."""
            _verts, circles = g._snapdata()
            ci = next(i for i, c in enumerate(circles) if not c[4] and abs(c[0][0] - x) < 1e-6
                      and abs(c[0][1] - y) < 1e-6 and abs(c[0][2]) < 1e-6)
            c = circles[ci]
            return {"kind": "centre", "p": [float(q) for q in c[0]], "r": c[2], "i": ci, "n": len(circles)}

        dim = g.add_annot({"type": "lin", "view": sec["id"], "a": rim_end(408, 10), "b": rim_end(432, 10),
                           "orient": "h", "pos": [100, 120]})
        check(dim["a"].get("name") and dim["b"].get("name"), f"the dimension ends carry no names: {dim['a']} {dim['b']}")
        check(abs(g.dim_geometry(dim)[2] - 24.0) < 1e-6, f"dimension {g.dim_geometry(dim)[2]}, want 24")
        names = (dim["a"]["name"], dim["b"]["name"])
        # the plate gets thicker in the Part Studio; the drawing is rebuilt and finds both ends again by name
        dim["a"]["i"] = dim["b"]["i"] = 0                               # a stale index must not matter
        show(P)
        run_dialog("extrude", {}, {"depth": 14.0}, edit=ext["id"])
        w.tabs.setCurrentIndex(w._tab_index(g))
        settle()
        g.refresh(notify=False)
        check(abs(K.bbox(g.shape)[1][2] - 14.0) < 1e-6, f"the drawing shows a plate {K.bbox(g.shape)[1][2]} mm thick")
        check(not dim["a"].get("lost") and not dim["b"].get("lost") and g.dimension_errors() == {},
              f"dimension ends lost: {g.dimension_errors()}")
        check((dim["a"]["name"], dim["b"]["name"]) == names and abs(g.dim_geometry(dim)[2] - 24.0) < 1e-6,
              "the dimension did not follow its named ends")
        check(any(it[2] == dim["id"] and it[1] == "DIM" for it in g.items()), "the dimension is not drawn")
        S["plate_drawing"] = g

    # ================================================================== 8. a Version, a changed depth, what-changed view
    @step(8, "create a Version, change a depth, compare with the what-changed view")
    def _():
        from freeshape import history as HIST
        from freeshape.ui import versions_ui as V
        show(P)
        studio_tab = next(t for t in fd.tabs if t.doc is P)
        plate_ext = next(f for f in P.features if f["type"] == "extrude" and f["params"].get("depth") == 14.0)
        dlg = V.CreateVersionDialog(w.versions_ui)
        dlg.name_edit.setText("v1")
        check(dlg.submit(), f"create version: {dlg.error_label.text()}")
        check("v1" in [v["name"] if isinstance(v, dict) else v for v in fd.versions()], f"versions {fd.versions()}")
        run_dialog("extrude", {}, {"depth": 20.0}, edit=plate_ext["id"])
        check(P.feature(plate_ext["id"])["params"]["depth"] == 20.0, "the depth did not change")
        # the palette's Compare Versions command opens the panel; ask it for v1 against what is open now
        cmd = next(c for c in w._palette_commands() if c.name == "Compare Versions")
        check(cmd.disabled == "", f"Compare Versions is disabled: {cmd.disabled}")
        cmd.action()
        panel = w.versions_ui.compare_panel
        check(panel is not None, "the Compare Versions panel did not open")
        panel.fill(["v1", HIST.NOW])
        res = panel.compare()
        row = next(t for t in res["tabs"] if t["id"] == studio_tab.id)
        check(row["status"] == "changed", f"Part Studio row status {row['status']}")
        changed = [c for c in row["features"] if c["kind"] == "changed"]
        check([c["name"] for c in changed] == [plate_ext["name"]], f"changed features {[c['name'] for c in changed]}")
        check(changed[0]["lines"] == ["Depth: 14 mm → 20 mm"], f"change lines {changed[0]['lines']}")
        check(not [c for c in row["features"] if c["kind"] in ("added", "removed")], "the comparison lists a feature added or removed")
        others = [t for t in res["tabs"] if t["id"] != studio_tab.id]
        check(others and all(t["status"] == "not compared" for t in others), f"other tabs {[(t['kind'], t['status']) for t in others]}")
        text = panel.text.toPlainText()
        check("Depth: 14 mm → 20 mm" in text and plate_ext["name"] in text, "the panel does not show the depth change")
        panel.close_panel()
        run_dialog("extrude", {}, {"depth": 14.0}, edit=plate_ext["id"])             # put it back for the saved Document

    # ================================================================== 9. the bundled spur gear plugin through the engine
    @step(9, "install the bundled spur gear plugin, build its feature in the engine worker")
    def _():
        import json
        from freeshape import plugins as PL
        from freeshape.document import PartStudioDoc
        from freeshape.ui.plugin_ui import PluginsDialog
        check("plugin:spur_gear" not in PL.FEATURES, "the spur gear plugin is installed before the step")
        PluginsDialog(w).install_examples()               # the Plugins window's "Add the examples"
        settle()
        check("plugin:spur_gear" in PL.FEATURES, f"plugin features {sorted(PL.FEATURES)}")
        check(os.path.isfile(os.path.join(PL.user_dir(), "spur_gear.py")), "the plugin file is not in the plugins folder")
        w.new_doc()                                        # a Part Studio tab of its own for the gear
        settle()
        G = w.doc
        check(G.kind == "studio" and G is not P and se.attached(G), "no new Part Studio tab on the engine")
        gear = {"module": 1.5, "teeth": 14, "thickness": 4, "bore": 3}
        w.start_feature("plugin:spur_gear")
        d = w.dialog
        check(d is not None, "the spur gear dialog did not open")
        for k, v in gear.items():
            d._set(k, v)
        feat = d.feature
        d.accept()
        settle()
        check(not G.errors.get(feat["id"]), f"spur gear: {G.errors.get(feat['id'])}")
        check(len(G.state.parts) == 1, f"{len(G.state.parts)} parts, want the gear")
        # what the engine worker holds: one part, no errors, the same volume as an in-process build
        out = se.in_engine(G, lambda e, t: e.outputs(t))
        check(out.errors == {} and len(out.parts) == 1, f"worker errors {out.errors}, parts {len(out.parts)}")
        ref = PartStudioDoc()
        f2 = {"id": "F1", "type": "plugin:spur_gear", "name": "Spur gear", "suppressed": False,
              "params": {**PL.default_params("plugin:spur_gear"), **gear}}
        ref.add_feature(f2)
        check(not ref.errors, ref.errors)
        want = sum(K.volume(q.shape) for q in ref.state.parts.values())
        got = K.volume(K.shape_from_brep(out.parts[0].brep))
        check(abs(got - want) < 1e-6 * want, f"worker gear volume {got}, in-process {want}")
        check(want > 100, f"gear volume {want}")
        # the worker saves its copy of the build as blobs, so the file opens without the plugin
        data = json.loads(bytes(se.in_engine(G, lambda e, t: e.tab_data(t))).decode("utf-8"))
        entry = data["python_builds"][feat["id"]]
        check(PL.is_copy(entry) and entry["plugin"] == "spur_gear" and len(entry["breps"]) == 1, f"stored build {entry}")
        S["gear_doc"] = G
        S["gear_volume"] = want

    # ================================================================== 10. save, reload, versions, nothing lost
    @step(10, "save, close, reload: every tab's file version, and nothing reported lost")
    def _():
        import json
        from freeshape import migrations as MIG
        want_ver = {"studio": 4, "assembly": 9, "drawing": 3}
        check({k: MIG.latest_version(k if k != "studio" else "document") for k in want_ver} == want_ver,
              "the migrations module reports other latest versions than the card's formats")

        def tab_versions(folder):
            return {t.id: (t.kind, json.loads(bytes(folder.store.read_tab(t.id)).decode("utf-8")).get("version"))
                    for t in folder.tabs}

        def snapshot(folder):
            """What each tab holds, as numbers that survive a reload."""
            out = {}
            for t in folder.tabs:
                d = t.doc
                if t.kind == "studio":
                    out[t.id] = {"features": len(d.features), "folders": len(d.folders), "configs": d.config_names(),
                                 "parts": sorted(round(K.volume(q.shape), 3) for q in d.state.parts.values())}
                elif t.kind == "assembly":
                    out[t.id] = {"instances": len(d.instances), "mates": len(d.mates), "relations": len(d.relations),
                                 "patterns": len(d.patterns), "explode": len(d.explode["steps"])}
                else:
                    out[t.id] = {"views": len(d.views), "annots": sorted(a["type"] for a in d.annots),
                                 "source": d.source.get("kind")}
            return out

        def nothing_lost(folder, where):
            check(not folder.errors, f"{where}: tabs that did not open: {folder.errors}")
            check(not folder.warnings, f"{where}: Document warnings: {[x['message'] for x in folder.warnings]}")
            for t in folder.tabs:
                d = t.doc
                check(d is not None, f"{where}: tab {t.name} has no document")
                if t.kind == "studio":
                    check(not d.errors, f"{where}: {t.name} feature errors {d.errors}")
                    check(not d.state.recovered, f"{where}: {t.name} found features by shape: {d.state.recovered}")
                elif t.kind == "assembly":
                    check(not d.errors, f"{where}: {t.name} errors {d.errors}")
                else:
                    check(not d.error and d.dimension_errors() == {}, f"{where}: {t.name} {d.error} {d.dimension_errors()}")

        show(P)
        nothing_lost(fd, "before saving")
        before = snapshot(fd)
        kinds = {t.id: t.kind for t in fd.tabs}
        check(sorted(kinds.values()) == ["assembly", "drawing", "drawing", "studio", "studio"], f"tabs {sorted(kinds.values())}")
        check(w.save() is True, "saving the Document failed")
        vers = tab_versions(fd)
        check(all(v == want_ver[k] for k, v in vers.values()), f"saved file versions {vers}, want {want_ver}")
        ids = list(kinds)
        for d in w.docs.values():
            d.dirty = False
        check(w._close_document() is True, "closing the Document failed")
        settle()
        check(w.folder is None, "the Document is still open")
        fd2 = w.open_document(path)
        check(fd2 is not None and fd2 is not fd, "the Document did not reopen")
        settle()
        check([t.id for t in fd2.tabs] == ids, f"tabs after reload {[t.id for t in fd2.tabs]}, want {ids}")
        check({t.id: t.kind for t in fd2.tabs} == kinds, "a tab changed kind")
        nothing_lost(fd2, "after reload")
        check(snapshot(fd2) == before, f"the reloaded Document differs:\n  saved  {before}\n  loaded {snapshot(fd2)}")
        check(tab_versions(fd2) == vers, f"file versions changed on reload: {tab_versions(fd2)} vs {vers}")
        # the gear still builds without the plugin file: its stored copy is what opens
        gear_tab = next(t for t in fd2.tabs if t.doc is not None and t.kind == "studio" and t.id != fd2.tabs[0].id)
        check(abs(sum(K.volume(q.shape) for q in gear_tab.doc.state.parts.values()) - S["gear_volume"]) < 1e-6 * S["gear_volume"],
              "the reloaded gear differs from the saved one")
        # a second save from the reloaded Document keeps the same versions
        w.tabs.setCurrentIndex(w._tab_index(fd2.tabs[0].doc))
        for t in fd2.tabs:
            t.doc.dirty = True
        check(w.save() is True and tab_versions(fd2) == vers, f"versions after saving again {tab_versions(fd2)}")



if __name__ == "__main__":
    sys.exit(main())
