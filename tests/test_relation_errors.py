"""Relations in the failure paths: conflict isolation, left-out errors, DOF counting, phase recapture (I2.3)."""
import pytest

from freeshape import assembly as A
from test_golden_mates import _asm, _down, _face, _src, _up


def _plus_x(i):
    return i.get("n") is not None and i["n"][0] > 0.9


def _gears(n=3):
    """A fixed plate with n blocks, each on its own revolute mate, all at zero angle."""
    a = _asm()
    ip = a.insert(_src("plate"))
    mates = []
    for k in range(n):
        ib = a.insert(_src("block"))
        m = a.make_mate("revolute")
        m.update(a=a.connector(ip, "face", _face(a, ip, _up if k % 2 == 0 else _plus_x)),
                 b=a.connector(ib, "face", _face(a, ib, _down)))
        a.add_mate(m)
        assert a.drive_mate(m["id"], "rz", 0.0)[1]
        mates.append(m)
    return a, mates


def _gear(a, m1, m2, ratio=0.5):
    r = a.make_relation("gear")
    r.update(a={"mate": m1["id"], "dof": "rz"}, b={"mate": m2["id"], "dof": "rz"}, ratio=ratio)
    a.add_relation(r)
    return r


def _poses(a):
    return {i["id"]: [list(row) for row in i["T"]] for i in a.instances}


def test_contradictory_third_relation_is_the_one_in_error():
    a, (m1, m2, m3) = _gears(3)
    r12, r23 = _gear(a, m1, m2), _gear(a, m2, m3)
    r13 = _gear(a, m1, m3)
    assert a.solve() is True            # all three agree at the zero pose: nothing contradicts yet
    reached, ok = a.drive_mate(m1["id"], "rz", 90.0)      # driving A makes A-C (0.5) disagree with A-B-C (0.25)
    assert ok and reached == pytest.approx(90.0, abs=1e-6)
    assert r13["id"] in a.errors and a.errors[r13["id"]].startswith("Can't be solved")
    assert r13["id"] in a._conflicts
    assert r12["id"] not in a.errors and r23["id"] not in a.errors
    assert not any(m["id"] in a.errors for m in (m1, m2, m3))
    assert m2["values"]["rz"] == pytest.approx(45.0, abs=1e-6)
    assert m3["values"]["rz"] == pytest.approx(22.5, abs=1e-6)
    assert r13["id"] in a.errors and r12["id"] not in a.errors


def test_suppressing_a_related_mate_flags_the_relation():
    a, (m1, m2) = _gears(2)
    r = _gear(a, m1, m2)
    a.set_suppressed(m2["id"], True)
    assert a.solve() is True
    assert r["id"] in a.errors and m2["name"] in a.errors[r["id"]]
    a.set_suppressed(m2["id"], False)
    assert r["id"] not in a.errors
    assert a.solve() is True and r["id"] not in a.errors


def test_deleted_mate_names_it_in_the_relation_error():
    a, (m1, m2) = _gears(2)
    r = _gear(a, m1, m2)
    a.delete_mates([m2["id"]])
    a.solve()
    assert m2["id"] in a.errors[r["id"]]


def test_dof_counts_relation_residuals():
    a, (m1, m2) = _gears(2)
    a.solve()
    assert a.dof == 2
    assert a.instance_dof(m1["b"]["inst"]) + a.instance_dof(m2["b"]["inst"]) == 2
    _gear(a, m1, m2)
    a.solve()
    assert a.dof == 1
    assert a.instance_dof(m1["b"]["inst"]) == 0 and a.instance_dof(m2["b"]["inst"]) == 0


def test_conflicting_relation_does_not_count_for_dof():
    a, (m1, m2, m3) = _gears(3)
    _gear(a, m1, m2), _gear(a, m2, m3), _gear(a, m1, m3)
    assert a.drive_mate(m1["id"], "rz", 90.0)[1]
    a._update_status()                  # the status inside the drive still held A at its step value
    assert a.dof == 1


def test_replace_mate_recaptures_phase_and_moves_nothing_else():
    a, (m1, m2) = _gears(2)
    r = _gear(a, m1, m2)
    assert a.drive_mate(m1["id"], "rz", 30.0)[1]
    other = m2["b"]["inst"]
    before = _poses(a)[other]
    new = dict(m1, angle=40.0)
    a.replace_mate(new)
    assert _poses(a)[other] == [pytest.approx(row, abs=1e-9) for row in before]
    assert r["id"] not in a.errors
    m1n = a.mate(m1["id"])
    m2n = a.mate(m2["id"])
    assert m2n["values"]["rz"] - 0.5 * m1n["values"]["rz"] == pytest.approx(r["phase"], abs=1e-6)
    reached, ok = a.drive_mate(m1["id"], "rz", m1n["values"]["rz"] + 60.0)
    assert ok
    assert a.mate(m2["id"])["values"]["rz"] - 0.5 * a.mate(m1["id"])["values"]["rz"] == pytest.approx(r["phase"], abs=1e-6)
