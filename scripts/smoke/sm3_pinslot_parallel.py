"""Pin slot and parallel mates (slice S3 gate): a pin slot leaves 2 DOF and its slide drives to 15 mm, a parallel
mate leaves 4 DOF, a gear relation from a parallel mate's rz to a revolute mate holds at ratio 0.5 over 720
degrees, and a linear relation from a pin slot's tx to a slider holds.

    both fs_py scripts/smoke/sm3_pinslot_parallel.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import mates_common as C  # noqa: E402


def plus_x(i):
    return i.get("n") is not None and i["n"][0] > 0.9


def second_mate(a, ip, mtype):
    """A new block on a mate of `mtype` against the plate's +X side face. Returns the mate."""
    ib = C.add_body(a, "block")
    ca = a.connector(ip, "face", C.face(a, ip, plus_x))
    cb = a.connector(ib, "face", C.face(a, ib, C.down))
    return C.add_mate(a, mtype, ca, cb)


def relate(a, rtype, m1, d1, m2, d2, ratio):
    r = a.make_relation(rtype)
    r.update(a={"mate": m1["id"], "dof": d1}, b={"mate": m2["id"], "dof": d2}, ratio=ratio)
    a.add_relation(r)
    return r


def zero(a, m, dof):
    assert a.drive_mate(m["id"], dof, 0.0)[1], (m["type"], dof)


def check_pin_slot_two_dof_and_slide():
    a = C.new_assembly()
    ip, ib = a.insert(C.src("plate")), a.insert(C.src("block"))
    m = C.face_mate(a, "pin_slot", ip, ib)
    assert not a.errors, a.errors
    assert a.dof == 2, a.dof
    reached, ok = a.drive_mate(m["id"], "tx", 15.0)
    assert ok and abs(reached - 15.0) < 1e-6 and abs(m["values"]["tx"] - 15.0) < 1e-6, (reached, ok, m["values"])
    a.solve()
    assert a.dof == 2 and abs(m["values"]["tx"] - 15.0) < 1e-6, (a.dof, m["values"])


def check_parallel_four_dof():
    a = C.new_assembly()
    ip, ib = a.insert(C.src("plate")), a.insert(C.src("block"))
    C.face_mate(a, "parallel", ip, ib)
    assert not a.errors, a.errors
    assert a.dof == 4, a.dof


def check_gear_on_parallel_rz():
    a = C.new_assembly()
    ip = a.insert(C.src("plate"))
    m1 = C.face_mate(a, "parallel", ip, C.add_body(a, "block"))
    m2 = second_mate(a, ip, "revolute")
    zero(a, m1, "rz")
    zero(a, m2, "rz")
    relate(a, "gear", m1, "rz", m2, "rz", 0.5)
    for target, want in ((720.0, 360.0), (180.0, 90.0)):
        assert a.drive_mate(m1["id"], "rz", target)[1], target
        got = m2["values"]["rz"]
        assert abs(got - want) < 1e-6, (target, got)
        assert abs(m1["values"]["rz"] - target) < 1e-6, m1["values"]


def check_linear_on_pin_slot_tx():
    a = C.new_assembly()
    ip = a.insert(C.src("plate"))
    m1 = C.face_mate(a, "pin_slot", ip, C.add_body(a, "block"))
    m2 = second_mate(a, ip, "slider")
    zero(a, m1, "tx")
    zero(a, m2, "tz")
    relate(a, "linear", m1, "tx", m2, "tz", 2.0)
    assert a.drive_mate(m1["id"], "tx", 10.0)[1]
    assert abs(m2["values"]["tz"] - 20.0) < 1e-6, m2["values"]
    assert a.drive_mate(m2["id"], "tz", 6.0)[1]
    assert abs(m1["values"]["tx"] - 3.0) < 1e-6, m1["values"]


def main():
    check_pin_slot_two_dof_and_slide()
    check_parallel_four_dof()
    check_gear_on_parallel_rz()
    check_linear_on_pin_slot_tx()
    print(f"SM3 OK: pin slot 2 DOF and tx 15, parallel 4 DOF, gear 0.5 over 720, linear on pin slot ocp={os.environ.get('OCP', '?')}")


if __name__ == "__main__":
    main()
