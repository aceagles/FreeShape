"""Straight bevel gear geometry (freeshape/addons/gears/bevel_geometry.py, ported from freecad.gears).

Goldens: tests/golden/gears_bevel.json, recorded on cadquery-ocp 7.9.3.1.1.

Volumes here use OCC's plain Gauss integrator, not conftest.shape_fingerprint's adaptive one: at 1e-4 the
adaptive integrator needs minutes on some flat strips of these lofts (one gap face alone took 112 s), while
the plain one is exact for the degree-3-by-1 and planar faces these gears are made of."""
import hashlib
import math
import os
import re

import numpy as np
import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRep import BRep_Tool
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopExp import TopExp

from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX

from conftest import FREEFORM, _count, assert_golden
from freeshape import kernel as K
from freeshape.addons.gears.bevel_geometry import bevel_gear, cone_limit

PYGEARS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "freeshape", "addons", "gears", "pygears")
NUM_POINTS = 8          # coarse flanks keep the suite quick; the defaults are FreeCAD's 20

LABEL = re.compile(r"[A-Za-z0-9_+-]+")


@pytest.fixture(scope="module")
def gear15():
    return bevel_gear(15, 1.0, 5.0, pitch_angle=45.0, num_points=NUM_POINTS)


def _labels(gear):
    return [lab for _, lab in gear.face_labels]


def _point(vertex):
    p = BRep_Tool.Pnt_s(vertex)
    return np.array([p.X(), p.Y(), p.Z()])


def _volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _fingerprint(shape):
    """conftest.shape_fingerprint's keys, with the plain volume integrator (see the module docstring)."""
    g, sa = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    BRepGProp.SurfaceProperties_s(shape, sa)
    com = g.CentreOfMass()
    lo, hi = K.bbox(shape)
    return {
        "volume": g.Mass(),
        "area": sa.Mass(),
        "com": [com.X(), com.Y(), com.Z()],
        "bbox": list(lo) + list(hi),
        "diag": float(np.linalg.norm(hi - lo)),
        "solids": _count(shape, TopAbs_SOLID),
        "shells": _count(shape, TopAbs_SHELL),
        "faces": _count(shape, TopAbs_FACE),
        "edges": _count(shape, TopAbs_EDGE),
        "vertices": _count(shape, TopAbs_VERTEX),
        "valid": bool(K.is_valid(shape)),
    }


def _face(gear, label):
    (face,) = [f for f, lab in gear.face_labels if lab == label]
    return face


