"""The pin slot mate (I3.1): Z axes aligned, B's origin on A's X line, free along X and about Z."""
import numpy as np
import pytest

from freeshape import assembly as A
from test_golden_mates import _face_mate, _residual


def test_registered_with_two_freedoms():
    assert A.MATE_DOF["pin_slot"] == 2
    assert A.MATE_VALUE_DOFS["pin_slot"] == ("tx", "rz")
    assert "pin_slot" in A.MATE_TYPES


def test_dof_is_two_and_values_start_at_zero_pose():
    a, m, _ip, _ib = _face_mate("pin_slot")
    assert not a.errors, a.errors
    assert a.dof == 2
    assert _residual(a, m) < 1e-6
    assert set(m["values"]) == {"tx", "rz"}


def test_drive_tx_moves_pin_along_a_x():
    a, m, _ip, _ib = _face_mate("pin_slot")
    FA = a.world_frame(m["a"])
    p0 = a.world_frame(m["b"])["o"].copy()
    t0 = m["values"]["tx"]
    reached, ok = a.drive_mate(m["id"], "tx", 15.0)
    assert ok and reached == pytest.approx(15.0, abs=1e-6)
    moved = a.world_frame(m["b"])["o"] - p0
    assert np.allclose(moved, (15.0 - t0) * FA["x"], atol=1e-5)
    assert _residual(a, m) < 1e-6


def test_drive_rz_turns_pin_in_place():
    a, m, _ip, _ib = _face_mate("pin_slot")
    p0 = a.world_frame(m["b"])["o"].copy()
    reached, ok = a.drive_mate(m["id"], "rz", 90.0)
    assert ok and reached == pytest.approx(90.0, abs=1e-6)
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    yA = np.cross(FA["z"], FA["x"])
    assert float(FB["x"] @ yA) == pytest.approx(1.0, abs=1e-6)
    assert np.allclose(FB["o"], p0, atol=1e-5)
    assert _residual(a, m) < 1e-6


def test_pin_displaced_along_y_is_pulled_back_onto_the_slot_line():
    a, m, _ip, ib = _face_mate("pin_slot")
    FA = a.world_frame(m["a"])
    yA = np.cross(FA["z"], FA["x"])
    a.move_instance(ib, A.translation(7.0 * yA), checkpoint=False, solve=False)
    assert _residual(a, m) > 1.0
    a.solve()
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    assert not a.errors, a.errors
    assert abs(float((FB["o"] - FA["o"]) @ yA)) < 1e-5
    assert _residual(a, m) < 1e-6


def test_offset_holds_pin_above_the_slot_plane():
    a, m, _ip, _ib = _face_mate("pin_slot", offset=3.0)
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    assert float((FB["o"] - FA["o"]) @ FA["z"]) == pytest.approx(3.0, abs=1e-6)


def test_snap_keeps_position_along_the_slot():
    a, m, _ip, _ib = _face_mate("pin_slot")
    a.drive_mate(m["id"], "tx", 12.0)
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    M = A.snap_transform(m, FA, FB)
    assert np.allclose(M, np.eye(4), atol=1e-6)
