"""Planes hidden with P stay hidden until P shows them again (issue: "Planes keep reappearing after sketches").

The planes came back when the app was restarted, which happens each time a fix is deployed: the P state was never
saved. It is now kept in the app settings, with Shift+P's axes and K's mate connectors. The sketch, undo and tab
tests pin down that nothing in a session turns the planes back on either. Offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import TOP, rect_lines  # noqa: E402
from test_visibility_keys import _window, face, key, plane_keys, win  # noqa: E402,F401


def hide_planes(w):
    w.viewport.setFocus()
    key(Qt.Key_P)
    assert plane_keys(w) == set() and not w.act_planes.isChecked()


def assert_still_hidden(w):
    QApplication.processEvents()
    assert not w.act_planes.isChecked() and plane_keys(w) == set()


def draw_sketch(w, finish=True):
    w.viewport.set_selection([face(w.plate)])
    w.viewport.setFocus()
    key(Qt.Key_S, Qt.ShiftModifier)                      # Shift+S: a new sketch on the selected face
    if w.sketcher is None:
        w.sk_ctx["feature"]["params"]["plane"] = TOP
        w._enter_sketcher(animate=False)
    assert w.mode == "sketch"
    w.viewport.setFocus()
    key(Qt.Key_P)                                        # in a sketch P is the Point tool, not the planes
    assert w.sketcher.tool == "point"
    rect_lines(w.sketcher.sk, 200, 0, 10, 10)
    w._sketch_changed(True)
    (w.finish_sketch if finish else w.cancel_sketch)()
    assert w.mode == "idle"


@pytest.fixture
def ini_window(tmp_path, monkeypatch):
    """Make windows that keep their settings in one ini file, as two runs of the app on one machine do."""
    ini = tmp_path / "settings.ini"
    real = QSettings
    monkeypatch.setattr("PySide6.QtCore.QSettings", lambda *a: real(str(ini), real.IniFormat))
    made = []

    def make():
        w = _window(tmp_path, monkeypatch)
        made.append(w)
        return w
    yield make
    for w in made:
        w.hide()
        w.deleteLater()


def test_planes_hidden_with_p_stay_hidden_after_a_restart(ini_window):
    w = ini_window()
    assert w.act_planes.isChecked() and ("std", "Top") in plane_keys(w)    # shown on a first run
    hide_planes(w)
    w.close()
    w2 = ini_window()
    assert not w2.act_planes.isChecked() and plane_keys(w2) == set()
    w2.viewport.setFocus()
    key(Qt.Key_P)                                        # shown again, and that is kept too
    assert ("std", "Top") in plane_keys(w2)
    w2.close()
    assert ini_window().act_planes.isChecked()


def test_shift_p_and_k_are_kept_across_a_restart(ini_window):
    w = ini_window()
    w.viewport.setFocus()
    key(Qt.Key_P, Qt.ShiftModifier)                      # planes, axes and connectors all off
    assert not (w.act_planes.isChecked() or w.show_axes or w.show_connectors)
    w.close()
    w2 = ini_window()
    assert not (w2.act_planes.isChecked() or w2.show_axes or w2.show_connectors)
    w2.viewport.setFocus()
    key(Qt.Key_K)                                        # connectors back on their own
    w2.close()
    w3 = ini_window()
    assert w3.show_connectors and not w3.show_axes and not w3.act_planes.isChecked()


def test_view_menu_show_planes_is_kept_across_a_restart(ini_window):
    w = ini_window()
    w.act_planes.trigger()                               # View > Show planes
    assert not w.act_planes.isChecked() and plane_keys(w) == set()
    w.close()
    assert not ini_window().act_planes.isChecked()


def test_finishing_a_new_sketch_keeps_planes_hidden(win):
    hide_planes(win)
    draw_sketch(win)
    assert_still_hidden(win)


def test_cancelling_a_sketch_keeps_planes_hidden(win):
    hide_planes(win)
    draw_sketch(win, finish=False)
    assert_still_hidden(win)


def test_editing_a_sketch_keeps_planes_hidden(win):
    hide_planes(win)
    win.start_sketch(edit_fid=next(iter(win.doc.state.sketches)))
    assert win.mode == "sketch"
    win.finish_sketch()
    assert_still_hidden(win)


def test_undo_and_redo_of_a_sketch_keep_planes_hidden(win):
    hide_planes(win)
    draw_sketch(win)
    win.doc.undo()
    assert_still_hidden(win)
    win.doc.redo()
    assert_still_hidden(win)


def test_switching_tabs_keeps_planes_hidden(win):
    hide_planes(win)
    first = win.tabs.currentIndex()
    win._add_tab(win._new_document())
    QApplication.processEvents()
    assert win.tabs.currentIndex() != first and plane_keys(win) == set()
    win.tabs.setCurrentIndex(first)
    assert_still_hidden(win)
