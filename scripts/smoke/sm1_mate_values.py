"""Mate values and drive to value (slice S1 gate): a mate knows how far it has travelled, past a full turn, the
number survives save and reopen, files from before Mate values load with values measured from their pose,
and a script can turn a mate to a value.

    both fs_py scripts/smoke/sm1_mate_values.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

import mates_common as C  # noqa: E402
from freeshape import assembly as A  # noqa: E402


def check_revolute_450():
    a, m, _ip, ia = C.hinge()
    reached, ok = a.drive_mate(m["id"], "rz", 450.0)
    assert ok, "drive to 450 did not hold"
    assert abs(m["values"]["rz"] - 450.0) < 1e-6, m["values"]
    assert abs(reached - 450.0) < 1e-6, reached
    tip450 = C.point_world(a, ia, C.ARM_TIP)
    b, mb, _ip, ib = C.hinge()
    b.drive_mate(mb["id"], "rz", 90.0)
    assert abs(mb["values"]["rz"] - 90.0) < 1e-6, mb["values"]
    tip90 = C.point_world(b, ib, C.ARM_TIP)
    assert np.allclose(tip450, tip90, atol=1e-5), (tip450, tip90)
    return a, m


def check_save_load(a, m):
    back = C.save_load(a)
    assert not back.errors, back.errors
    v = back.mates[0]["values"]["rz"]
    assert abs(v - 450.0) < 1e-6, v
    assert back.mates[0]["id"] == m["id"]


def check_previous_version():
    a, m, _ip, _ia = C.hinge()
    a.drive_mate(m["id"], "rz", 450.0)
    path = C.write_previous_version(a)
    import json
    with open(path) as fh:
        d = json.load(fh)
    assert d["version"] < A.M.latest_version("assembly") and all("values" not in x for x in d["mates"])
    old = A.AssemblyDoc.load(path, resolver=C.resolver)
    assert not old.errors, old.errors
    vals = old.mates[0]["values"]
    # No turn count survives in a pose: the value is measured wrapped, 450 -> 90.
    assert abs(vals["rz"] - 90.0) < 1e-6, vals
    s, ms, _ip, _ib = C.slider()
    s.drive_mate(ms["id"], A.MATE_VALUE_DOFS["slider"][0], 25.0)
    old_s = A.AssemblyDoc.load(C.write_previous_version(s), resolver=C.resolver)
    assert abs(old_s.mates[0]["values"][A.MATE_VALUE_DOFS["slider"][0]] - 25.0) < 1e-6, old_s.mates[0]["values"]


def check_slider_25():
    a, m, _ip, _ib = C.slider()
    dof = A.MATE_VALUE_DOFS["slider"][0]
    reached, ok = a.drive_mate(m["id"], dof, 25.0)
    assert ok and abs(reached - 25.0) < 1e-6 and abs(m["values"][dof] - 25.0) < 1e-6, (reached, ok, m["values"])


def check_dialog():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.assembly_ui import MateDialog
    from freeshape.ui.viewport import Viewport
    a, m, _ip, _ia = C.hinge()
    a.drive_mate(m["id"], "rz", 450.0)
    ctl = SimpleNamespace(w=SimpleNamespace(viewport=Viewport()), doc=a, update_triads=lambda: None)
    dlg = MateDialog(ctl, m, False, [])
    text = dlg.value_labels["rz"].text()
    assert "450" in text, text
    dlg.reject()
    del app


def main():
    a, m = check_revolute_450()
    check_save_load(a, m)
    check_previous_version()
    check_slider_25()
    check_dialog()
    print(f"SM1 OK: revolute 450, save/load, previous-version load, slider 25, dialog ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
