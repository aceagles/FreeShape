"""I6.4: Select through context. A 40 x 20 x 10 block (the edited part) seen square on from above (+z) with a Context
body, a 60 x 40 x 2 plate floating at z 14..16 over it and 10 mm past it on every side. The Context body is marked the way
set_parts marks one today: the 7th element of its tuple, which fills Viewport.reference_ids. Offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

from conftest import block_doc, only_part  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from test_select_other_studio import FakePopup, at  # noqa: E402

CTX = "ctx"


def _rig(tmp_path, monkeypatch, reference=True, ini=None):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    if ini is not None:
        real = QSettings
        monkeypatch.setattr("PySide6.QtCore.QSettings", lambda *a: real(str(ini), real.IniFormat))
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
    vp = w.viewport
    plate = K.translate(only_part(block_doc(60, 40, 2, x0=-10, y0=-10)).shape, (0, 0, 14))
    parts = [(pid, vp.part_shapes[pid], col, None, None, None, False) for pid, _m, col in vp.parts]
    parts.append((CTX, plate, (0.6, 0.6, 0.9), None, None, None, reference))
    vp.set_scene(parts, vp.planes, [(f, sr) for f, sr, _m in vp.sketches],
                 vp.axes)
    vp.cam.set_state((np.array([20.0, 10, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))
    vp.select_filter = vp.user_filter = None
    w.popups = []

    def factory(vp_, x, y, rows, h, c, cl):
        p = FakePopup(rows, h, c, cl)
        w.popups.append(p)
        return p
    vp.popup_factory = factory
    return w


@pytest.fixture
def win(tmp_path, monkeypatch):
    w = _rig(tmp_path, monkeypatch)
    yield w
    w.deleteLater()


def set_option(win, on):
    win.set_select_through_context(on, save=False)


def pid_of(pk):
    return pk.key[0]


def test_marker_is_the_reference_flag(win):
    assert win.viewport.reference_ids == {CTX} and win.viewport.pick_through_ids == {CTX}
    set_option(win, False)
    assert win.viewport.pick_through_ids == frozenset()


def test_context_face_over_a_part_face(win):
    vp = win.viewport
    x, y = at(win, 20, 10, 10)
    set_option(win, True)
    on = vp.pick(x, y, kinds={"face"})
    assert on.kind == "face" and pid_of(on) != CTX
    set_option(win, False)
    off = vp.pick(x, y, kinds={"face"})
    assert off.kind == "face" and pid_of(off) == CTX
    for opt in (True, False):
        set_option(win, opt)
        picks = vp.pick_all(x, y, {"face"})
        assert pid_of(picks[0]) == CTX and on in picks and off in picks, opt


def test_part_edge_behind_a_context_face(win):
    vp = win.viewport
    x, y = at(win, 20, 0, 10)                   # the middle of the block's front top edge, under the plate
    set_option(win, True)
    pk = vp.pick(x, y, kinds={"edge"})
    assert pk is not None and pk.kind == "edge" and pid_of(pk) != CTX
    set_option(win, False)
    assert vp.pick(x, y, kinds={"edge"}) is None        # the Context face hides it, as before


def test_only_the_context_body_under_the_cursor(win):
    vp = win.viewport
    x, y = at(win, -5, 5, 16)
    for opt in (True, False):
        set_option(win, opt)
        pk = vp.pick(x, y, kinds={"face"})
        assert pk.kind == "face" and pid_of(pk) == CTX, opt


def test_a_plane_behind_a_context_face_does_not_win(win):
    vp = win.viewport
    x, y = at(win, -5, 5, 16)
    vp.show_planes = True
    assert any(pk.kind == "plane" for pk in vp.pick_all(x, y))      # a default plane lies behind
    set_option(win, True)
    pk = vp.pick(x, y)
    assert pk.kind == "face" and pid_of(pk) == CTX


def test_box_select_skips_context_bodies(win):
    vp = win.viewport
    rect = (0, 0, vp.width(), vp.height())
    set_option(win, True)
    got = vp._items_in_rect(rect, True)
    assert got and all(pk.key[0] != CTX for pk in got if pk.kind in ("face", "edge", "vertex", "part"))
    set_option(win, False)
    assert any(pk.key[0] == CTX for pk in vp._items_in_rect(rect, True))


def test_dialog_field_follows_the_rule_and_select_other_delivers_the_context_row(win):
    vp = win.viewport
    got = []
    vp.select_filter = {"face"}
    vp.pick_handler = got.append
    x, y = at(win, 20, 10, 10)
    set_option(win, True)
    vp.click_pick(vp.pick(x, y))
    assert len(got) == 1 and got[0].kind == "face" and pid_of(got[0]) != CTX
    p = vp.open_select_other(x, y)
    row = next(i for i, (_t, pk) in enumerate(p.rows) if pid_of(pk) == CTX)
    p.choose(row)
    assert len(got) == 2 and pid_of(got[1]) == CTX


def test_shift_t_transparent_body_is_still_picked_in_front(tmp_path, monkeypatch):
    w = _rig(tmp_path, monkeypatch, reference=False)        # an ordinary part, made see-through with Shift+T
    vp = w.viewport
    vp.set_transparent({CTX})
    x, y = at(w, 20, 10, 10)
    for opt in (True, False):
        set_option(w, opt)
        pk = vp.pick(x, y, kinds={"face"})
        assert pk.kind == "face" and pid_of(pk) == CTX, opt
    w.deleteLater()


def test_option_defaults_on_and_survives_a_new_window(tmp_path, monkeypatch):
    ini = tmp_path / "settings.ini"
    w = _rig(tmp_path, monkeypatch, ini=ini)
    assert w.viewport.select_through is True and w.act_select_through.isChecked()
    w.act_select_through.trigger()
    assert w.viewport.select_through is False and w.settings.value("select_through_context") == "false"
    w.settings.sync()
    w.deleteLater()
    w2 = _rig(tmp_path, monkeypatch, ini=ini)
    assert w2.viewport.select_through is False and not w2.act_select_through.isChecked()
    w2.deleteLater()


def test_command_search_finds_it(win):
    from freeshape.ui.palette import search_commands
    cmds = win._palette_commands()
    for text in ("select transparent geometry", "click through", "context", "select through context"):
        names = [c.name for c in search_commands(cmds, text)]
        assert "Select through context" in names, text
