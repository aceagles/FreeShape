"""A sketch left open when another tab is shown is still open, as it was, when its tab is shown again."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from conftest import TOP, rect_lines, sketch_feature  # noqa: E402


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


@pytest.fixture
def w():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    win = MainWindow()
    win.settings = _Settings()
    app.processEvents()
    yield win
    win._leave_modes()
    win.deleteLater()
    app.processEvents()


def _other_tab(w):
    """A second Part Studio tab, added without showing it. Returns its document."""
    other = w._new_document()
    w._add_tab(other, switch=False)
    return other


def _new_sketch(w):
    """A new sketch on Top with a rectangle, drawn in two undoable steps."""
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    k = w.sketcher
    k.push_undo()
    rect_lines(k.sk, 0, 0, 10, 10)
    k.push_undo()
    rect_lines(k.sk, 20, 0, 5, 5)
    k.sk.solve()
    k.set_tool("circle")
    return k


def _assert_idle(w):
    vp = w.viewport
    assert w.mode == "idle" and w.sketcher is None and w.dialog is None
    assert vp.sketcher is None and vp.pick_handler is None and vp.select_filter is None
    assert w.tool_stack.currentIndex() == 0


def test_new_sketch_survives_a_round_trip_to_another_tab(w):
    doc = w.doc
    other = _other_tab(w)
    k = _new_sketch(w)
    panel, fid, drawn = w.dialog, w.sk_ctx["feature"]["id"], k.sk.to_dict()
    cam = w.viewport.cam.state()

    w.tabs.setCurrentIndex(1)
    assert w.doc is other
    _assert_idle(w)                                   # the other tab sees no half-open sketch
    assert panel.isHidden()
    assert doc.features == [] and not doc.dirty       # and nothing was put into the document behind its back
    assert not doc.can_undo()

    w.tabs.setCurrentIndex(0)
    assert w.doc is doc and w.mode == "sketch"
    assert w.sketcher is k and w.viewport.sketcher is k           # the same session: undo history and tool too
    assert w.sk_ctx["feature"]["id"] == fid and w.sk_ctx["is_new"]
    assert k.sk.to_dict() == drawn
    assert k.tool == "circle" and w._sketch_buttons["circle"].isChecked()
    assert w.dialog is panel and not panel.isHidden()
    assert w.tool_stack.currentIndex() == 1
    for a, b in zip(w.viewport.cam.state(), cam):                 # looking at it as before
        assert abs(a - b).max() < 1e-9
    w.undo()                                                      # the sketch's own undo still steps back
    assert len(k.sk.to_dict()["entities"]) < len(drawn["entities"])
    w.redo()
    assert k.sk.to_dict() == drawn


def test_finishing_after_a_round_trip_gives_the_same_feature(w):
    _other_tab(w)
    _new_sketch(w)
    w.tabs.setCurrentIndex(1)
    w.tabs.setCurrentIndex(0)
    w.finish_sketch()
    _assert_idle(w)
    there_and_back = w.doc.features

    w.new_doc()                                       # the same sketch, finished without leaving its tab
    _new_sketch(w)
    w.finish_sketch()
    assert len(there_and_back) == 1 and w.doc.features == there_and_back
    assert not w.doc.errors


def test_editing_an_existing_sketch_survives_a_round_trip(w):
    doc = w.doc
    fid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    before = doc.feature(fid)["params"]["sketch"]
    other = _other_tab(w)
    w.start_sketch(fid)
    k = w.sketcher
    k.push_undo()
    rect_lines(k.sk, 20, 0, 5, 5)
    k.sk.solve()
    edited = k.sk.to_dict()
    assert edited != before

    w.tabs.setCurrentIndex(1)
    assert w.doc is other
    _assert_idle(w)
    assert doc.feature(fid)["params"]["sketch"] == before         # not committed while away

    w.tabs.setCurrentIndex(0)
    assert w.mode == "sketch" and w.sketcher is k and w.viewport.sketcher is k
    assert w.sk_ctx["feature"]["id"] == fid and not w.sk_ctx["is_new"]
    assert k.sk.to_dict() == edited
    w.finish_sketch()
    assert doc.feature(fid)["params"]["sketch"] == edited
    doc.undo()                                                    # one step in the document, as for any edit
    assert doc.feature(fid)["params"]["sketch"] == before


def test_each_tab_keeps_its_own_sketch(w):
    doc = w.doc
    other = _other_tab(w)
    k0 = _new_sketch(w)
    w.tabs.setCurrentIndex(1)
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    k1 = w.sketcher
    rect_lines(k1.sk, 0, 0, 3, 3)
    w.tabs.setCurrentIndex(0)
    assert w.sketcher is k0 and w.viewport.sketcher is k0
    w.tabs.setCurrentIndex(1)
    assert w.sketcher is k1 and len(k1.sk.to_dict()["entities"]) == 4
    w.finish_sketch()
    assert len(other.features) == 1 and doc.features == []
    w.tabs.setCurrentIndex(0)
    w.finish_sketch()
    assert len(doc.features) == 1


def test_a_new_sketch_with_nothing_drawn_is_dropped(w):
    doc = w.doc
    _other_tab(w)
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    w.tabs.setCurrentIndex(1)
    _assert_idle(w)
    w.tabs.setCurrentIndex(0)
    _assert_idle(w)
    assert doc.features == [] and not doc.dirty and not w._parked


def test_opening_a_new_tab_keeps_the_sketch(w):
    doc = w.doc
    k = _new_sketch(w)
    w.new_doc()
    assert w.doc is not doc
    _assert_idle(w)
    w.tabs.setCurrentIndex(0)
    assert w.mode == "sketch" and w.sketcher is k


def test_closing_a_tab_whose_sketch_is_parked_drops_it(w):
    doc = w.doc
    other = _other_tab(w)
    _new_sketch(w)
    w.tabs.setCurrentIndex(1)
    w.close_tab(0)                                    # closing the tab cancels its sketch, as it always did
    assert w.doc is other and id(doc) not in w.docs
    _assert_idle(w)
    assert not w._parked
