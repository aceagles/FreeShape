"""Spur / helical gear geometry (freeshape/plugins/gears/spur_geometry.py, adapted from freecad.gears)."""
import functools
import hashlib
import math
import os
import re

import pytest

from conftest import FREEFORM, assert_golden, shape_fingerprint
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer

from freeshape import kernel as K
from freeshape.plugins.gears import pygears
from freeshape.plugins.gears.spur_geometry import GearShape, spur_gear

PYGEARS_DIR = os.path.dirname(pygears.__file__)
LABEL = re.compile(r"^[A-Za-z0-9_+-]+$")
NP = 8                      # coarse discretisation keeps the tests quick


@functools.lru_cache(maxsize=None)
def gear(z=15, m=1.0, h=5.0, **kw) -> GearShape:
    return spur_gear(z, m, h, num_points=NP, **dict(kw))


def volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def labels(g):
    return {label: f for f, label in g.face_labels}


def centroid(face):
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    p = g.CentreOfMass()
    return p.X(), p.Y(), p.Z()


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def vertices(shape):
    out = []
    exp = TopExp_Explorer(shape, TopAbs_VERTEX)
    while exp.More():
        out.append(K._as_vertex(exp.Current()))
        exp.Next()
    return out


def vertex_xyz(v):
    from OCP.BRep import BRep_Tool
    p = BRep_Tool.Pnt_s(v)
    return p.X(), p.Y(), p.Z()


# ------------------------------------------------------------------------------------------------ licence

