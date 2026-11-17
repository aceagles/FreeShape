"""The show / hide eye on a feature-list row, and showing a hidden sketch.

- Clicking an eye only shows or hides: it never selects rows (it used to drag-select every row above it when the
  mouse moved a pixel during the click). Esc and a click on empty list space clear a selection.
- With the engine on, a sketch on a face that a later feature changed (a chamfer) still reaches the window's
  state, so showing it draws it and hovering its row outlines its curves. The window used to re-run the sketch
  against the finished parts, where its face no longer resolves, and silently dropped it.
Offscreen."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import (add, block_doc, both, centre_near, edge_refs, line_along, only_part, rect_lines,  # noqa: E402
                      sketch_feature)
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.engine.encode import sketch_out  # noqa: E402
from freeshape.ui.panels import ROLE_ID, eye_rect  # noqa: E402
from freeshape.ui.partstudio_engine import StudioEngine  # noqa: E402


def _send(widget, typ, pt, button, buttons):
    ev = QMouseEvent(typ, QPointF(pt), widget.mapToGlobal(QPointF(pt)), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)
    QApplication.processEvents()


def _click(lst, pt, wobble=QPoint(2, 1)):
    """A left click at `pt` on the list, the mouse moving a little while the button is down (as a hand does)."""
    vp = lst.viewport()
    _send(vp, QEvent.MouseMove, pt, Qt.NoButton, Qt.NoButton)
    _send(vp, QEvent.MouseButtonPress, pt, Qt.LeftButton, Qt.LeftButton)
    _send(vp, QEvent.MouseMove, pt + wobble, Qt.NoButton, Qt.LeftButton)
    _send(vp, QEvent.MouseButtonRelease, pt + wobble, Qt.LeftButton, Qt.NoButton)


def _eye_point(lst, ident):
    it = next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(ROLE_ID) == ident)
    return eye_rect(lst.visualItemRect(it), lst.viewport().rect().right()).center()


def _selected(lst):
    return [it.data(ROLE_ID) for it in lst.selectedItems()]


def _face_sketch_doc():
    """A 40 x 20 x 10 block (F1 sketch, F2 extrude), a sketch F3 on its top face, then a chamfer on a top edge
    (F4): F3's face is changed after F3, so it no longer resolves in the finished part."""
    doc = block_doc()
    part = only_part(doc)
    top = next(f for f in K.faces(part.shape) if abs(K.face_info(f)["c"][2] - 10) < 1e-6)
    plane = {"kind": "face", "face": F.make_ref(part.id, top, "face", part.shape, doc.state.named(part.id))}
    sid = sketch_feature(doc, lambda s: rect_lines(s, 5, 5, 10, 5), plane)
    add(doc, "chamfer", edges=edge_refs(only_part(doc), both(line_along([1, 0, 0]), centre_near([20, 0, 10])),
                                        expect=1), distance=2.0)
    doc.hidden_features = {"F1", sid}            # both sketches hidden, as after extruding them
    return doc, sid


def _engine_apply(doc):
    """Lay the engine's result over `doc` as StudioEngine._apply does, with the parts left out: only the sketches,
    planes and axes the engine sends can reach the window's state."""
    st = doc.state
    res = SimpleNamespace(parts=[], context=[], connectors=[], variables={}, planes={}, axes={}, errors={},
                          error_refs={}, copies=None, revision=1,
                          snap={"features": doc.features, "rollback": None, "configs": None},
                          sketches={fid: sketch_out(fid, fid, r) for fid, r in st.sketches.items()})
    StudioEngine._apply(SimpleNamespace(window=None), SimpleNamespace(doc=doc), res)


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    QTest.qWaitForWindowExposed(w)
    app.processEvents()
    yield w
    w.hide()
    w.deleteLater()


def _open(w, doc):
    w._add_tab(doc)
    QApplication.processEvents()
    assert w.doc is doc
    return w.left.features


def test_eye_click_shows_the_sketch_and_leaves_the_selection_alone(win):
    doc = block_doc()
    doc.hidden_features = {"F1"}
    lst = _open(win, doc)
    _click(lst, _eye_point(lst, "F1"))
    assert doc.hidden_features == set()
    assert _selected(lst) == []                              # was: Top, Front, Right and Sketch 1
    assert [s[0] for s in win.viewport.sketches] == ["F1"]
    lst.clearSelection()
    lst.item(next(i for i in range(lst.count()) if lst.item(i).data(ROLE_ID) == "F2")).setSelected(True)
    _click(lst, _eye_point(lst, "F1"))                       # hide it again, with Extrude 1 picked
    assert doc.hidden_features == {"F1"} and _selected(lst) == ["F2"]
    assert win.viewport.sketches == []


def test_esc_and_a_click_on_empty_space_clear_the_list_selection(win):
    lst = _open(win, block_doc())
    lst.selectAll()
    assert _selected(lst)
    win._escape()
    assert _selected(lst) == []
    lst.selectAll()
    last = lst.visualItemRect(lst.item(lst.count() - 1))
    empty = QPoint(20, last.bottom() + 20)
    assert lst.itemAt(empty) is None
    _click(lst, empty, QPoint(0, 0))
    assert _selected(lst) == []


def test_parts_list_eye_click_does_not_select(win):
    doc = block_doc()
    _open(win, doc)
    parts = win.left.parts
    pid = only_part(doc).id
    _click(parts, _eye_point(parts, pid))
    assert doc.hidden_parts == {pid} and _selected(parts) == []


def test_engine_state_keeps_a_sketch_on_a_face_changed_later():
    doc, sid = _face_sketch_doc()
    want = doc.state.sketches[sid]
    _engine_apply(doc)
    got = doc.state.sketches.get(sid)
    assert got is not None, list(doc.state.sketches)
    assert abs(got.plane.origin[2] - 10) < 1e-6 and len(got.regions) == len(want.regions) == 1
    assert got.region_names == want.region_names


def test_showing_or_hovering_a_hidden_face_sketch_draws_it(win):
    doc, sid = _face_sketch_doc()
    _open(win, doc)
    _engine_apply(doc)
    win._feature_hovered(sid)                                # hovered while hidden: its curves are outlined
    assert win.viewport.feature_hl is not None and len(win.viewport.feature_hl["polys"]) == 4
    win._feature_hovered(None)
    win._tree_eye("feature:" + sid)
    assert sid in [s[0] for s in win.viewport.sketches]
