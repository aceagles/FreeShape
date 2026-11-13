"""Mate limits (slice S5 gate): a limit is always true. A part past a limit moves to it when the limit is set,
a drive or a drag stops at it, a limit of 0 to 720 degrees holds at 720, a limit a fastened mate makes impossible
is a conflict on the limited mate, a limit on a gear's output stops its input, "Apply limit position" reaches each
end, and limits set in the mate dialog are stored.

    both fs_py scripts/smoke/sm5_limits.py
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


def measured(a, m):
    return A.mate_values(m, a.world_frame(m["a"]), a.world_frame(m["b"]))


def slider_at(tz):
    """The pin on a slider mate in the plate's hole, pushed to `tz` mm along the axis."""
    a, m, ip, ipin = C.pin_in_hole("slider")
    FA = a.world_frame(m["a"])
    a.move_instance(ipin, A.translation(np.asarray(FA["z"], float) * (tz - m["values"]["tz"])), checkpoint=False)
    assert abs(m["values"]["tz"] - tz) < 1e-6, m["values"]
    return a, m, ip, ipin


def check_slider_lands_on_limit():
    a, m, _ip, _ipin = slider_at(80.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    assert abs(m["values"]["tz"] - 50.0) < 1e-6 and abs(measured(a, m)["tz"] - 50.0) < 1e-6, m["values"]
    assert not a.errors and not a._conflicts
    b, n, _ip, ipin = slider_at(30.0)
    before = np.array(b.instance(ipin)["T"], float)
    b.set_limits(n["id"], "tz", 0.0, 50.0)
    assert np.allclose(np.array(b.instance(ipin)["T"], float), before, atol=1e-9), "a part inside the range moved"
    assert abs(n["values"]["tz"] - 30.0) < 1e-6, n["values"]


def check_drive_past_limit():
    a, m, _ip, _ipin = slider_at(0.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    reached, ok = a.drive_mate(m["id"], "tz", 80.0)
    assert ok is False and abs(reached - 50.0) < 1e-5, (reached, ok)
    assert abs(measured(a, m)["tz"] - 50.0) < 1e-5 and not a._conflicts
    reached, ok = a.drive_mate(m["id"], "tz", 50.0)
    assert ok and abs(reached - 50.0) < 1e-5, (reached, ok)


def check_drag_past_limit():
    a, m, _ip, ipin = slider_at(10.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    z = np.asarray(a.world_frame(m["a"])["z"], float)
    u = np.cross(z, [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    P = np.array([z, u])
    start = np.array(a.instance(ipin)["T"], float)[:3, 3]
    for k in range(1, 7):
        a.solve(drag={"inst": ipin, "local": np.zeros(3), "target": start + z * 70.0 * k / 6, "P": P}, iters=8)
    assert abs(m["values"]["tz"] - 50.0) < 1e-4, m["values"]
    assert abs(measured(a, m)["tz"] - 50.0) < 1e-4
    assert not a.errors and not a._conflicts


def check_720_limit():
    a, m, _ip, ia = C.hinge()
    a.set_limits(m["id"], "rz", 0.0, 720.0)
    reached, ok = a.drive_mate(m["id"], "rz", 900.0)
    assert ok is False and abs(reached - 720.0) < 1e-4, (reached, ok)
    assert abs(m["values"]["rz"] - 720.0) < 1e-4, m["values"]
    reached, ok = a.drive_mate(m["id"], "rz", 720.0)
    assert ok and abs(m["values"]["rz"] - 720.0) < 1e-4, (reached, ok, m["values"])
    a.solve()
    assert abs(m["values"]["rz"] - 720.0) < 1e-4, m["values"]
    assert not a.errors and not a._conflicts


def check_impossible_limit_is_conflict():
    a = C.new_assembly()
    ip, ipin = a.insert(C.src("plate")), a.insert(C.src("pin"))

    def hole_and_pin():
        return (a.connector(ip, "edge", C.edge(a, ip, C.hole_edge(0))),
                a.connector(ipin, "edge", C.edge(a, ipin, C.hole_edge(0))))
    ca, cb = hole_and_pin()
    slider = C.add_mate(a, "slider", ca, cb, flip=True)
    FA = a.world_frame(slider["a"])
    a.move_instance(ipin, A.translation(np.asarray(FA["z"], float) * (80.0 - slider["values"]["tz"])), checkpoint=False)
    ca, cb = hole_and_pin()
    fast = a.make_mate("fastened")
    fast.update(a=ca, b=cb, flip=True, offset=-80.0)
    a.mates.insert(0, fast)                      # the fastened mate holds the pin at 80 mm
    a.solve()
    assert not a.errors, a.errors
    a.set_limits(slider["id"], "tz", 0.0, 50.0)
    assert slider["id"] in a.errors and a.errors[slider["id"]].startswith("Limit can't be met"), a.errors
    assert slider["id"] in a._conflicts and fast["id"] not in a._conflicts, a._conflicts
    assert fast["id"] not in a.errors, a.errors
    a.set_limits(slider["id"], "tz", None, None)
    assert not a.errors, a.errors


def check_gear_output_limit_stops_input():
    a = C.new_assembly()
    ip = a.insert(C.src("plate"))

    def revolute(face_pick):
        ib = C.add_body(a, "block")
        return C.add_mate(a, "revolute", a.connector(ip, "face", C.face(a, ip, face_pick)),
                          a.connector(ib, "face", C.face(a, ib, C.down)))
    m1 = revolute(C.up)
    m2 = revolute(lambda i: i.get("n") is not None and i["n"][0] > 0.9)
    for m in (m1, m2):
        assert a.drive_mate(m["id"], "rz", 0.0)[1]
    r = a.make_relation("gear")
    r.update(a={"mate": m1["id"], "dof": "rz"}, b={"mate": m2["id"], "dof": "rz"}, ratio=0.5)
    a.add_relation(r)
    a.set_limits(m2["id"], "rz", -90.0, 90.0)
    for target, want in ((400.0, 180.0), (-400.0, -180.0)):
        reached, ok = a.drive_mate(m1["id"], "rz", target)
        assert ok is False and abs(reached - want) < 1e-4, (target, reached, ok)
        assert abs(m2["values"]["rz"] - want / 2) < 1e-4, m2["values"]
    assert not a._conflicts and not a.errors


def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Status:
    def __init__(self):
        self.msgs = []

    def showMessage(self, text, ms=0):
        self.msgs.append(text)


def controller(a):
    """A controller with just enough around it to open the mate dialog, accept it and show the mate menu."""
    from PySide6.QtWidgets import QWidget
    from freeshape.ui.assembly_ui import AssemblyController, AssemblyPanel
    from freeshape.ui.viewport import Viewport
    ctl = AssemblyController.__new__(AssemblyController)
    w = QWidget()
    w.viewport, w.doc, w.status, w.mode, w._adapt_tree = Viewport(), a, Status(), "idle", lambda d: None
    ctl.w = w
    ctl.mate_dialog = None
    ctl.panel = AssemblyPanel()
    ctl.update_triads = lambda: None
    ctl.show_scene = lambda: None
    ctl.on_changed = lambda: None
    return ctl


def check_apply_limit_position():
    app = qapp()
    import PySide6.QtWidgets as QW
    a, m, _ip, _ipin = C.slider()
    a.set_limits(m["id"], "tz", 0, 50)
    ctl = controller(a)
    seen = {}

    class FakeMenu(QW.QMenu):
        def exec(self, *x, **k):
            seen["menu"] = self
    real = QW.QMenu
    QW.QMenu = FakeMenu
    try:
        ctl.panel.selected_mates = lambda: [m["id"]]
        ctl.mate_menu(m["id"], None)
    finally:
        QW.QMenu = real
    sub = [x for x in seen["menu"].actions() if x.text() == "Apply limit position"]
    assert sub, "no Apply limit position submenu"
    acts = {x.text().split(" (")[0]: x for x in sub[0].menu().actions()}
    assert set(acts) == {"Slide Min", "Slide Max"}, set(acts)
    for name, want in (("Slide Max", 50.0), ("Slide Min", 0.0), ("Slide Max", 50.0)):
        acts[name].trigger()
        assert abs(a.mate(m["id"])["values"]["tz"] - want) < 1e-6, (name, m["values"])
    del app


def check_dialog_limits_stored():
    app = qapp()
    from freeshape.ui.assembly_ui import MateDialog
    a, m, _ip, _ipin = C.slider()
    ctl = controller(a)
    dlg = MateDialog(ctl, a.mate(m["id"]), False, [])
    ctl.mate_dialog = dlg
    dlg.accepted.connect(ctl._mate_accepted)
    dlg.rejected.connect(ctl._mate_rejected)
    dlg.limits_check.setChecked(True)
    lo, hi = dlg.limit_edits["tz"]
    lo.setText("0")
    hi.setText("50")
    dlg.accept()
    assert a.mate(m["id"])["limits"] == {"tz": [0, 50]}, a.mate(m["id"]).get("limits")
    del app


def main():
    check_slider_lands_on_limit()
    check_drive_past_limit()
    check_drag_past_limit()
    check_720_limit()
    check_impossible_limit_is_conflict()
    check_gear_output_limit_stops_input()
    check_apply_limit_position()
    check_dialog_limits_stored()
    print(f"SM5 OK: slider 80 lands on 50, drive and drag stop at limit, 0 to 720 holds, impossible limit conflict, gear output +-90 stops input at +-180, apply limit position, dialog limits stored ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
