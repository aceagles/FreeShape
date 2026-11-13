"""AnimateDialog and Animate… in the mate menu (I6.2)."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from test_golden_mates import _face_mate  # noqa: E402
from test_mate_limits_ui import _ctl, _menu_actions, qapp  # noqa: E402,F401


def _dialog(a, mid, qapp):
    from freeshape.ui.assembly_ui import AnimateDialog
    ctl = _ctl(a)
    d = AnimateDialog(ctl, mid)
    return ctl, d


def _state(a):
    return json.dumps([a.instances, a.mates, a.errors, a.dof], sort_keys=True, default=str)


def _set(d, start, end, steps, mode):
    d.start_edit.setText(str(start))
    d.end_edit.setText(str(end))
    d.steps_edit.setText(str(steps))
    d.playback.setCurrentIndex(d.playback.findData(mode))


def test_single_run_shows_end_then_restores(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    before = _state(a)
    _ctl_, d = _dialog(a, m["id"], qapp)
    _set(d, 0, 40, 5, "single")
    d.play()
    assert d.running and d.play_btn.text() == "Stop"
    for _ in range(5):
        d._tick()
    assert float(d.current.text()) == pytest.approx(40.0, abs=1e-3)
    assert not d.running and d.play_btn.text() == "Play"
    assert _state(a) == before
    d._close()


def test_defaults_follow_limits_per_end(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    a.set_limits(m["id"], "tz", 5, 50)
    _c, d = _dialog(a, m["id"], qapp)
    assert (d.start_edit.text(), d.end_edit.text()) == ("5", "50")
    d._close()
    a.set_limits(m["id"], "tz", None, 50)
    _c, d = _dialog(a, m["id"], qapp)
    assert (d.start_edit.text(), d.end_edit.text()) == ("0", "50")
    d._close()


def test_defaults_without_limits(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    _c, d = _dialog(a, m["id"], qapp)
    from freeshape.ui.numparse import fmt
    assert (d.start_edit.text(), d.end_edit.text()) == ("0", fmt(a.length_scale()))
    d._close()
    a2, m2, _p, _b = _face_mate("revolute")
    _c, d2 = _dialog(a2, m2["id"], qapp)
    assert (d2.start_edit.text(), d2.end_edit.text(), d2.steps_edit.text()) == ("0", "360", "60")
    assert d2.dof_row.isHidden()
    d2._close()


def test_freedom_row_for_multi_freedom_mate(qapp):
    a, m, _ip, _ib = _face_mate("cylindrical")
    _c, d = _dialog(a, m["id"], qapp)
    assert not d.dof_row.isHidden() and d.dof_box.count() == 2
    d.dof_box.setCurrentIndex(d.dofs.index("rz"))
    assert d.end_edit.text() == "360"
    d._close()


def test_close_mid_run_restores_once(qapp, monkeypatch):
    a, m, _ip, _ib = _face_mate("slider")
    before = _state(a)
    ctl, d = _dialog(a, m["id"], qapp)
    calls = []
    orig = a.end_animate
    monkeypatch.setattr(a, "end_animate", lambda: (calls.append(1), orig())[1])
    _set(d, 0, 40, 10, "single")
    d.play()
    d._tick()
    d._tick()
    assert _state(a) != before
    d.close()
    d.reject()
    assert _state(a) == before and calls == [1]
    assert not d.running and d._doc_notified not in a.listeners


def test_stop_restores_and_notifications_do_not_restart(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    before = _state(a)
    _c, d = _dialog(a, m["id"], qapp)
    _set(d, 0, 40, 10, "loop")
    d.play()
    for _ in range(14):                 # loop wraps
        d._tick()
    assert d.running
    d.toggle_play()
    assert not d.running and _state(a) == before
    a.notify()
    assert not d.running
    d._close()


def test_reciprocate_and_reverse(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    _c, d = _dialog(a, m["id"], qapp)
    _set(d, 0, 40, 3, "reciprocate")
    d.play()
    seen = []
    for _ in range(4):
        d._tick()
        seen.append(float(d.current.text()))
    assert seen == pytest.approx([0, 20, 40, 20], abs=1e-3)
    d.reverse()
    d._tick()
    assert float(d.current.text()) == pytest.approx(40.0, abs=1e-3)
    d.stop()
    d._close()


def test_bad_fields_report_and_do_not_play(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    before = _state(a)
    _c, d = _dialog(a, m["id"], qapp)
    d.steps_edit.setText("1")
    d.play()
    assert not d.running and d.msg.text()
    d.steps_edit.setText("5")
    d.end_edit.setText("abc")
    d.play()
    assert not d.running and "End" in d.msg.text() and _state(a) == before
    d._close()


def test_deleting_the_mate_closes_the_dialog(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    _c, d = _dialog(a, m["id"], qapp)
    _set(d, 0, 40, 5, "loop")
    d.play()
    d._tick()
    a.delete_mates([m["id"]])
    assert d._closed and not d.running


def test_mate_menu_has_animate(qapp, monkeypatch):
    a, m, _ip, _ib = _face_mate("slider")
    ctl = _ctl(a)
    ctl.insert_panel = None
    menu, _sub = _menu_actions(ctl, m["id"], monkeypatch)
    act = [x for x in menu.actions() if x.text() == "Animate…"]
    assert len(act) == 1
    act[0].trigger()
    assert ctl.animate_dialog is not None and ctl.w.mode == "dialog"
    ctl.animate_dialog.reject()
    assert ctl.animate_dialog is None and ctl.w.mode == "idle"
    b, fm, _p, _q = _face_mate("fastened")
    ctl = _ctl(b)
    menu, _sub = _menu_actions(ctl, fm["id"], monkeypatch)
    assert not [x for x in menu.actions() if x.text() == "Animate…"]
