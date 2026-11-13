"""I1.3: Select other in a Part Studio. A 40 x 20 x 10 box in an offscreen main window, looked at square on from
above (+z): the top face is the front, the bottom face the back. The popup is a stand-in, so no real window opens."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402

from conftest import block_doc  # noqa: E402


class FakePopup:
    def __init__(self, rows, on_hover, on_choose, on_close):
        self.rows, self.on_hover, self.on_choose, self.on_close = rows, on_hover, on_choose, on_close
        self.closed = False

    def hover(self, i):
        self.on_hover(self.rows[i][1])

    def choose(self, i, mods=Qt.NoModifier):
        self.close()
        self.on_choose(self.rows[i][1], mods)

    def close(self):
        if not self.closed:
            self.closed = True
            self.on_close()


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.camera import basis
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w._add_tab(block_doc(40, 20, 10))
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    w.app = app
    w.popups = []

    def factory(vp, x, y, rows, on_hover, on_choose, on_close):
        p = FakePopup(rows, on_hover, on_choose, on_close)
        w.popups.append(p)
        return p
    w.viewport.popup_factory = factory
    w.viewport.cam.set_state((np.array([20.0, 10, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    w.viewport.select_filter = w.viewport.user_filter = None
    yield w
    w.deleteLater()


def at(win, *xyz):
    sx, sy, _z = win.viewport.cam.to_screen(np.array(xyz, float))
    return float(sx), float(sy)


def faces_of(rows):
    return [(t, pk) for t, pk in rows if pk.kind == "face"]


def test_rows_for_the_middle_of_the_front_face_hold_two_faces_front_first(win):
    vp = win.viewport
    x, y = at(win, 20, 10, 10)
    rows = vp.select_other_rows(x, y)
    faces = faces_of(rows)
    assert len(faces) == 2
    assert faces[0][1] == vp.pick(x, y, kinds={"face"})
    assert faces[0][0] == "Face of Part 1 (1)" and faces[1][0] == "Face of Part 1 (2)"
    assert all(t.startswith("Plane ") for t, pk in rows if pk.kind == "plane")


def test_labels_name_the_owner_and_number_repeats(win):
    vp = win.viewport
    labels = [t for t, _ in vp.select_other_rows(*at(win, 20, 0, 10))]
    assert "Edge of Part 1 (1)" in labels and "Edge of Part 1 (2)" in labels
    from freeshape.ui.viewport import Pick
    assert vp.other_label(Pick("plane", ("std", "Top"))) == "Plane Top"
    sid = next(f["id"] for f in win.doc.features if f["type"] == "sketch")
    assert vp.other_label(Pick("region", (sid, 0))) == f"Sketch region of {win.doc.feature(sid)['name']}"


def _open(win, xyz):
    x, y = at(win, *xyz)
    p = win.viewport.open_select_other(x, y)
    assert p is win.popups[-1]
    return p


def test_choosing_the_back_face_selects_it_alone_and_ctrl_adds(win):
    vp = win.viewport
    p = _open(win, (20, 10, 10))
    back = next(i for i, (_t, pk) in enumerate(p.rows) if pk.kind == "face" and pk != p.rows[0][1])
    want = p.rows[back][1]
    p.choose(back)
    assert vp.selection == [want]
    p = _open(win, (20, 10, 10))
    other = next(i for i, (_t, pk) in enumerate(p.rows) if pk.kind == "face" and pk != want)
    p.choose(other, Qt.ControlModifier)
    assert len(vp.selection) == 2 and want in vp.selection and p.rows[other][1] in vp.selection


def test_fillet_edge_field_lists_edges_only_and_takes_a_hidden_back_edge(win):
    win.start_feature("fillet")
    d = win.dialog
    fld = d.active_field
    assert fld is not None and "edge" in fld.want
    vp = win.viewport
    p = _open(win, (20, 0, 10))
    assert len(p.rows) >= 2 and all(pk.kind == "edge" for _t, pk in p.rows)
    p.choose(1)
    assert len(fld.items) == 1 and fld.items[0][0] == "edge"
    assert vp.selection == []                         # the dialog took it, as a click would
    win.cancel_dialog()


def test_hover_sets_viewport_hover_and_draw_through_and_close_clears_both(win):
    vp = win.viewport
    p = _open(win, (20, 10, 10))
    assert not vp.hover_through
    p.hover(1)
    assert vp.hover == p.rows[1][1] and vp.hover_through
    p.close()
    assert vp.hover is None and not vp.hover_through


def test_context_menu_offers_select_other_only_over_two_or_more_hits(win, monkeypatch):
    from freeshape.ui import mainwindow as MW
    seen = []

    class Menu(MW.QMenu):
        def exec(self, *a, **k):
            seen.append([a.text() for a in self.actions()])

    monkeypatch.setattr(MW, "QMenu", Menu)
    vp = win.viewport
    for xyz, expect in (((20, 10, 10), True), ((500, 500, 10), False)):
        x, y = at(win, *xyz)
        gpos = vp.mapToGlobal(QPoint(int(x), int(y)))
        win._viewport_context(vp.pick(x, y), gpos)
        assert ("Select other…" in seen[-1]) is expect, xyz
    one = vp.pick_all(*at(win, 20, 10, 10))[:1]
    monkeypatch.setattr(vp, "pick_all", lambda x, y, kinds=None: one)
    x, y = at(win, 20, 10, 10)
    win._viewport_context(vp.pick(x, y), vp.mapToGlobal(QPoint(int(x), int(y))))
    assert "Select other\u2026" not in seen[-1] and len(seen) == 3


def test_single_hit_gives_no_list(win):
    vp = win.viewport
    vp.user_filter = {"edge"}
    x, y = at(win, 20, 10, 10)
    assert vp.pick_all(x, y) == [] and vp.open_select_other(x, y) is None
    assert win.popups == []


def test_backtick_opens_the_list_in_the_studio_but_belongs_to_the_sketcher_in_a_sketch(win):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    vp = win.viewport
    vp._cursor_xy = at(win, 20, 10, 10)
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_QuoteLeft, Qt.NoModifier))
    assert len(win.popups) == 1
    vp._other_closed()

    seen = []

    class Sk:
        def key_press(self, vp_, e):
            seen.append(e.key())
            return True
    vp.sketcher = Sk()
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_QuoteLeft, Qt.NoModifier))
    vp.sketcher = None
    assert seen == [Qt.Key_QuoteLeft] and len(win.popups) == 1
