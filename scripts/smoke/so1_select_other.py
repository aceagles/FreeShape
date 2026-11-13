"""Select other smoke (slice S1): pick the element hidden under another one, end to end in a real MainWindow.

Gate script of the Select Other plan (helpers in topdown_common.py). Three scenarios, each through the real
SelectOtherPopup (a real QListWidget popup, driven with QTest key events: Down to the row, Enter to choose) and
each asserting the chosen element by id, not by count:

  1. Sketch: a solid line lies exactly under a construction line. The sketch is opened with the window's edit
     command, the pointer is moved over the line's middle with a mouse-move event and the backtick key is delivered
     to the viewport. The popup lists both lines; Down to the solid line's row and Enter leave the sketcher's
     selection as exactly [the solid line's id]. The right-click "Select other..." action is also checked.
  2. Part Studio: an extruded box seen square on from +z. Backtick over the front (top) face's middle lists both
     faces; the row of the back (bottom) face is chosen and viewport.selection is that one face, found by its
     geometry (normal -z, centre z = 0), not by its position in the list.
  3. Assembly: two instances of a part whose mate connector is on its far (bottom) face, seen from above. With a
     mate started, backtick over the connector's screen position lists it; its row is chosen and the mate dialog's
     end A is that connector on that instance (checked for the front instance, then for the rear one).

Not through the real UI: the Document is built with test helpers (not drawn); the sketch is opened by calling
MainWindow.edit_feature, not by clicking the feature tree; the camera is set directly (after the window's own view
fits finish); the pointer move is a QMouseEvent sent to the viewport, with no button click; the context-menu action is
found on a recording QMenu and triggered (a real QMenu.exec blocks offscreen); the assembly mate is started with
AssemblyController.start_mate and its connector row is chosen with the keyboard in the real popup.

    both fs_py scripts/smoke/so1_select_other.py
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

TMP = T.isolate("so1_select_other_")

import numpy as np  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def settle(vp):
    """Let the window finish its own queued view fits and animations, so none of them moves the camera after the
    script has set it (a fit queued on a new tab or scene starts its animation on the next event-loop turn)."""
    import time
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    end = time.time() + 5.0
    quiet = 0
    while quiet < 25 and time.time() < end:          # 25 quiet turns of 10 ms: no animation running or about to start
        app.processEvents()
        time.sleep(0.01)
        quiet = 0 if vp._anim_timer.isActive() else quiet + 1
    T.check(quiet >= 25, "the view animation did not finish")


def square_on(vp, centre):
    """Look straight down -z at `centre` (the camera the unit tests use), once the window has stopped moving it."""
    from freeshape.ui.camera import basis
    settle(vp)
    vp.cam.set_state((np.array(centre, float), basis((0, 0, 1), (0, 1, 0)), 0.1))


def at(vp, *xyz):
    sx, sy, _z = vp.cam.to_screen(np.array(xyz, float))
    return float(sx), float(sy)


def move_to(vp, x, y):
    """A real mouse-move event delivered to the viewport through Qt's event dispatch, so the cursor position the
    backtick key reads is set the way a move sets it. (QTest.mouseMove does not reach a widget in a window that is
    not active offscreen.)"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    local = QPointF(float(x), float(y))
    ev = QMouseEvent(QEvent.MouseMove, local, vp.mapToGlobal(local), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(vp, ev)


def press_backtick(vp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.keyClick(vp, Qt.Key_QuoteLeft)


def choose_row(app, popup, want, hovered):
    """Drive the real popup: Down until the current row's payload satisfies `want`, then Enter. `hovered` is called
    with each payload the popup reports as highlighted (so the hover callback is seen to fire). Returns the row index."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    T.check(popup.isVisible(), "the Select other popup is not shown")
    for _ in range(popup.count() + 1):
        cur = popup.currentItem()
        if cur is not None and want(cur.data(Qt.UserRole)):
            break
        QTest.keyClick(popup, Qt.Key_Down)
        app.processEvents()
        cur = popup.currentItem()
        T.check(cur is not None, "Down did not highlight a row")
        hovered(cur.data(Qt.UserRole))
    else:
        raise T.Fail("no row in the popup matched")
    row = popup.currentRow()
    QTest.keyClick(popup, Qt.Key_Return)
    app.processEvents()
    T.check(not popup.isVisible(), "Enter did not close the popup")
    return row


# ---------------------------------------------------------------------------------------------------- 1. sketch
def scenario_sketch(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu
    from conftest import sketch_feature
    from freeshape.document import PartStudioDoc
    from freeshape.ui.select_other import SelectOtherPopup
    _app, w = T.new_window(TMP)
    ids = {}

    def build(s):
        # the construction line is added first, so a plain click (hit()) takes it and the solid line is the second row
        a, b = s.add_point(-10, 15), s.add_point(10, 15)
        ids["constr"] = s.add_line(a, b, True)
        c, d = s.add_point(-10, 15), s.add_point(10, 15)
        ids["solid"] = s.add_line(c, d)
    doc = PartStudioDoc()
    sid = sketch_feature(doc, build)
    w._add_tab(doc)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    w.edit_feature(sid)
    app.processEvents()
    T.check(w.mode == "sketch" and w.sketcher is not None, "the window did not enter sketch mode")
    vp, sk = w.viewport, w.sketcher
    T.check(vp.sketcher is sk and sk.tool == "select", "the sketcher is not live in the viewport")
    vp.resize(1000, 700)
    app.processEvents()
    square_on(vp, (0, 0, 0))
    solid, constr = ids["solid"], ids["constr"]
    T.check(sk.sk.entities[solid].get("construction") in (None, False) and sk.sk.entities[constr].get("construction"),
            "the sketch lines are not one solid and one construction")
    x, y = (float(v) for v in sk.scr(vp, (0.0, 15.0)))

    # a plain click picks the construction line (hit order): the solid one needs Select other
    T.check(sk.hit(vp, x, y, kinds=("entity",)) == ("entity", constr), sk.hit(vp, x, y, kinds=("entity",)))

    # the context menu offers the action over the two lines
    seen = []

    class RecMenu(QMenu):
        def addAction(self, *a):
            act = super().addAction(*a)
            seen.append((a[0] if a and isinstance(a[0], str) else act.text(), a[1] if len(a) > 1 else None))
            return act
    menu = RecMenu()
    sk.add_context_actions(menu, vp, x, y)
    T.check(any(t == "Select other…" for t, _ in seen), f"no Select other action in {[t for t, _ in seen]}")

    # backtick: pointer moved with a real mouse-move event, key delivered to the viewport
    move_to(vp, x, y)
    app.processEvents()
    T.check(sk._last_xy is not None, "the mouse-move did not reach the sketcher")
    press_backtick(vp)
    app.processEvents()
    pop = sk._popup
    T.check(isinstance(pop, SelectOtherPopup), f"backtick did not open the real popup ({pop!r})")
    payloads = [pop.item(i).data(Qt.UserRole) for i in range(pop.count())]
    labels = {pop.item(i).data(Qt.UserRole): pop.item(i).text() for i in range(pop.count())}
    T.check(solid in payloads and constr in payloads, f"popup rows {payloads} lack the two lines")
    T.check(labels[constr].endswith("(construction)") and not labels[solid].endswith("(construction)"), labels)
    hover_log = []
    row = choose_row(app, pop, lambda p: p == solid, hover_log.append)
    T.check(payloads[row] == solid, "the highlighted row is not the solid line's")
    T.check(hover_log and hover_log[-1] == solid, f"the hover callback saw {hover_log}")
    T.check(sk.selection == [solid], f"sketch selection is {sk.selection}, wanted exactly [{solid!r}]")
    T.check(sk.hover is None and sk._popup is None, "the popup left a hover or a reference behind")
    T.say(f"sketch: solid line {solid} chosen under construction line {constr} (row {row} of {len(payloads)}); "
          f"selection {sk.selection}")
    w.cancel_sketch()
    app.processEvents()
    T.check(w.mode == "idle", "the sketch did not close")


# ---------------------------------------------------------------------------------------------------- 2. studio
def scenario_studio(app):
    from PySide6.QtCore import Qt
    from conftest import block_doc, only_part
    from freeshape.ui.select_other import SelectOtherPopup
    _app, w = T.new_window(TMP)
    doc = block_doc(40, 20, 10)
    w._add_tab(doc)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    vp = w.viewport
    vp.select_filter = vp.user_filter = None
    app.processEvents()
    square_on(vp, (20, 10, 5))
    part = only_part(doc)
    faces = K.faces(part.shape)

    def face_pick_info(pk):
        T.check(pk.kind == "face", pk)
        return K.face_info(faces[pk.key[1]])

    x, y = at(vp, 20, 10, 10)
    front = vp.pick(x, y, kinds={"face"})
    T.check(abs(face_pick_info(front)["c"][2] - 10.0) < 1e-6, "a plain click is not the top face")
    move_to(vp, x, y)
    app.processEvents()
    press_backtick(vp)
    app.processEvents()
    pop = vp._other_popup
    T.check(isinstance(pop, SelectOtherPopup), f"backtick did not open the real popup ({pop!r})")
    pks = [pop.item(i).data(Qt.UserRole) for i in range(pop.count())]

    def is_back(pk):
        if pk.kind != "face":
            return False
        info = face_pick_info(pk)
        return abs(info["c"][2]) < 1e-6 and info["n"][2] < -0.9      # bottom face: centre at z = 0, normal -z
    T.check(sum(is_back(p) for p in pks) == 1, "the back face is not listed exactly once")
    back = next(p for p in pks if is_back(p))
    choose_row(app, pop, is_back, lambda p: None)
    T.check(len(vp.selection) == 1 and vp.selection[0] == back, f"selection is {vp.selection}, wanted {back}")
    info = face_pick_info(vp.selection[0])
    T.check(abs(info["c"][2]) < 1e-6 and abs(info["c"][0] - 20) < 1e-6 and abs(info["c"][1] - 10) < 1e-6
            and info["n"][2] < -0.9, f"the selected face is not the back face: {info}")
    T.check(vp.hover_through is False and vp._other_popup is None, "the list left hover state behind")
    T.say(f"part studio: back face {back.key} (centre {np.round(info['c'], 3).tolist()}, normal -z) selected "
          f"from the front face {front.key}")


# -------------------------------------------------------------------------------------------------- 3. assembly
def scenario_assembly(app):
    from PySide6.QtCore import Qt
    from conftest import add, block_doc, only_part
    from test_studio_connectors import connector_params
    from test_select_other_assembly import bottom_index
    from freeshape.ui.select_other import SelectOtherPopup
    _app, w = T.new_window(TMP)
    doc = block_doc(40, 20, 10)
    part = only_part(doc)
    add(doc, "mate_connector", **connector_params(doc, name="MC 1", index=bottom_index(part)))
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    T0 = np.eye(4)
    ia = w.doc.insert(w.studio_link(doc, part.id), T=T0)
    T1 = np.eye(4)
    T1[2, 3] = -20.0                                    # the second instance sits 20 mm behind the first
    ib = w.doc.insert(w.studio_link(doc, part.id), T=T1)
    w.doc.solve()
    w.asm.show_scene()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    vp = w.viewport
    vp.select_filter = vp.user_filter = None
    app.processEvents()
    cid = {iid: w.doc.published_connectors(iid)[0]["id"] for iid in (ia, ib)}
    T.check(w.doc.published_connectors(ia)[0]["name"] == "MC 1", w.doc.published_connectors(ia))

    for iid, other in ((ia, ib), (ib, ia)):
        w.asm.start_mate("fastened")
        d = w.asm.mate_dialog
        T.check(d is not None and vp.pick_handler == d.handle_pick, "the mate dialog is not taking picks")
        T.check(not d.mate.get("a"), "end A is already set")
        app.processEvents()
        square_on(vp, (20, 10, -5))
        fr = w.doc.world_frame(w.doc.named_connector(iid, cid[iid]))
        x, y = at(vp, *np.asarray(fr["o"], float))          # the connector's screen position
        move_to(vp, x, y)
        app.processEvents()
        press_backtick(vp)
        app.processEvents()
        pop = vp._other_popup
        T.check(isinstance(pop, SelectOtherPopup), f"backtick did not open the real popup ({pop!r})")
        rows = [(pop.item(i).text(), pop.item(i).data(Qt.UserRole)) for i in range(pop.count())]
        mine = [t for t, pk in rows if pk.kind == "connector" and pk.key == (iid, cid[iid])]
        T.check(mine == [f"Mate connector MC 1 of {w.doc.instance(iid)['name']}"], f"connector row not listed: {rows}")
        choose_row(app, pop, lambda pk: pk.kind == "connector" and pk.key == (iid, cid[iid]), lambda p: None)
        a_end = d.mate.get("a")
        T.check(a_end and a_end["inst"] == iid and a_end.get("named") == str(cid[iid]),
                f"mate end A is {a_end}, wanted connector {cid[iid]} of instance {iid}")
        T.check(a_end["inst"] != other, "the end landed on the other instance")
        T.say(f"assembly: mate end A = connector {a_end['named']} (MC 1) of instance {iid} ({w.doc.instance(iid)['name']}), "
              f"not instance {other}")
        d.reject()                                          # drop the half-made mate before the next round
        app.processEvents()
        T.check(w.mode == "idle" and w.asm.mate_dialog is None, "the mate dialog did not close")


def run():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    scenario_sketch(app)
    scenario_studio(app)
    scenario_assembly(app)


T.run_smoke("SO1", TMP, run)
