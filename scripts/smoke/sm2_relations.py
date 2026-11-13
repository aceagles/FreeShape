"""Relations (slice S2 gate): gear, rack and pinion, screw and linear relations hold their ratio over many turns,
in both directions, and adding one moves nothing. A contradictory relation is the one reported, a suppressed mate
leaves its relation out, relations survive save/load and delete + undo, a drag of the output moves the input,
and the relation dialog adds one.

    both fs_py scripts/smoke/sm2_relations.py
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


def near(x, want, tol=1e-4):
    return abs(x - want) < tol


def poses(a):
    return {i["id"]: np.array(i["T"], float) for i in a.instances}


def same_poses(p, q):
    return p.keys() == q.keys() and all(np.allclose(p[k], q[k], atol=1e-9) for k in p)


def check_gear_train():
    a, (m1, m2, m3) = C.mated_blocks("revolute", "revolute", "revolute")
    C.relate(a, "gear", m1, m2, 0.5)
    C.relate(a, "gear", m2, m3, 0.5)
    reached, ok = a.drive_mate(m1["id"], "rz", 720.0)
    assert ok and near(reached, 720.0), (reached, ok)
    v = [m["values"]["rz"] for m in (m1, m2, m3)]
    assert near(v[0], 720.0) and near(v[1], 360.0) and near(v[2], 180.0), v
    assert not a.errors, a.errors
    reached, ok = a.drive_mate(m1["id"], "rz", -720.0)       # and back the other way
    assert ok and near(m2["values"]["rz"], -360.0) and near(m3["values"]["rz"], -180.0), (m2["values"], m3["values"])


def check_rack_and_pinion():
    a, (pinion, rack) = C.mated_blocks("revolute", "slider")
    C.relate(a, "rack_pinion", pinion, rack, 40.0, "rz", "tz")
    assert a.drive_mate(pinion["id"], "rz", 360.0)[1]
    assert near(rack["values"]["tz"], 40.0), rack["values"]
    assert a.drive_mate(pinion["id"], "rz", -180.0)[1]
    assert near(rack["values"]["tz"], -20.0), rack["values"]


def check_screw():
    a, m, _ip, _ib = C.cylindrical()
    for dof in ("tz", "rz"):                                   # the phase is captured, so start from zero
        assert a.drive_mate(m["id"], dof, 0.0)[1]
    C.relate(a, "screw", m, m, 2.0, "rz", "tz")
    assert a.drive_mate(m["id"], "rz", 1080.0)[1]
    assert near(m["values"]["tz"], 6.0), m["values"]
    assert near(m["values"]["rz"], 1080.0), m["values"]


def check_linear_and_reverse():
    for reverse, want in ((False, 20.0), (True, -20.0)):
        a, (m1, m2) = C.mated_blocks("slider", "slider")
        C.relate(a, "linear", m1, m2, 2.0, "tz", "tz", reverse=reverse)
        assert a.drive_mate(m1["id"], "tz", 10.0)[1]
        assert near(m2["values"]["tz"], want), (reverse, m2["values"])


def check_adding_moves_nothing():
    a, (m1, m2) = C.mated_blocks("revolute", "revolute")
    assert a.drive_mate(m2["id"], "rz", 7.5)[1]
    before = poses(a)
    r = C.relate(a, "gear", m1, m2, 0.5)
    assert same_poses(before, poses(a)), "adding a relation moved an instance"
    assert near(r["phase"], 7.5), r["phase"]
    assert a.drive_mate(m1["id"], "rz", 100.0)[1]
    assert near(m2["values"]["rz"], 57.5), m2["values"]       # 7.5 + 0.5 * 100
    assert a.drive_mate(m1["id"], "rz", 0.0)[1]
    assert near(m2["values"]["rz"], 7.5), m2["values"]


def check_contradiction_reported():
    a, (m1, m2, m3) = C.mated_blocks("revolute", "revolute", "revolute")
    r12 = C.relate(a, "gear", m1, m2, 0.5)
    r23 = C.relate(a, "gear", m2, m3, 0.5)
    r13 = C.relate(a, "gear", m1, m3, 0.5)
    assert a.solve() is True and not a.errors, a.errors       # consistent at rest
    assert a.drive_mate(m1["id"], "rz", 90.0)[1]
    assert r13["id"] in a.errors and a.errors[r13["id"]].startswith("Can't be solved"), a.errors
    assert r12["id"] not in a.errors and r23["id"] not in a.errors, a.errors
    assert not any(m["id"] in a.errors for m in (m1, m2, m3)), a.errors


def check_suppressed_mate():
    a, (m1, m2) = C.mated_blocks("revolute", "revolute")
    r = C.relate(a, "gear", m1, m2, 0.5)
    a.set_suppressed(m2["id"], True)
    assert a.solve() is True
    assert r["id"] in a.errors and a.errors[r["id"]].startswith("Left out:") and m2["name"] in a.errors[r["id"]], a.errors
    a.set_suppressed(m2["id"], False)
    assert r["id"] not in a.errors, a.errors


def check_save_load_and_delete_undo():
    a, (m1, m2) = C.mated_blocks("revolute", "revolute")
    assert a.drive_mate(m2["id"], "rz", 7.5)[1]
    r = C.relate(a, "gear", m1, m2, 0.5)
    back = C.save_load(a)
    assert not back.errors, back.errors
    assert back.relations == a.relations and near(back.relations[0]["phase"], 7.5), back.relations
    a.delete_relation(r["id"])
    assert a.relations == []
    a.undo()
    assert [x["id"] for x in a.relations] == [r["id"]] and near(a.relations[0]["phase"], 7.5), a.relations
    assert a.drive_mate(m1["id"], "rz", 20.0)[1]           # undo rebuilt the mates: look them up again
    assert near(a.mate(m2["id"])["values"]["rz"], 17.5), a.mate(m2["id"])["values"]


def check_drag_output_moves_input():
    a, (m1, m2) = C.mated_blocks("slider", "slider")
    C.relate(a, "linear", m1, m2, 2.0, "tz", "tz")
    ib1, ib2 = m1["b"]["inst"], m2["b"]["inst"]
    T1 = poses(a)[ib1]
    lp = np.array([10.0, 5.0, 4.0])
    T2 = poses(a)[ib2]
    target = T2[:3, :3] @ lp + T2[:3, 3] + 6.0 * a.world_frame(m2["a"])["z"]
    a.solve(drag={"inst": ib2, "points": [(lp, target)]})
    assert near(m2["values"]["tz"], 6.0, 1e-3) and near(m1["values"]["tz"], 3.0, 1e-3), (m1["values"], m2["values"])
    assert not np.allclose(poses(a)[ib1], T1, atol=1e-3), "input did not move"


def check_dialog():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.assembly_ui import AssemblyController, AssemblyPanel
    from freeshape.ui.viewport import Viewport
    a, (m1, m2) = C.mated_blocks("revolute", "revolute")
    ctl = AssemblyController.__new__(AssemblyController)
    status = SimpleNamespace(showMessage=lambda *args: None)
    ctl.w = SimpleNamespace(viewport=Viewport(), doc=a, status=status, mode="idle", _adapt_tree=lambda d: None)
    ctl.mate_dialog = ctl.relation_dialog = ctl.insert_panel = None
    ctl.panel = AssemblyPanel()
    ctl.update_triads = lambda: None
    ctl.show_scene = lambda: None
    ctl.start_relation("gear")
    d = ctl.relation_dialog
    d.pick_mate(m1["id"])
    d.pick_mate(m2["id"])
    d.ratio.setText("0.5")
    d.accept()
    assert len(a.relations) == 1, a.relations
    r = a.relations[0]
    assert r["type"] == "gear" and r["ratio"] == 0.5 and r["a"]["mate"] == m1["id"] and r["b"]["mate"] == m2["id"], r
    del app


def main():
    check_gear_train()
    check_rack_and_pinion()
    check_screw()
    check_linear_and_reverse()
    check_adding_moves_nothing()
    check_contradiction_reported()
    check_suppressed_mate()
    check_save_load_and_delete_undo()
    check_drag_output_moves_input()
    check_dialog()
    print(f"SM2 OK: gear train 720/360/180, rack and pinion, screw, linear and reverse, add moves nothing, contradiction, suppressed mate, save/load, delete+undo, drag, dialog ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
