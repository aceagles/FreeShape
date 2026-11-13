"""AssemblyDoc.drive_mate moves one freedom of a mate to a value in bounded steps (I1.3)."""
import numpy as np
import pytest

from freeshape import assembly as A
from freeshape import kernel as K
from test_golden_mates import _face, _face_mate, _residual, _up


def _poses(a):
    return {i["id"]: np.array(i["T"], float) for i in a.instances}


def _turn_pose(a, m, iid, deg):
    FA = a.world_frame(m["a"])
    a.move_instance(iid, A.rot_about(FA["z"], np.radians(deg), FA["o"]), checkpoint=False)
    return np.array(a.instance(iid)["T"], float)


def test_hinge_driven_to_450():
    a, m, _ip, ib = _face_mate("revolute")
    start = m["values"]["rz"]
    T0 = np.array(a.instance(ib)["T"], float)
    assert a.drive_mate(m["id"], "rz", 450.0) == (pytest.approx(450.0, abs=1e-6), True)
    assert m["values"]["rz"] == pytest.approx(450.0, abs=1e-6)
    ref, mr, _ip2, ib2 = _face_mate("revolute")
    want = _turn_pose(ref, mr, ib2, 450.0 - start)
    assert np.allclose(a.instance(ib)["T"], want, atol=1e-6)
    assert not np.allclose(T0, want)
    assert _residual(a, m) < 1e-6


def test_slider_driven_to_25():
    a, m, _ip, ib = _face_mate("slider")
    FA = a.world_frame(m["a"])
    p0 = a.world_frame(m["b"])["o"].copy()
    t0 = m["values"]["tz"]
    reached, ok = a.drive_mate(m["id"], "tz", 25.0)
    assert ok and reached == pytest.approx(25.0, abs=1e-6)
    assert m["values"]["tz"] == pytest.approx(25.0, abs=1e-6)
    moved = a.world_frame(m["b"])["o"] - p0
    assert np.allclose(moved, (25.0 - t0) * FA["z"], atol=1e-5)


def test_steps_are_bounded(monkeypatch):
    a, m, _ip, _ib = _face_mate("revolute")
    seen = []
    real = a.solve

    def spy(*args, **kw):
        seen.append(a.mates[0]["values"]["rz"])
        return real(*args, **kw)
    monkeypatch.setattr(a, "solve", spy)
    a.drive_mate(m["id"], "rz", 200.0)
    steps = np.diff([seen[0]] + seen[1:] + [m["values"]["rz"]])
    assert len(seen) >= 7 and np.all(np.abs(steps) <= 30.0 + 1e-6)


def _blocked():
    a, m, ip, ib = _face_mate("revolute")
    plate_side = _face(a, ip, lambda i: i.get("n") is not None and i["n"][0] > 0.9)
    block_side = _face(a, ib, lambda i: i.get("n") is not None and i["n"][0] < -0.9)
    ca = a.connector(ip, "face", plate_side)
    cb = a.connector(ib, "face", block_side)
    m2 = a.make_mate("planar")
    m2.update(a=ca, b=cb, offset=float((a.world_frame(cb)["o"] - a.world_frame(ca)["o"]) @ a.world_frame(ca)["z"]))
    a.add_mate(m2)
    return a, m, m2


def test_blocked_drive_stops_and_keeps_mates_satisfied():
    a, m, m2 = _blocked()
    assert not a.errors and _residual(a, m2) < 1e-6
    reached, ok = a.drive_mate(m["id"], "rz", 90.0)
    assert ok is False
    assert abs(reached - m["values"]["rz"]) < 1e-9 and abs(reached) < 90.0
    for mm in (m, m2):
        assert _residual(a, mm) < 1e-6


def test_one_undo_restores_poses_and_values():
    a, m, _ip, _ib = _face_mate("revolute")
    T0, v0 = _poses(a), dict(m["values"])
    n = len(a._undo)
    a.drive_mate(m["id"], "rz", 200.0)
    assert len(a._undo) == n + 1
    a.undo()
    for k, T in _poses(a).items():
        assert np.allclose(T, T0[k], atol=1e-9)
    assert a.mates[0]["values"] == v0


def test_one_notify_per_call():
    a, m, _ip, _ib = _face_mate("revolute")
    calls = []
    a.listeners.append(lambda: calls.append(1))
    a.drive_mate(m["id"], "rz", 100.0)
    assert len(calls) == 1


def test_unknown_mate_or_freedom_raises():
    a, m, _ip, _ib = _face_mate("revolute")
    n = len(a._undo)
    with pytest.raises(ValueError):
        a.drive_mate("nope", "rz", 1.0)
    with pytest.raises(ValueError):
        a.drive_mate(m["id"], "tz", 1.0)
    assert len(a._undo) == n


def test_drive_to_current_value_is_ok():
    a, m, _ip, _ib = _face_mate("revolute")
    v = m["values"]["rz"]
    assert a.drive_mate(m["id"], "rz", v) == (pytest.approx(v), True)
