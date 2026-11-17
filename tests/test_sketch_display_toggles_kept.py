"""The Sketch panel's Show constraints and Show sketch regions keep their last setting from one sketch to the next
and across a restart, like See through solids (issue: "unchecking 'show constraints' etc doesn't persist between
sketch entries"). Offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from conftest import TOP, rect_lines  # noqa: E402
from test_planes_hidden_sticks import ini_window  # noqa: E402,F401
from test_visibility_keys import _window, win  # noqa: E402,F401


def open_sketch(w, edit_fid=None):
    w.start_sketch(edit_fid=edit_fid)
    if w.sketcher is None:
        w.sk_ctx["feature"]["params"]["plane"] = TOP
        w._enter_sketcher(animate=False)
    assert w.mode == "sketch"


def toggles(w):
    d, s = w.dialog, w.sketcher
    return (d.show_cons.isChecked(), s.show_constraints, d.show_regions.isChecked(), s.show_regions)


@pytest.mark.parametrize("leave", ["finish", "cancel"])
def test_constraints_and_regions_stay_off_in_the_next_sketch(win, leave):
    open_sketch(win)
    assert toggles(win) == (True, True, True, True)          # shown by default
    win.dialog.show_cons.setChecked(False)
    win.dialog.show_regions.setChecked(False)
    assert toggles(win) == (False, False, False, False)
    rect_lines(win.sketcher.sk, 200, 0, 10, 10)
    (win.finish_sketch if leave == "finish" else win.cancel_sketch)()
    open_sketch(win)                                         # a new sketch
    assert toggles(win) == (False, False, False, False)
    win.cancel_sketch()
    open_sketch(win, edit_fid=next(iter(win.doc.state.sketches)))     # editing one already there
    assert toggles(win) == (False, False, False, False)
    win.dialog.show_cons.setChecked(True)                    # turned back on: on next time
    win.cancel_sketch()
    open_sketch(win)
    assert toggles(win) == (True, True, False, False)
    win.cancel_sketch()


def test_sketch_toggles_are_kept_across_a_restart(ini_window):
    from test_connector_frames import plate_studio
    w = ini_window()
    w._add_tab(plate_studio()[0])
    open_sketch(w)
    w.dialog.show_cons.setChecked(False)
    w.cancel_sketch()
    w.hide()                     # (close() would ask to save the edited Part Studio)
    w2 = ini_window()
    w2._add_tab(plate_studio()[0])
    open_sketch(w2)
    assert toggles(w2) == (False, False, True, True)
    w2.cancel_sketch()
