"""The parallel mate (I3.2): only the Z axes are held parallel; the part is otherwise free."""
import numpy as np
import pytest

from freeshape import assembly as A
from test_golden_mates import _face_mate, _residual


def test_registered_with_four_freedoms():
    assert A.MATE_DOF["parallel"] == 4
    assert A.MATE_VALUE_DOFS["parallel"] == ("tx", "ty", "tz", "rz")
    assert "parallel" in A.MATE_TYPES


@pytest.mark.parametrize("flip", [False, True])
def test_solve_makes_axes_parallel_and_keeps_position(flip):
    a, m, _ip, ib = _face_mate("parallel", flip=flip)
    assert not a.errors, a.errors
    assert a.dof == 4
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    s = 1.0 if flip else -1.0
    assert np.allclose(FB["z"], s * FA["z"], atol=1e-9)
    # a part that starts tilted: adding the mate turns it about the connector and does not move the connector
    a.remove_mate(m["id"]) if hasattr(a, "remove_mate") else a.mates.remove(m)
    p0 = a.world_frame(m["b"])["o"].copy()
    a.move_instance(ib, A.rot_about(FB["x"], np.radians(25), p0), checkpoint=False, solve=False)
    assert np.linalg.norm(a.world_frame(m["b"])["z"] - s * FA["z"]) > 0.1
    a.add_mate(m)
    FB = a.world_frame(m["b"])
    assert not a.errors, a.errors
    assert np.allclose(FB["z"], s * FA["z"], atol=1e-9)
    assert np.allclose(FB["o"], p0, atol=1e-6)
    assert _residual(a, m) < 1e-9


def test_snap_rotates_without_translating():
    a, m, _ip, _ib = _face_mate("parallel")
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    FB = dict(FB, z=A.unit(FB["z"] + 0.4 * FA["x"]))
    FB["x"] = A.unit(np.cross(np.cross(FB["z"], FB["x"]), FB["z"]))
    M = A.snap_transform(m, FA, FB)
    assert np.allclose(M @ np.append(FB["o"], 1.0), np.append(FB["o"], 1.0), atol=1e-9)
    assert np.allclose(M[:3, :3] @ FB["z"], -FA["z"], atol=1e-9)


def test_drive_rz_to_450():
    a, m, _ip, _ib = _face_mate("parallel")
    reached, ok = a.drive_mate(m["id"], "rz", 450.0)
    assert ok and reached == pytest.approx(450.0, abs=1e-6)
    assert m["values"]["rz"] == pytest.approx(450.0, abs=1e-6)
    assert _residual(a, m) < 1e-6


def test_drive_tz_moves_part_along_axis():
    a, m, _ip, _ib = _face_mate("parallel")
    FA = a.world_frame(m["a"])
    p0 = a.world_frame(m["b"])["o"].copy()
    t0 = m["values"]["tz"]
    reached, ok = a.drive_mate(m["id"], "tz", 12.0)
    assert ok and reached == pytest.approx(12.0, abs=1e-6)
    moved = a.world_frame(m["b"])["o"] - p0
    assert np.allclose(moved, (12.0 - t0) * FA["z"], atol=1e-5)
