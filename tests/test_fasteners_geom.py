"""Shared profile and recess helpers of the fasteners plugin (port of the workbench helpers)."""
import csv
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.BRepCheck import BRepCheck_Analyzer

from freeshape import kernel as K
from freeshape.plugins.fasteners import geom

DATA = os.path.join(os.path.dirname(__file__), "data", "fasteners", "upstream", "FsData")


def load_table(name):
    with open(os.path.join(DATA, name + ".csv"), newline="") as f:
        rows = list(csv.reader(f))
    return {r[0]: tuple(float(v) for v in r[1:]) for r in rows[1:] if len(r) > 1}


def valid(shape):
    return BRepCheck_Analyzer(shape).IsValid()


def inside(shape, lo, hi, tol=1e-6):
    a, b = K.bbox(shape)
    return all(a[i] >= lo[i] - tol and b[i] <= hi[i] + tol for i in range(3))


def test_rectangle_revolves_to_cylinder():
    fm = geom.FSFaceMaker()
    fm.AddPoint(0.0, 0.0)
    fm.AddPoint(3.0, 0.0)
    fm.AddPoint(3.0, 7.0)
    fm.AddPoint(0.0, 7.0)
    solid = geom.RevolveZ(fm.GetFace())
    assert valid(solid)
    assert K.volume(solid) == pytest.approx(math.pi * 9 * 7, abs=1e-6)


def test_partial_revolve():
    fm = geom.FSFaceMaker()
    for p in ((0, 0), (3, 0), (3, 7), (0, 7)):
        fm.AddPoint(*p)
    half = geom.RevolveZ(fm.GetFace(), 180)
    assert K.volume(half) == pytest.approx(math.pi * 9 * 7 / 2, abs=1e-6)


def test_hex_prism_volume():
    prism = geom.makeHexPrism(5.5, 2.4)
    assert valid(prism)
    assert K.volume(prism) == pytest.approx(math.sqrt(3) / 2 * 5.5 ** 2 * 2.4, abs=1e-6)
    lo, hi = K.bbox(prism)
    assert hi[0] == pytest.approx(5.5 / math.sqrt(3), abs=1e-6)  # corner on +X
    assert lo[2] == pytest.approx(0.0, abs=1e-6) and hi[2] == pytest.approx(2.4, abs=1e-6)


@pytest.mark.parametrize("chamfer", [False, True])
def test_hex_recess(chamfer):
    w, d = 5.0, 3.0
    rec = geom.makeHexRecess(w, d, chamfer)
    assert valid(rec) and len(K.solids(rec)) == 1
    r = d + 1.005 * w / math.sqrt(3) if chamfer else w / math.sqrt(3)
    assert inside(rec, (-r, -r, -2 * d), (r, r, d))
    assert K.volume(rec) > 0


@pytest.mark.parametrize("ctype", ["0", "1", "2", "3", "4"])
def test_h_cross_recess(ctype):
    table = load_table("iso4757def")
    m = 5.0
    rec = geom.makeHCrossRecess(ctype, m, table)
    assert valid(rec) and len(K.solids(rec)) == 1
    g = table[ctype][2]
    rmax = m / 2 + m / 4 * math.tan(math.radians(26.5))
    t_tot = (m - g) / 2 / math.tan(math.radians(26.5)) + g / 2 * math.tan(math.radians(28))
    assert inside(rec, (-rmax, -rmax, -t_tot), (rmax, rmax, m / 4))
    assert 0 < K.volume(rec)


def test_hexalobular_recess():
    rec = geom.makeHexalobularRecess("T20", 2.0, True, {"T20": (3.86, 2.75, 0.4)})
    assert valid(rec) and K.volume(rec) > 0


def test_arc_profile_face():
    fm = geom.FSFaceMaker()
    fm.AddPoint(0.0, 0.0)
    fm.AddPoint(4.0, 0.0)
    fm.AddArc(5.0, 1.0, 4.0, 2.0)
    fm.AddPoint(0.0, 2.0)
    face = fm.GetFace()
    assert valid(face)
    assert valid(geom.RevolveZ(face))


def test_arc2_profile_face():
    fm = geom.FSFaceMaker()
    fm.AddPoint(0.0, 0.0)
    fm.AddPoint(4.0, 0.0)
    fm.AddArc2(0.0, 1.0, 90)  # quarter circle, centre 1 above the last point
    fm.AddPoint(0.0, 3.0)
    face = fm.GetFace()
    assert valid(face)
    assert valid(geom.RevolveZ(face))


def test_arc2_end_point():
    fm = geom.FSFaceMaker()
    fm.AddPoint(4.0, 0.0)
    fm.AddArc2(0.0, -1.0, 90)  # centre (4, -1), start angle +90 deg, sweeps to 180 deg
    assert (fm.lastPoint.X(), fm.lastPoint.Z()) == pytest.approx((3.0, -1.0), abs=1e-9)


@pytest.mark.parametrize("pts", [(3.0, 2.0, 0.0, 3.0), (2.0, 0.5, 3.0, 1.0, 0.0, 3.0)])
def test_bspline_profile_face(pts):
    fm = geom.FSFaceMaker()
    fm.AddPoint(0.0, 0.0)
    fm.AddPoint(3.0, 0.0)
    fm.AddBSpline(*pts)
    face = fm.GetFace()
    assert valid(face)
    assert valid(geom.RevolveZ(face))


def test_bspline_arg_check():
    fm = geom.FSFaceMaker()
    fm.AddPoint(0.0, 0.0)
    with pytest.raises(ValueError):
        fm.AddBSpline(1.0, 2.0)


def test_dia_str_and_get_dia():
    dia_list = load_table("DiaList")
    assert geom.DiaStr2Num("#6", dia_list) == pytest.approx(dia_list["#6"][0])
    assert geom.DiaStr2Num("M6", dia_list) == 6.0
    assert geom.DiaStr2Num("1in", dia_list) == pytest.approx(25.4)
    assert geom.getDia("M6", False, dia_list) == 6.0
    assert geom.getDia(5.0, True, dia_list) == 5.0
    assert geom.getDia("M6", True, dia_list, True, nut_scale=(1.1, 0.05)) == pytest.approx(6.65)
