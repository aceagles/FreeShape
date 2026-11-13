"""Mate values in MateDialog, the mate list tooltip and the Set value controller method (I1.4)."""
import os
import sys
from types import SimpleNamespace

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


def _ctl(a):
    from freeshape.ui.assembly_ui import AssemblyController
    from freeshape.ui.viewport import Viewport
    ctl = AssemblyController.__new__(AssemblyController)
    ctl.w = SimpleNamespace(viewport=Viewport(), doc=a, status=_Status(), mode="idle")
    return ctl


def _dialog(a, m, qapp):
    from freeshape.ui.assembly_ui import MateDialog
    from freeshape.ui.viewport import Viewport
    ctl = SimpleNamespace(w=SimpleNamespace(viewport=Viewport()), doc=a, update_triads=lambda: None)
    return MateDialog(ctl, m, False, [])


def _texts(dlg):
    return {d: l.text() for d, l in dlg.value_labels.items()}


def test_dialog_value_row_shows_450(qapp):
    a, m, _ip, _ib = _face_mate("revolute")
    a.drive_mate(m["id"], "rz", 450.0)
    dlg = _dialog(a, m, qapp)
    assert "450" in _texts(dlg)["rz"]
    assert dlg.values_box.isVisibleTo(dlg)


def test_dialog_value_row_follows_notify(qapp):
    a, m, _ip, _ib = _face_mate("revolute")
    dlg = _dialog(a, m, qapp)
    a.drive_mate(m["id"], "rz", 90.0)          # notifies, as a drag does
    assert "90" in _texts(dlg)["rz"]
    dlg.reject()
    assert not a.listeners


def test_dialog_rows_per_freedom_and_none_for_fastened(qapp):
    a, m, _ip, _ib = _face_mate("cylindrical")
    assert set(_dialog(a, m, qapp).value_labels) == {"tz", "rz"}
    b, f, _ip, _ib = _face_mate("fastened")
    dlg = _dialog(b, f, qapp)
    assert dlg.value_labels == {} and not dlg.values_box.isVisibleTo(dlg)


def test_tooltip_lists_values(qapp):
    from freeshape.ui.assembly_ui import AssemblyPanel
    a, m, _ip, _ib = _face_mate("revolute")
    a.drive_mate(m["id"], "rz", 450.0)
    p = AssemblyPanel()
    p.populate(a, None)
    tip = p.mates.item(0).toolTip()
    assert "450" in tip and "°" in tip


def test_set_mate_value_drives(qapp):
    a, m, _ip, _ib = _face_mate("revolute")
    ctl = _ctl(a)
    assert ctl.set_mate_value(m["id"], "rz", 90) == (pytest.approx(90.0, abs=1e-6), True)
    assert m["values"]["rz"] == pytest.approx(90.0, abs=1e-6)
    assert not ctl.w.status.msgs


def test_set_mate_value_reports_failure(qapp):
    a, m, _ip, _ib = _face_mate("revolute")
    ctl = _ctl(a)
    assert ctl.set_mate_value(m["id"], "tz", 5) == (None, False)    # a revolute has no tz
    assert ctl.w.status.msgs
