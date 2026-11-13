"""Limits in MateDialog, the mate list marker and Apply limit position in the mate menu (I5.3)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from test_golden_mates import _face_mate  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Status:
    def __init__(self):
        self.msgs = []

    def showMessage(self, text, ms=0):
        self.msgs.append(text)


# Closing a mate dialog schedules its deletion (deleteLater), which runs the next time an event loop spins. The
# dialog is a child of the stand-in viewport made here; if that viewport has been garbage-collected by then, the
# deferred delete reaches a freed object and the process dies in a later test that waits on the event loop.
# In the app the viewport outlives every dialog, so the stand-ins are kept alive for the whole run.
_KEEP = []


def _ctl(a):
    """A controller with just enough around it to open the dialog, accept it and show the mate menu."""
    from freeshape.ui.assembly_ui import AssemblyController, AssemblyPanel
    from freeshape.ui.viewport import Viewport
    vp = Viewport()
    ctl = AssemblyController.__new__(AssemblyController)
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    w.viewport, w.doc, w.status, w.mode, w._adapt_tree = vp, a, _Status(), "idle", lambda d: None
    ctl.w = w
    ctl.mate_dialog = None
    ctl.panel = AssemblyPanel()
    ctl.update_triads = lambda: None
    ctl.show_scene = lambda: None
    ctl.on_changed = lambda: None
    _KEEP.append((vp, w, ctl))
    return ctl


def _edit(ctl, mid):
    from freeshape.ui.assembly_ui import MateDialog
    ctl.mate_dialog = MateDialog(ctl, ctl.doc.mate(mid), False, [])
    ctl.mate_dialog.accepted.connect(ctl._mate_accepted)
    ctl.mate_dialog.rejected.connect(ctl._mate_rejected)
    return ctl.mate_dialog


def _fill(dlg, dof, lo, hi):
    dlg.limits_check.setChecked(True)
    e_lo, e_hi = dlg.limit_edits[dof]
    e_lo.setText(lo)
    e_hi.setText(hi)


def test_accept_stores_slider_limits(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    ctl = _ctl(a)
    dlg = _edit(ctl, m["id"])
    assert dlg.limits_check.isVisibleTo(dlg) and not dlg.limits_check.isChecked()
    _fill(dlg, "tz", "0", "50")
    dlg.accept()
    assert a.mate(m["id"])["limits"] == {"tz": [0, 50]}


def test_cancel_leaves_limits_untouched(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    a.set_limits(m["id"], "tz", 0, 50)
    before = a.mate(m["id"])["limits"]
    ctl = _ctl(a)
    dlg = _edit(ctl, m["id"])
    assert dlg.limits_check.isChecked()
    _fill(dlg, "tz", "10", "20")
    dlg.reject()
    assert a.mate(m["id"])["limits"] == before == {"tz": [0, 50]}
    b, n, _ip, _ib = _face_mate("slider")
    dlg = _edit(_ctl(b), n["id"])
    _fill(dlg, "tz", "0", "5")
    dlg.reject()
    assert "limits" not in b.mate(n["id"])


def test_blank_end_is_no_limit_and_untick_clears(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    ctl = _ctl(a)
    dlg = _edit(ctl, m["id"])
    _fill(dlg, "tz", "", "50")
    dlg.accept()
    assert a.mate(m["id"])["limits"] == {"tz": [None, 50]}
    dlg = _edit(ctl, m["id"])
    dlg.limits_check.setChecked(False)
    dlg.accept()
    assert "limits" not in a.mate(m["id"])


def test_bad_text_and_min_above_max_block_accept(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    ctl = _ctl(a)
    dlg = _edit(ctl, m["id"])
    _fill(dlg, "tz", "abc", "5")
    dlg.accept()
    assert ctl.mate_dialog is dlg and "limits" not in a.mate(m["id"]) and dlg.err.text()
    _fill(dlg, "tz", "9", "5")
    dlg.accept()
    assert ctl.mate_dialog is dlg and "minimum" in dlg.err.text()
    dlg.reject()


def test_new_mate_limits_stored_after_add(qapp):
    a, m, ip, ib = _face_mate("slider")
    ctl = _ctl(a)
    a.delete_mates([m["id"]])
    new = a.make_mate("slider")
    new.update(a=m["a"], b=m["b"])
    from freeshape.ui.assembly_ui import MateDialog
    ctl.mate_dialog = MateDialog(ctl, new, True, [])
    ctl.mate_dialog.accepted.connect(ctl._mate_accepted)
    _fill(ctl.mate_dialog, "tz", "0", "50")
    ctl.mate_dialog.accept()
    assert a.mate(new["id"])["limits"] == {"tz": [0, 50]}


def test_ball_and_fastened_have_no_limits_tick(qapp):
    for t in ("ball", "fastened"):
        a, m, _ip, _ib = _face_mate(t)
        dlg = _edit(_ctl(a), m["id"])
        assert not dlg.limits_check.isVisibleTo(dlg) and dlg.limit_edits == {}
        dlg.reject()


def test_cylindrical_has_row_per_freedom_with_angle_units(qapp):
    a, m, _ip, _ib = _face_mate("cylindrical")
    dlg = _edit(_ctl(a), m["id"])
    dlg.limits_check.setChecked(True)
    assert set(dlg.limit_edits) == {"tz", "rz"}
    dlg.limit_edits["rz"][0].setText("-90")
    dlg.limit_edits["rz"][1].setText("90")
    assert dlg.limit_values() == {"tz": (None, None), "rz": (-90.0, 90.0)}
    dlg.reject()


def test_changing_type_drops_stale_limits(qapp):
    a, m, _ip, _ib = _face_mate("slider")
    a.set_limits(m["id"], "tz", 0, 50)
    ctl = _ctl(a)
    dlg = _edit(ctl, m["id"])
    dlg.set_type("ball", preview=False)
    assert not dlg.limits_check.isVisibleTo(dlg)
    dlg.accept()
    assert "limits" not in a.mate(m["id"])


def test_panel_marks_limited_mates(qapp):
    from freeshape.ui.assembly_ui import AssemblyPanel
    a, m, _ip, _ib = _face_mate("slider")
    p = AssemblyPanel()
    p.populate(a, None)
    plain = p.mates.item(0).text()
    assert "limits" not in p.mates.item(0).toolTip()
    a.set_limits(m["id"], "tz", 0, 50)
    p.populate(a, None)
    it = p.mates.item(0)
    assert it.text() != plain and it.text().startswith(plain)
    assert "limits: 0 to 50 mm" in it.toolTip()


def _menu_actions(ctl, mid, monkeypatch):
    import PySide6.QtWidgets as QW
    seen = {}

    class FakeMenu(QW.QMenu):
        def exec(self, *a, **k):
            seen["menu"] = self
    monkeypatch.setattr(QW, "QMenu", FakeMenu)
    ctl.panel.selected_mates = lambda: [mid]
    ctl.mate_menu(mid, None)
    menu = seen["menu"]
    sub = [x for x in menu.actions() if x.text() == "Apply limit position"]
    return menu, sub


def test_apply_limit_position_min_and_max(qapp, monkeypatch):
    a, m, _ip, _ib = _face_mate("slider")
    a.set_limits(m["id"], "tz", 0, 50)
    ctl = _ctl(a)
    _menu, sub = _menu_actions(ctl, m["id"], monkeypatch)
    acts = {x.text().split(" (")[0]: x for x in sub[0].menu().actions()}
    assert set(acts) == {"Slide Min", "Slide Max"}
    acts["Slide Max"].trigger()
    assert a.mate(m["id"])["values"]["tz"] == pytest.approx(50.0, abs=1e-6)
    acts["Slide Min"].trigger()
    assert a.mate(m["id"])["values"]["tz"] == pytest.approx(0.0, abs=1e-6)


def test_apply_limit_position_absent_without_limits(qapp, monkeypatch):
    a, m, _ip, _ib = _face_mate("slider")
    _menu, sub = _menu_actions(_ctl(a), m["id"], monkeypatch)
    assert sub == []


def test_apply_limit_position_one_sided(qapp, monkeypatch):
    a, m, _ip, _ib = _face_mate("slider")
    a.set_limits(m["id"], "tz", None, 50)
    _menu, sub = _menu_actions(_ctl(a), m["id"], monkeypatch)
    assert [x.text() for x in sub[0].menu().actions()] == ["Slide Max (50 mm)"]
