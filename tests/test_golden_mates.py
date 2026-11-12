"""Every mate type with flip, offset and angle: where the solver puts the free part, and the DOF it reports.

These record the current assembly solver's results as the baseline for swapping in another solver
(e.g. OndselSolver). Under-constrained mates (slider, cylindrical, planar, ball, revolute) leave the part
wherever the solver's minimum-motion step takes it; those placements are characterisation, not a spec, and
are named `*_free` so a solver swap knows which ones may legitimately move.
"""
import itertools

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from conftest import assert_golden, matrix_list
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape.features import Part


def _box(x, y, z, at=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*at), x, y, z).Shape()


def _cyl(r, h, at=(0, 0, 0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


LIB = {
    "plate": K.cut(_box(60, 40, 10), _cyl(4, 12, (15, 20, -1))),
    "block": _box(20, 10, 8),
    "pin": _cyl(4, 30),
    "arm": K.cut(_box(50, 12, 6), _cyl(4, 8, (6, 6, -1))),
}


def _asm():
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", src["part"], LIB[src["part"]], (0.7, 0.7, 0.7), None)
    return a


def _src(name):
    return {"kind": "studio", "uid": "u1", "part": name}


def _face(a, iid, pred):
    return next(i for i, f in enumerate(K.faces(a.parts[iid].shape)) if pred(K.face_info(f)))


def _edge(a, iid, pred):
    return next(i for i, e in enumerate(K.edges(a.parts[iid].shape)) if pred(K.edge_info(e)))


def _up(i):
    return i.get("n") is not None and i["n"][2] > 0.9


def _down(i):
    return i.get("n") is not None and i["n"][2] < -0.9


def _hole_edge(z):
    return lambda e: abs(e.get("r", 0) - 4) < 1e-6 and abs(e["center"][2] - z) < 1e-6


VARIANTS = {"plain": {}, "flip": {"flip": True}, "offset": {"offset": 3.0}, "angle": {"angle": 30.0},
            "offset_angle": {"offset": -2.5, "angle": 135.0}}


def _face_mate(mtype, **kw):
    a = _asm()
    ip, ib = a.insert(_src("plate")), a.insert(_src("block"))
    ca = a.connector(ip, "face", _face(a, ip, _up))
    cb = a.connector(ib, "face", _face(a, ib, _down))
    m = a.make_mate(mtype)
    m.update(a=ca, b=cb, **kw)
    a.add_mate(m)
    return a, m, ip, ib


def _residual(a, m):
    return float(np.linalg.norm(A.mate_residuals(m, a.world_frame(m["a"]), a.world_frame(m["b"]),
                                                 a.length_scale())))


def _record(a, extra=None):
    out = {"T": {i["id"]: matrix_list(i["T"]) for i in a.instances}, "dof": a.dof,
           "errors": sorted(a.errors)}
    out.update(extra or {})
    return out


@pytest.mark.parametrize("mtype,variant", list(itertools.product(sorted(A.MATE_TYPES), sorted(VARIANTS))))
def test_face_mate(mtype, variant):
    kw = VARIANTS[variant]
    a, m, ip, ib = _face_mate(mtype, **kw)
    assert not a.errors, a.errors
    assert a.dof == A.MATE_DOF[mtype]
    assert _residual(a, m) < 1e-6
    assert np.allclose(a.instance(ip)["T"], np.eye(4))           # the first instance is fixed
    FA, FB = a.world_frame(m["a"]), a.world_frame(m["b"])
    s = 1.0 if kw.get("flip") else -1.0
    d = kw.get("offset", 0.0)
    if mtype != "ball":
        assert np.allclose(FB["z"], s * FA["z"], atol=1e-9)
    if mtype in ("fastened", "revolute", "ball"):
        assert np.allclose(FB["o"], FA["o"] + d * FA["z"], atol=1e-7)
    if mtype == "planar":
        assert abs(float((FB["o"] - FA["o"]) @ FA["z"]) - d) < 1e-7
    if mtype in ("fastened", "slider"):
        ang = np.radians(kw.get("angle", 0.0))
        yA = np.cross(FA["z"], FA["x"])
        assert np.allclose(FB["x"], np.cos(ang) * FA["x"] + np.sin(ang) * yA, atol=1e-7)
    if mtype == "fastened" and not kw.get("flip"):
        lo, _hi = K.bbox(a.world_shape(ib))
        assert abs(lo[2] - (10 + d)) < 1e-5                        # sits on the plate, `offset` above it
    free = "" if mtype == "fastened" else "_free"
    assert_golden(f"mates/face_{mtype}_{variant}{free}", _record(a), abs_tol=1e-6)


def _pin_in_hole(mtype, **kw):
    a = _asm()
    ip, ipin = a.insert(_src("plate")), a.insert(_src("pin"))
    hole_bot = a.connector(ip, "edge", _edge(a, ip, _hole_edge(0)))
    pin_bot = a.connector(ipin, "edge", _edge(a, ipin, _hole_edge(0)))
    m = a.make_mate(mtype)
    m.update(a=hole_bot, b=pin_bot, flip=True, **kw)
    a.add_mate(m)
    return a, m, ip, ipin


@pytest.mark.parametrize("mtype", ["fastened", "revolute", "cylindrical", "slider", "ball"])
@pytest.mark.parametrize("offset", [0.0, -5.0])
def test_pin_in_hole_edge_mate(mtype, offset):
    a, m, ip, ipin = _pin_in_hole(mtype, offset=offset)
    assert not a.errors, a.errors
    assert a.dof == A.MATE_DOF[mtype]
    assert _residual(a, m) < 1e-6
    lo, hi = K.bbox(a.world_shape(ipin))
    if mtype in ("fastened", "revolute", "ball"):
        # the hole's bottom circle points -Z; flip aligns the pin's bottom circle with it, `offset` along -Z
        assert np.allclose([lo[0], lo[1], hi[0], hi[1]], [11, 16, 19, 24], atol=1e-5)
        assert abs(lo[2] - (-offset)) < 1e-5, lo
    if mtype in ("cylindrical", "slider"):
        assert np.allclose([lo[0], lo[1], hi[0], hi[1]], [11, 16, 19, 24], atol=1e-5)  # on the hole's axis
    free = "" if mtype == "fastened" else "_free"
    assert_golden(f"mates/pin_{mtype}_off{offset:g}{free}", _record(a), abs_tol=1e-6)


def test_revolute_angle_is_ignored_and_fastened_angle_is_not():
    """`angle` only constrains mates that fix the rotation about Z (fastened, slider)."""
    ang = {}
    for mtype in ("fastened", "revolute"):
        for deg in (0.0, 60.0):
            a, m, ip, ipin = _pin_in_hole(mtype, angle=deg)
            ang[(mtype, deg)] = np.array(a.instance(ipin)["T"])
    assert np.allclose(ang[("revolute", 0.0)], ang[("revolute", 60.0)], atol=1e-9)
    R = ang[("fastened", 60.0)][:3, :3] @ ang[("fastened", 0.0)][:3, :3].T
    assert abs(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))) - 60) < 1e-6