def test_pygears_files_are_verbatim_copies():
    want = {
        "__init__.py": "2cb4ae1dfe60bb720dec1cf1efeeb8b51b6f0be88b13d2bd8e62a8e60f443c9e",
        "_functions.py": "a52bfee5d2096ac86e39982853f7eaf55fb1df9a56da5b1884851cb8e93d7cdd",
        "involute_tooth.py": "1ac8f1978781ea5a55d837ee95ca62e914be219a7772eb801098661de3cfb495",
    }
    for name, digest in want.items():
        with open(os.path.join(PYGEARS_DIR, name), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == digest, name


def test_adapted_file_keeps_header_and_credit():
    import freeshape.plugins.gears.spur_geometry as mod
    with open(mod.__file__, encoding="utf-8") as fh:
        head = fh.read(2500)
    assert "GNU General Public License as published by" in head
    assert "(at your option) any later version" in head
    assert "Adapted for FreeShape from freecad.gears" in head and "@ 4164073" in head


# ------------------------------------------------------------------------------------------------ geometry

def test_straight_gear_z15():
    g = gear()
    assert len(K.solids(g.solid)) == 1 and K.is_valid(g.solid)
    assert g.pitch_diameter == pytest.approx(15.0)
    assert g.tip_diameter == pytest.approx(17.0)
    assert g.root_diameter == pytest.approx(15 - 2 - 0.5)
    fp = shape_fingerprint(g.solid)
    lo, hi = fp["bbox"][:3], fp["bbox"][3:]
    assert lo[2] == pytest.approx(0, abs=1e-6) and hi[2] == pytest.approx(5, abs=1e-6)
    # the tips lie on the tip circle (odd tooth count: no tip opposite another, so the box is a bit smaller)
    assert max(math.hypot(*vertex_xyz(v)[:2]) for v in vertices(g.solid)) == pytest.approx(8.5, abs=0.01)
    assert hi[0] - lo[0] <= 17.0 + 0.01 and hi[1] - lo[1] <= 17.0 + 0.01
    assert hi[0] - lo[0] > 16.5 and hi[1] - lo[1] > 16.5


def test_even_tooth_count_bbox_is_tip_diameter():
    g = gear(16)
    fp = shape_fingerprint(g.solid)
    # the tip is a straight chord (as in freecad.gears), so its middle sits ~0.01 inside the tip circle
    assert fp["bbox"][3] - fp["bbox"][0] == pytest.approx(g.tip_diameter, abs=0.02)
    assert fp["bbox"][4] - fp["bbox"][1] == pytest.approx(g.tip_diameter, abs=0.02)


def test_helical_keeps_volume_and_twists_top():
    straight, helical = gear(), gear(helix_angle=20.0)
    assert K.is_valid(helical.solid) and len(K.solids(helical.solid)) == 1
    assert volume(helical.solid) == pytest.approx(volume(straight.solid), rel=0.005)
    assert helical.pitch_diameter == pytest.approx(15.0)
    assert twist_of(helical) == pytest.approx(5.0 * math.tan(math.radians(20)) * 2 / 15.0, abs=1e-3)
    assert twist_of(gear(helix_angle=-20.0)) == pytest.approx(-5.0 * math.tan(math.radians(20)) * 2 / 15.0, abs=1e-3)
    assert twist_of(straight) == pytest.approx(0.0, abs=1e-9)


def twist_of(g, label="t1_tip"):
    """Angle by which the top edge of a labelled side face is turned relative to its bottom edge."""
    pts = [vertex_xyz(v) for v in vertices(labels(g)[label])]
    zlo, zhi = min(p[2] for p in pts), max(p[2] for p in pts)
    mean = lambda z: sum(math.atan2(y, x) for x, y, zz in pts if abs(zz - z) < 1e-6) / sum(
        1 for p in pts if abs(p[2] - z) < 1e-6)
    return wrap(mean(zhi) - mean(zlo))


def test_double_helix():
    straight, dbl = gear(), gear(helix_angle=20.0, double_helix=True)
    assert K.is_valid(dbl.solid) and len(K.solids(dbl.solid)) == 1
    assert volume(dbl.solid) == pytest.approx(volume(straight.solid), rel=0.005)
    lab = labels(dbl)
    assert len(lab) == len(dbl.face_labels)
    # each half twists by half the angle, in opposite senses going up: a herringbone
    half = 5.0 * math.tan(math.radians(20)) / 15.0
    assert twist_of(dbl) == pytest.approx(half, abs=1e-3)
    assert twist_of(dbl, "t1_tip_lo") == pytest.approx(-half, abs=1e-3)
    assert all(LABEL.match(k) for k in lab)
    assert {"t1_flankA", "t1_flankA_lo", "top", "bottom"} <= set(lab)


def test_undercut_z8_is_valid():
    g = spur_gear(8, 1.0, 5.0, undercut=True, num_points=NP)
    assert K.is_valid(g.solid) and len(K.solids(g.solid)) == 1


def test_bad_arguments():
    with pytest.raises(ValueError):
        spur_gear(2, 1.0, 5.0)
    with pytest.raises(ValueError):
        spur_gear(15, 1.0, 5.0, bore=14.0)
    with pytest.raises(ValueError):
        spur_gear(15, 0.0, 5.0)


# ------------------------------------------------------------------------------------------------ labels

@pytest.mark.parametrize("kw", [
    dict(z=15), dict(z=16), dict(z=15, bore=5.0), dict(z=16, bore=5.0),
    dict(z=15, helix_angle=20.0), dict(z=15, helix_angle=20.0, bore=5.0),
])
def test_labels_cover_every_face_once(kw):
    g = gear(**kw)
    z = kw["z"]
    faces = K.faces(g.solid)
    assert len(g.face_labels) == len(faces)
    for f in faces:
        assert sum(1 for h, _ in g.face_labels if h.IsSame(f)) == 1
    names = [label for _, label in g.face_labels]
    assert len(set(names)) == len(names)
    assert all(LABEL.match(n) for n in names)
    assert {"top", "bottom"} <= set(names)
    assert ("bore" in names) == (kw.get("bore", 0) > 0)
    for part in ("rootA", "flankA", "tip", "flankB", "rootB", "gap"):
        assert sum(1 for n in names if re.fullmatch(rf"t\d+_{part}", n)) == z, part
    assert sum(1 for n in names if n.endswith("_flankA")) == z


@pytest.mark.parametrize("kw", [dict(z=15), dict(z=16, bore=5.0), dict(z=15, helix_angle=20.0)])
def test_flank_a_lies_in_its_tooth_sector(kw):
    g = gear(**kw)
    z = kw["z"]
    phi = 2 * math.pi / z
    lab = labels(g)
    turn = 5.0 * math.tan(math.radians(kw.get("helix_angle", 0))) * 2 / 15.0
    for i in range(1, z + 1):
        x, y, _ = centroid(lab[f"t{i}_flankA"])
        d = wrap(math.atan2(y, x) - (i - 1) * phi - turn / 2)
        assert abs(d) < phi / 2, (i, d)
        assert d < 0                       # flank A is the leading flank, before the tooth's centre line


def test_bore_face_is_the_cylinder():
    g = gear(bore=5.0)
    bore = labels(g)["bore"]
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    surf = BRepAdaptor_Surface(bore)
    assert surf.GetType() == GeomAbs_Cylinder and surf.Cylinder().Radius() == pytest.approx(2.5)
    assert volume(g.solid) < volume(gear().solid)


# ------------------------------------------------------------------------------------------------ meshing

@pytest.mark.slow
def test_gears_mesh_without_overlap():
    a = spur_gear(15, 1.0, 5.0, num_points=20)
    b = spur_gear(21, 1.0, 5.0, num_points=20)
    phi2 = 2 * math.pi / 21
    delta = (math.pi - phi2 / 2) % phi2          # turn gear b so a gap faces gear a's tooth 1 (on +X)
    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), delta)
    mv = gp_Trsf()
    mv.SetTranslation(gp_Vec(18.0, 0, 0))
    moved = BRepBuilderAPI_Transform(b.solid, mv.Multiplied(rot), True).Shape()
    common = BRepAlgoAPI_Common(a.solid, moved)
    assert common.IsDone()
    assert volume(common.Shape()) < 0.01 * volume(a.solid)


# ------------------------------------------------------------------------------------------------ golden

@pytest.mark.parametrize("key,kw", [
    ("straight_z15", dict(z=15)),
    ("helical_z15_b20", dict(z=15, helix_angle=20.0)),
    ("bored_z16", dict(z=16, bore=5.0)),
])
def test_gear_golden(key, kw):
    assert_golden(f"gears_spur/{key}", shape_fingerprint(gear(**kw).solid), FREEFORM)
