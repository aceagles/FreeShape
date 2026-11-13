"""mate_values: each Mate value reads back the amount a snapped pose was moved by."""
import itertools

import numpy as np
import pytest

from freeshape import assembly as A

TOL = 1e-9


def _frame(o, z, x):
    z = A.unit(np.array(z, float))
    x = np.array(x, float)
    x = A.unit(x - (x @ z) * z)
    return {"o": np.array(o, float), "z": z, "x": x}


FA = _frame([10.0, -4.0, 7.0], [1.0, 2.0, 2.0], [0.0, 1.0, -1.0])
FB0 = _frame([-3.0, 5.0, 2.0], [0.3, -0.5, 0.8], [1.0, 0.1, 0.0])

CASES = [dict(flip=f, offset=o, angle=a) for f, o, a in itertools.product((False, True), (0.0, 3.5), (0.0, 37.0, -120.0))]


def _snapped(m):
    """B in the snap pose, moved to the zero of every free freedom."""
    T = A.snap_transform(m, FA, FB0)
    fb = A.transform_frame(T, FB0)
    ang = np.radians(m["angle"])
    zA = A.unit(FA["z"])
    xt = np.cos(ang) * FA["x"] + np.sin(ang) * np.cross(zA, FA["x"])
    fb["x"] = A.unit(xt)
    # the snap keeps B's place along the free directions; the zero pose is the origin of the aligned frames
    fb["o"] = FA["o"] + (m["offset"] * zA if m["type"] in ("revolute", "planar") else 0.0)
    assert np.linalg.norm(A.mate_residuals(m, FA, fb, 1.0)) < 1e-9
    # fb["z"] is the snapped z; x is perpendicular to it by construction
    return fb


def _moved(m, fb, tx=0.0, ty=0.0, tz=0.0, rz=0.0):
    zA = A.unit(FA["z"])
    ang = np.radians(m["angle"])
    xt = np.cos(ang) * FA["x"] + np.sin(ang) * np.cross(zA, FA["x"])
    yt = np.cross(zA, xt)
    R = A.rot_about(zA, np.radians(rz), fb["o"])
    out = A.transform_frame(R, fb)
    out["o"] = out["o"] + tx * xt + ty * yt + tz * zA
    return out


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("mtype", ["revolute", "slider", "cylindrical", "planar"])
def test_each_freedom_reads_back(mtype, case):
    m = dict(type=mtype, **case)
    fb = _snapped(m)
    assert set(A.mate_values(m, FA, fb)) == set(A.MATE_VALUE_DOFS[mtype])
    for v in A.mate_values(m, FA, fb).values():
        assert abs(v) < TOL
    amounts = {"tx": 4.25, "ty": -6.5, "tz": 12.75, "rz": 53.0}
    for name in A.MATE_VALUE_DOFS[mtype]:
        got = A.mate_values(m, FA, _moved(m, fb, **{name: amounts[name]}))
        assert abs(got[name] - amounts[name]) < TOL
        for other, v in got.items():
            if other != name:
                assert abs(v) < TOL
    if mtype == "planar":
        got = A.mate_values(m, FA, _moved(m, fb, tx=4.25, ty=-6.5, rz=-80.0))
        assert got == pytest.approx({"tx": 4.25, "ty": -6.5, "rz": -80.0}, abs=TOL)


@pytest.mark.parametrize("mtype", ["fastened", "ball"])
def test_no_values(mtype):
    assert A.mate_values(dict(type=mtype), FA, FB0) == {}
    assert A.MATE_VALUE_DOFS[mtype] == ()


def test_rotation_wraps_to_half_open_interval():
    m = dict(type="revolute", flip=False, offset=0.0, angle=0.0)
    fb = _snapped(m)
    assert A.mate_values(m, FA, _moved(m, fb, rz=190.0))["rz"] == pytest.approx(-170.0, abs=TOL)
    assert A.mate_values(m, FA, _moved(m, fb, rz=-190.0))["rz"] == pytest.approx(170.0, abs=TOL)
    assert A.mate_values(m, FA, _moved(m, fb, rz=180.0))["rz"] == pytest.approx(180.0, abs=TOL)
    assert A.mate_values(m, FA, _moved(m, fb, rz=-180.0))["rz"] == pytest.approx(180.0, abs=TOL)


def test_table_covers_every_mate_type():
    assert set(A.MATE_VALUE_DOFS) == set(A.MATE_TYPES)
    for t, names in A.MATE_VALUE_DOFS.items():
        assert len(names) <= A.MATE_DOF[t]