@pytest.mark.parametrize("name, digest", [
    ("__init__.py", "2cb4ae1dfe60bb720dec1cf1efeeb8b51b6f0be88b13d2bd8e62a8e60f443c9e"),
    ("_functions.py", "a52bfee5d2096ac86e39982853f7eaf55fb1df9a56da5b1884851cb8e93d7cdd"),
    ("bevel_tooth.py", "76f6653a9ca55776edbb3f55e6b622a806007274afb15c1e3b1b2517e01a4eed"),
])
def test_pygears_files_are_verbatim_copies(name, digest):
    with open(os.path.join(PYGEARS, name), "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == digest


def test_one_valid_solid(gear15):
    assert len(K.solids(gear15.solid)) == 1
    assert K.is_valid(gear15.solid)
    assert _volume(gear15.solid) > 0
    assert K.rough_volume(gear15.solid) == pytest.approx(_volume(gear15.solid), rel=0.01)   # quick, see above


def test_diameters_and_heel_size(gear15):
    z, m, delta = 15, 1.0, math.radians(45.0)
    assert gear15.pitch_diameter == pytest.approx(m * z)
    lo, hi = K.bbox(_face(gear15, "heel"))
    assert abs(lo[2]) < 1e-6 and abs(hi[2]) < 1e-6           # the heel lies on z=0
    size = max(hi[0] - lo[0], hi[1] - lo[1])
    # FreeCAD's heel is flat, so the tips there lie on the tip cone (half-angle delta + 2 sin(delta)/z), not
    # on the back-cone tip circle m(z + 2 cos delta) = 16.41: the heel tip diameter is 18.13 for this gear.
    heel_tip = 2 * cone_limit(z, m, 45.0) * math.tan(delta + 2 * math.sin(delta) / z)
    assert gear15.tip_diameter == pytest.approx(heel_tip, rel=1e-6)
    assert size == pytest.approx(heel_tip, rel=0.02)
    assert gear15.root_diameter < m * z < gear15.tip_diameter
    lo, hi = K.bbox(_face(gear15, "toe"))
    assert lo[2] == pytest.approx(5.0, abs=1e-6) and hi[2] == pytest.approx(5.0, abs=1e-6)


def test_pitch_cone_apex_where_documented(gear15):
    """The tooth-tip face is a ruled strip between the toe and heel tips; its side edges run through the
    apex, which the module docstring places at (0, 0, cone_limit(...))."""
    apex_z = cone_limit(15, 1.0, 45.0)
    assert apex_z == pytest.approx(7.5)
    found = 0
    for e in K.edges(_face(gear15, "t1_tip")):
        a, b = (_point(TopExp.FirstVertex_s(e)), _point(TopExp.LastVertex_s(e)))
        if abs(a[2] - b[2]) < 1e-6:
            continue                         # an edge on the heel or toe cap
        heel, toe = (a, b) if a[2] < b[2] else (b, a)
        r0, r1 = math.hypot(heel[0], heel[1]), math.hypot(toe[0], toe[1])
        t = r0 / (r0 - r1)
        p = heel + t * (toe - heel)
        assert math.hypot(p[0], p[1]) < 0.01
        assert p[2] == pytest.approx(apex_z, abs=0.01)
        found += 1
    assert found == 2


def test_face_labels_cover_every_face(gear15):
    labels = _labels(gear15)
    faces = K.faces(gear15.solid)
    assert len(labels) == len(faces) == len(set(labels))
    for face in faces:
        assert sum(face.IsSame(f) for f, _ in gear15.face_labels) == 1
    assert all(LABEL.fullmatch(lab) for lab in labels)
    assert sum(lab.endswith("_flankA") for lab in labels) == 15
    assert {"heel", "toe"} <= set(labels)
    assert {f"t{i}_{part}" for i in range(1, 16) for part in ("flankA", "tip", "flankB", "gap")} <= set(labels)


def test_bore_is_labelled():
    gear = bevel_gear(15, 1.0, 5.0, bore=4.0, num_points=NUM_POINTS)
    assert K.is_valid(gear.solid)
    labels = _labels(gear)
    assert "bore" in labels
    assert len(labels) == len(K.faces(gear.solid)) == len(set(labels))
    lo, hi = K.bbox(_face(gear, "bore"))
    assert hi[0] - lo[0] == pytest.approx(4.0, rel=1e-3)


def test_height_at_cone_limit_is_rejected():
    with pytest.raises(ValueError, match=r"7\.5"):
        bevel_gear(15, 1.0, 7.5)
    with pytest.raises(ValueError, match=r"7\.5"):
        bevel_gear(15, 1.0, 9.0)


def _moved(shape, trsf):
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _rotation(axis_dir, angle):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(*axis_dir)), angle)
    return t


@pytest.mark.slow
def test_pair_meshes():
    """z=15 on z=30 at 90 degrees: apexes coincident, a tooth of the pinion in a gap of the wheel."""
    d1 = math.degrees(math.atan(15 / 30))
    d2 = 90.0 - d1
    g1 = bevel_gear(15, 1.0, 4.0, pitch_angle=d1, num_points=NUM_POINTS)
    g2 = bevel_gear(30, 1.0, 4.0, pitch_angle=d2, num_points=NUM_POINTS)
    a1, a2 = cone_limit(15, 1.0, d1), cone_limit(30, 1.0, d2)
    # pinion: tooth 1 (centred on +Y) turned to +X, where the pitch cones touch
    s1 = _moved(g1.solid, _rotation((0, 0, 1), -math.pi / 2))
    # wheel: a gap centre (tooth 1 at +Y, gaps half a pitch either side) turned to local -X, then its axis
    # laid along -X (apex towards -X) and its apex moved onto the pinion's
    gap = math.pi / 2 + math.pi / 30
    s2 = _moved(g2.solid, _rotation((0, 0, 1), math.pi - gap))
    s2 = _moved(s2, _rotation((0, 1, 0), -math.pi / 2))
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(a2, 0, a1))
    s2 = _moved(s2, move)
    common = BRepAlgoAPI_Common(s1, s2).Shape()
    overlap = _volume(common) if K.solids(common) else 0.0
    # the card asks for < 2 %; the correct phase gives 0, a quarter pitch off gives 1.6 % and half a pitch 2.9 %,
    # so 0.2 % is what actually tells a meshing pair from a mis-phased one
    assert overlap < 0.002 * _volume(s1)


@pytest.mark.parametrize("z", [15, 30])
def test_fingerprint_golden(z):
    gear = bevel_gear(z, 1.0, 5.0, pitch_angle=45.0, num_points=NUM_POINTS)
    assert_golden(f"gears_bevel/z{z}_m1_h5_pa45", _fingerprint(gear.solid), FREEFORM)
