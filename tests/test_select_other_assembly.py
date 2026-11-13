"""I1.4: Select other in an Assembly. Two instances of a 40 x 20 x 10 block (a Part Studio with a mate connector on
the bottom face), one directly behind the other when seen from above (+z): A at z 0..10, B at z -20..-10. The popup is
a stand-in, so no real window opens."""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from conftest import add, block_doc, only_part  # noqa: E402
from test_select_other_studio import FakePopup  # noqa: E402
from test_studio_connectors import connector_params  # noqa: E402


def bottom_index(part):
    from freeshape import kernel as K
    return next(i for i, f in enumerate(K.faces(part.shape)) if (K.face_info(f).get("n") or [0, 0, 0])[2] < -0.9)


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.camera import basis
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc = block_doc(40, 20, 10)
    part = only_part(doc)
    add(doc, "mate_connector", **connector_params(doc, name="MC 1", index=bottom_index(part)))
    w._add_tab(doc)
    w.new_assembly()
    app.processEvents()
    T = np.eye(4)
    w.ia = w.doc.insert(w.studio_link(doc, part.id), T=T)
    T = np.eye(4)
    T[2, 3] = -20.0
    w.ib = w.doc.insert(w.studio_link(doc, part.id), T=T)
    w.doc.solve()
    w.asm.show_scene()
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    w.app = app
    w.popups = []

    def factory(vp, x, y, rows, on_hover, on_choose, on_close):
        p = FakePopup(rows, on_hover, on_choose, on_close)
        w.popups.append(p)
        return p
    vp = w.viewport
    vp.popup_factory = factory
    vp.cam.set_state((np.array([20.0, 10, -5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    vp.select_filter = vp.user_filter = None
    yield w
    w.deleteLater()


def at(win, *xyz):
    sx, sy, _z = win.viewport.cam.to_screen(np.array(xyz, float))
    return float(sx), float(sy)


def rows_at(win, *xyz):
    return win.viewport.select_other_rows(*at(win, *xyz))


def faces_of(rows):
    return [(t, pk) for t, pk in rows if pk.kind == "face"]


def conn_rows(rows):
    return [(t, pk) for t, pk in rows if pk.kind == "connector"]


def test_rows_list_a_face_of_each_instance_front_first(win):
    rows = rows_at(win, 5, 5, 10)                         # off the connectors, which sit at the face centres
    faces = faces_of(rows)
    owners = [pk.key[0] for _t, pk in faces]
    assert owners == [win.ia, win.ia, win.ib, win.ib]      # A's top and bottom, then B's: front instance first
    assert faces[0][1] == win.viewport.pick(*at(win, 5, 5, 10), kinds={"face"})
    names = {win.ia: win.doc.instance(win.ia)["name"], win.ib: win.doc.instance(win.ib)["name"]}
    assert all(t.startswith(f"Face of {names[pk.key[0]]}") for t, pk in faces)


def test_labels_use_the_instances_list_names(win):
    a = win.doc.instance(win.ia)["name"]
    assert a.endswith(" <1>") or "<" in a                  # the unique name the Instances list shows
    vp = win.viewport
    from freeshape.ui.viewport import Pick
    assert vp.other_label(Pick("part", (win.ia,))) == a
    assert vp.other_label(Pick("face", (win.ia, 0))) == f"Face of {a}"
    cid = win.doc.published_connectors(win.ia)[0]["id"]
    assert vp.other_label(Pick("connector", (win.ia, cid))) == f"Mate connector MC 1 of {a}"


def _start_mate(win):
    win.asm.start_mate("fastened")
    d = win.asm.mate_dialog
    assert d is not None and win.viewport.pick_handler == d.handle_pick
    return d


def _choose(win, xyz, pred):
    x, y = at(win, *xyz)
    p = win.viewport.open_select_other(x, y)
    assert p is win.popups[-1]
    i = next(i for i, (_t, pk) in enumerate(p.rows) if pred(pk))
    p.choose(i)
    return p


def test_choosing_the_rear_instances_face_sets_the_first_mate_end_to_it(win):
    d = _start_mate(win)
    assert d.active is d.fa and not d.mate.get("a")
    _choose(win, (5, 5, 10), lambda pk: pk.kind == "face" and pk.key[0] == win.ib)
    assert d.mate["a"]["inst"] == win.ib and d.mate["a"]["kind"] == "face"
    assert d.mate.get("b") is None and d.active is d.fb


def test_a_connector_on_the_back_face_is_listed_and_becomes_the_end(win):
    cid = win.doc.published_connectors(win.ia)[0]["id"]
    rows = rows_at(win, 20, 10, 10)                       # square on to the middle of A's top face
    mine = [t for t, pk in conn_rows(rows) if pk.key == (win.ia, cid)]
    assert mine == [f"Mate connector MC 1 of {win.doc.instance(win.ia)['name']}"]
    ts = [t for t, _ in rows]
    assert ts.index(mine[0]) > next(i for i, (_t, pk) in enumerate(rows) if pk.kind == "face" and pk.key[0] == win.ia)
    d = _start_mate(win)
    _choose(win, (20, 10, 10), lambda pk: pk.kind == "connector" and pk.key[0] == win.ia)
    assert d.mate["a"]["inst"] == win.ia and d.mate["a"]["named"] == str(cid)


def test_a_chosen_face_is_not_taken_over_by_a_triad_under_the_pointer(win):
    d = _start_mate(win)
    win.viewport.last_click = at(win, 20, 10, 0)           # an earlier click, on a connector
    _choose(win, (20, 10, 10), lambda pk: pk.kind == "face" and pk.key[0] == win.ib)
    assert d.mate["a"]["inst"] == win.ib and d.mate["a"]["kind"] == "face"


def test_hidden_mate_connectors_are_not_listed(win):
    assert conn_rows(rows_at(win, 20, 10, 10))
    win.asm.panel.vis_actions["connectors"].trigger()
    assert win.viewport.triads == [] and conn_rows(rows_at(win, 20, 10, 10)) == []
    win.asm.panel.vis_actions["connectors"].trigger()
    assert conn_rows(rows_at(win, 20, 10, 10))


def test_a_hidden_instances_connector_is_not_listed(win):
    win.doc.instance(win.ib)["hidden"] = True
    win.asm.show_scene()
    assert {pk.key[0] for _t, pk in conn_rows(rows_at(win, 20, 10, 10))} == {win.ia}


def test_idle_choosing_an_instance_row_selects_it_in_the_instances_list(win):
    vp = win.viewport
    p = _choose(win, (5, 5, 10), lambda pk: pk.kind == "face" and pk.key[0] == win.ib)
    assert p.closed and vp.selection and vp.selection[0].key[0] == win.ib
    assert set(win.asm.panel.selected_instances()) == {win.ib}


def test_idle_choosing_a_connector_row_changes_nothing(win):
    vp = win.viewport
    _choose(win, (20, 10, 10), lambda pk: pk.kind == "connector")
    assert vp.selection == [] and win.asm.panel.selected_instances() == []


def test_pick_and_the_kind_filter_ignore_connectors(win):
    vp = win.viewport
    x, y = at(win, 20, 10, 10)
    assert all(pk.kind != "connector" for pk in vp.pick_all(x, y, kinds={"face"}))
    assert vp.pick(x, y).kind != "connector"


def test_backtick_opens_the_assembly_list(win):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    vp = win.viewport
    vp._cursor_xy = at(win, 5, 5, 10)
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_QuoteLeft, Qt.NoModifier))
    p = win.popups[-1]
    a = win.doc.instance(win.ia)["name"]
    assert any(t.startswith("Face of ") and a in t for t, _ in p.rows)


def test_right_click_menu_offers_select_other_with_two_or_more_rows(win, monkeypatch):
    from PySide6 import QtWidgets
    from PySide6.QtCore import QPoint
    seen = []

    class RecMenu:
        def __init__(self, *a):
            self.entries = []

        def addAction(self, text, fn=None):
            self.entries.append((text, fn))
            return types.SimpleNamespace(setObjectName=lambda *a: None, setEnabled=lambda *a: None)

        def addMenu(self, *a):
            return RecMenu()

        def addSeparator(self):
            pass

        def exec(self, *a):
            seen.append(self.entries)

    monkeypatch.setattr(QtWidgets, "QMenu", RecMenu)
    vp = win.viewport
    for pk_xyz, expect in (((5, 5, 10), True), ((200, 200, 0), False)):
        seen.clear()
        x, y = at(win, *pk_xyz)
        g = vp.mapToGlobal(QPoint(int(x), int(y)))
        pk = vp.pick(x, y)
        win._viewport_context(pk, g)
        texts = [t for t, _ in seen[0]]
        assert ("Select other…" in texts) is expect
        if expect:
            fn = next(f for t, f in seen[0] if t.startswith("Select other"))
            fn()
            assert win.popups[-1].rows[0][1].kind == "face"