def test_arm_revolute_with_offset_and_two_mates():
    a = _asm()
    ip, ipin, iarm = a.insert(_src("plate")), a.insert(_src("pin")), a.insert(_src("arm"))
    m1 = a.make_mate("fastened")
    m1.update(a=a.connector(ip, "edge", _edge(a, ip, _hole_edge(0))),
              b=a.connector(ipin, "edge", _edge(a, ipin, _hole_edge(0))), flip=True)
    a.add_mate(m1)
    m2 = a.make_mate("revolute")
    m2.update(a=a.connector(ip, "edge", _edge(a, ip, _hole_edge(10))),
              b=a.connector(iarm, "edge", _edge(a, iarm, _hole_edge(0))), offset=2.0)
    a.add_mate(m2)
    assert not a.errors and a.dof == 1
    lo, hi = K.bbox(a.world_shape(iarm))
    assert abs(lo[2] - 12) < 1e-5 and abs(hi[2] - 18) < 1e-5     # 2 mm above the plate
    assert_golden("mates/arm_revolute_offset2_free", _record(a, {"instance_dof": {
        i["id"]: a.instance_dof(i["id"]) for i in a.instances}}), abs_tol=1e-6)


def test_suppressed_mate_frees_the_part():
    a, m, ip, ib = _face_mate("fastened")
    a.set_suppressed(m["id"], True)
    assert a.dof == 6 and a.instance_dof(ib) == 6
    a.set_suppressed(m["id"], False)
    assert a.dof == 0
