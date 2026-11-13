"""Animate (slice S6 gate): a mate plays over a range with everything else still enforced, and stopping puts the
assembly back. 0 to 720 degrees in 9 steps gives 0, 90 ... 720; reciprocate and loop give the specified sequences;
at every frame a gear relation's output reads half the input; end_animate restores transforms and values exactly;
the Animate dialog plays one run to completion offscreen.

    both fs_py scripts/smoke/sm6_animate.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mates_common as C  # noqa: E402
from freeshape import assembly as A  # noqa: E402

STEPS = 9
FULL = [0.0, 90.0, 180.0, 270.0, 360.0, 450.0, 540.0, 630.0, 720.0]


def plus_x(i):
    return i.get("n") is not None and i["n"][0] > 0.9


def gear_pair():
    """A fixed plate with two revolute blocks (top face and +X face) joined by a 0.5 gear relation from the first
    mate's rz to the second's. Both start at 0. Returns (asm, input mate, output mate)."""
    a = C.new_assembly()
    ip = a.insert(C.src("plate"))
    ib1, ib2 = C.add_body(a, "block"), C.add_body(a, "block")
    m1 = C.add_mate(a, "revolute", a.connector(ip, "face", C.face(a, ip, C.up)),
                    a.connector(ib1, "face", C.face(a, ib1, C.down)))
    m2 = C.add_mate(a, "revolute", a.connector(ip, "face", C.face(a, ip, plus_x)),
                    a.connector(ib2, "face", C.face(a, ib2, C.down)))
    for m in (m1, m2):
        assert a.drive_mate(m["id"], "rz", 0.0)[1]
    r = a.make_relation("gear")
    r.update(a={"mate": m1["id"], "dof": "rz"}, b={"mate": m2["id"], "dof": "rz"}, ratio=0.5)
    a.add_relation(r)
    assert not a.errors, a.errors
    return a, m1, m2


def state(a):
    return json.dumps([[i["id"], i["T"]] for i in a.instances]) + json.dumps([[m["id"], m.get("values")] for m in a.mates])


def check_values_single():
    assert A.animate_values(0, 720, STEPS, "single") == FULL, A.animate_values(0, 720, STEPS, "single")


def check_values_reciprocate_and_loop():
    got = A.animate_values(0, 720, STEPS, "reciprocate")
    assert got == FULL + FULL[-2::-1], got
    assert len(got) == 2 * STEPS - 1 and got[-1] == 0.0
    loop = A.animate_values(0, 720, STEPS, "loop")
    assert loop == FULL, loop                         # the caller repeats the sequence


def check_gear_half_at_every_frame():
    a, m1, m2 = gear_pair()
    a.begin_animate()
    for v in A.animate_values(0, 720, STEPS, "reciprocate"):
        reached = a.animate_frame(m1["id"], "rz", v)
        assert abs(reached - v) < 1e-6, (v, reached)
        out = a.mate(m2["id"])["values"]["rz"]
        assert abs(out - 0.5 * reached) < 1e-6, (v, reached, out)
    a.end_animate()


def check_stop_restores_exactly():
    a, m1, m2 = gear_pair()
    before = state(a)
    undo_depth = len(a._undo) if hasattr(a, "_undo") else None
    a.begin_animate()
    for v in A.animate_values(0, 720, STEPS, "single"):
        a.animate_frame(m1["id"], "rz", v)
    assert state(a) != before, "the run did not move anything"
    a.end_animate()
    assert state(a) == before, "end_animate did not restore transforms and values"
    if undo_depth is not None:
        assert len(a._undo) == undo_depth, "animate touched the undo stack"


def check_dialog_plays_one_run():
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.assembly_ui import AnimateDialog
    from test_mate_limits_ui import _ctl
    QApplication.instance() or QApplication([])
    a, m1, m2 = gear_pair()
    before = state(a)
    d = AnimateDialog(_ctl(a), m1["id"])
    d.start_edit.setText("0")
    d.end_edit.setText("720")
    d.steps_edit.setText(str(STEPS))
    d.playback.setCurrentIndex(d.playback.findData("single"))
    d.play()
    assert d.running
    frames = 0
    for _ in range(STEPS):
        d._tick()
        if d.running:                                 # the final tick ends the run and restores the assembly
            frames += 1
            out, cur = m2["values"]["rz"], float(d.current.text())
            assert abs(out - 0.5 * cur) < 1e-3, (out, cur)
    assert frames >= 1, frames
    assert not d.running, "the single run did not finish"
    assert abs(float(d.current.text()) - 720.0) < 1e-3, d.current.text()
    assert state(a) == before, "the dialog run did not restore the assembly"
    d._close()


def main():
    check_values_single()
    check_values_reciprocate_and_loop()
    check_gear_half_at_every_frame()
    check_stop_restores_exactly()
    check_dialog_plays_one_run()
    print(f"SM6 OK: 0..720 in 9 steps, reciprocate and loop sequences, gear output half the input per frame, "
          f"stop restores exactly, dialog single run ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
