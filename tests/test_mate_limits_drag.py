"""Mate limits in the drag solve, in drive_mate, as conflicts and through relations (I5.2)."""
import numpy as np
import pytest

from freeshape import assembly as A
from test_golden_mates import _asm, _pin_in_hole, _residual, _src, _face, _up, _down
from test_mate_limits import _measured, _slide, _slider_at
from test_relation_types import _mate, _plus_x, _rel


def _drag_along_axis(a, m, ipin, dist, steps=6):
    z = np.asarray(a.world_frame(m["a"])["z"], float)
    o = np.asarray(a.world_frame(m["a"])["o"], float)
    # a screen plane that contains the slider axis
    u = np.cross(z, [1.0, 0.0, 0.0] if abs(z[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    P = np.array([z, u])
    local = np.zeros(3)
    start = np.array(next(i for i in a.instances if i["id"] == ipin)["T"], float)[:3, 3]
    for k in range(1, steps + 1):
        a.solve(drag={"inst": ipin, "local": local, "target": start + z * dist * k / steps, "P": P}, iters=8)


def test_drag_stops_at_the_limit_with_mates_holding():
    a, m, _ip, ipin = _slider_at(10.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    _drag_along_axis(a, m, ipin, 70.0)
    assert m["values"]["tz"] == pytest.approx(50.0, abs=1e-4)
    assert _measured(a, m)["tz"] == pytest.approx(50.0, abs=1e-4)
    assert _residual(a, m) < 1e-5
    assert not a.errors and not a._conflicts and a._limit_holds == {}


def test_drag_rests_at_the_limit_without_jitter():
    a, m, _ip, ipin = _slider_at(10.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    _drag_along_axis(a, m, ipin, 70.0)
    seen = []
    for _ in range(4):
        _drag_along_axis(a, m, ipin, 30.0, steps=1)
        seen.append(m["values"]["tz"])
    assert max(seen) - min(seen) < 1e-6


def test_drive_past_the_limit_returns_the_limit():
    a, m, _ip, _ipin = _slider_at(0.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    reached, ok = a.drive_mate(m["id"], "tz", 80.0)
    assert ok is False and reached == pytest.approx(50.0, abs=1e-5)
    assert _measured(a, m)["tz"] == pytest.approx(50.0, abs=1e-5)
    assert not a._conflicts


def test_drive_inside_the_limit_still_succeeds():
    a, m, _ip, _ipin = _slider_at(0.0)
    a.set_limits(m["id"], "tz", 0.0, 50.0)
    reached, ok = a.drive_mate(m["id"], "tz", 50.0)
    assert ok and reached == pytest.approx(50.0, abs=1e-5)


def test_limit_that_cannot_be_met_marks_the_limited_mate():
    a = _asm()
    ip, ipin = a.insert(_src("plate")), a.insert(_src("pin"))
    from test_golden_mates import _edge, _hole_edge
    hole = a.connector(ip, "edge", _edge(a, ip, _hole_edge(0)))
    pin = a.connector(ipin, "edge", _edge(a, ipin, _hole_edge(0)))
    slider = a.make_mate("slider")
    slider.update(a=hole, b=pin, flip=True)
    a.add_mate(slider)
    _slide(a, slider, ipin, 80.0)
    # a fastened mate holds the pin where it is
    fast = a.make_mate("fastened")
    fast.update(a=a.connector(ip, "edge", _edge(a, ip, _hole_edge(0))), b=a.connector(ipin, "edge", _edge(a, ipin, _hole_edge(0))), flip=True, offset=-80.0)
    a.mates.insert(0, fast)
    a.solve()
    a.set_limits(slider["id"], "tz", 0.0, 50.0)
    assert slider["id"] in a.errors and "imit" in a.errors[slider["id"]]
    assert fast["id"] not in a.errors
    assert slider["id"] in a._conflicts and fast["id"] not in a._conflicts
    a.set_limits(slider["id"], "tz", None, None)
    assert not a.errors


def _gear():
    a = _asm()
    ip = a.insert(_src("plate"))
    m1 = _mate(a, "revolute", ip, _up)
    m2 = _mate(a, "revolute", ip, _plus_x)
    for m in (m1, m2):
        assert a.drive_mate(m["id"], "rz", 0.0)[1]
    _rel(a, "gear", m1, "rz", m2, "rz", 0.5)
    return a, m1, m2


def test_limit_on_the_output_of_a_gear_stops_the_input():
    a, m1, m2 = _gear()
    a.set_limits(m2["id"], "rz", -90.0, 90.0)
    reached, ok = a.drive_mate(m1["id"], "rz", 400.0)
    assert ok is False and reached == pytest.approx(180.0, abs=1e-4)
    assert m2["values"]["rz"] == pytest.approx(90.0, abs=1e-4)
    assert not a._conflicts and not a.errors
    reached, ok = a.drive_mate(m1["id"], "rz", -400.0)
    assert ok is False and reached == pytest.approx(-180.0, abs=1e-4)


def test_assembly_without_limits_never_enters_the_active_set(monkeypatch):
    a, m1, m2 = _gear()
    calls = []
    orig = a._solve_core
    monkeypatch.setattr(a, "_solve_core", lambda *x, **k: calls.append(1) or orig(*x, **k))
    a.solve()
    assert len(calls) == 1


@pytest.mark.parametrize("mtype,dof,target", [("revolute", "rz", 40.0), ("pin_slot", "tx", 15.0)])
def test_dof_after_drive_ignores_the_drive_residual(mtype, dof, target):
    from test_golden_mates import _face_mate
    a, m, _ip, _ib = _face_mate(mtype)
    before = a.dof
    reached, ok = a.drive_mate(m["id"], dof, target)
    assert ok and reached == pytest.approx(target, abs=1e-5)
    assert a.dof == before
    assert m["values"][dof] == pytest.approx(target, abs=1e-5)
    assert _measured(a, m)[dof] == pytest.approx(target, abs=1e-5)


def _gears_with_a_left_out_relation(limit_first=False):
    """Three revolute mates, A-B (0.5), B-C (0.5) and a contradictory A-C (0.5) that is left out once A turns."""
    from test_relation_errors import _gear, _gears
    a, (m1, m2, m3) = _gears(3)
    _gear(a, m1, m2), _gear(a, m2, m3)
    r13 = _gear(a, m1, m3)
    if limit_first:       # a static solve would settle the loop at zero, so the limit goes in before the drive
        a.set_limits(m2["id"], "rz", -30.0, 30.0)
    assert a.drive_mate(m1["id"], "rz", 20.0)[1]
    assert r13["id"] in a._conflicts and r13["id"] in a.errors
    return a, m1, m2, m3, r13


def test_drive_stops_at_a_limit_while_a_relation_is_left_out():
    a, m1, m2, m3, r13 = _gears_with_a_left_out_relation(limit_first=True)
    reached, ok = a.drive_mate(m1["id"], "rz", 150.0)
    assert ok is False and reached == pytest.approx(60.0, abs=1e-4)
    assert m2["values"]["rz"] == pytest.approx(30.0, abs=1e-4)
    assert m3["values"]["rz"] == pytest.approx(15.0, abs=1e-4)      # the surviving relation still holds
    assert a._conflicts == {r13["id"]}
    assert not any(k in a.errors for k in (m1["id"], m2["id"], m3["id"]))


def test_drag_stops_at_a_limit_while_a_relation_is_left_out():
    a, m1, m2, m3, r13 = _gears_with_a_left_out_relation(limit_first=True)
    ib = m1["b"]["inst"]
    FA = a.world_frame(m1["a"])
    z, o = np.asarray(FA["z"], float), np.asarray(FA["o"], float)
    T0 = np.array(next(i for i in a.instances if i["id"] == ib)["T"], float)
    p0 = T0[:3, 3]
    tangent = np.cross(z, p0 - o)
    tangent /= np.linalg.norm(tangent)
    P = np.array([tangent, np.cross(z, tangent)])
    for k in range(1, 40):
        a.solve(drag={"inst": ib, "local": np.zeros(3), "target": p0 + tangent * 2.0 * k, "P": P}, iters=8)
    assert m2["values"]["rz"] == pytest.approx(30.0, abs=1e-3)
    assert m1["values"]["rz"] == pytest.approx(60.0, abs=1e-2)
    assert a._conflicts == {r13["id"]}
    assert _residual(a, m1) < 1e-4 and _residual(a, m2) < 1e-4
