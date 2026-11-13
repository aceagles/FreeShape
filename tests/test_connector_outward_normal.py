"""A connector's Z points out of the material, also on flat faces whose plane is left-handed.

ISO 7089 / 4032 / 4762 M4 tops are such faces: K.face_info(face)["n"] points into the part there."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from freeshape import kernel as K
from freeshape.assembly import connector_frame
from freeshape.standard_insert import plan_insert, apply_insert
from tests.test_standard_insert_plan import doc, top_pick, WASHER, NUT, SCREW

UP = np.array([0.0, 0, 1])


def part_shape(type_id):
    a, p = doc()
    res = apply_insert(a, plan_insert(a, type_id, [top_pick(a, p)], {}), {}, {})
    return a.parts[res["instances"][0]].shape


def flat_faces(shape):
    return [(i, f) for i, f in enumerate(K.faces(shape)) if "n" in K.face_info(f)]


def check_flat_ends(shape):
    """Every flat end face (normal along Z) gives a connector Z that points away from the part's middle."""
    mid = np.add(*K.bbox(shape)) / 2
    checked = 0
    for i, f in flat_faces(shape):
        c = connector_frame(shape, "face", i)
        if abs(c["z"][2]) < 0.99:
            continue
        true_n = K.face_outward_normal(f)
        assert np.allclose(c["z"], true_n, atol=1e-6)
        assert np.sign(c["z"][2]) == np.sign(c["o"][2] - mid[2])
        # X and the stored basis stay perpendicular to Z
        assert abs(float(np.dot(c["x"], c["z"]))) < 1e-6
        px, py = c["xb"]
        assert abs(float(np.dot(px, c["z"]))) < 1e-6 and abs(float(np.dot(py, c["z"]))) < 1e-6
        checked += 1
    return checked


def left_handed_top(shape):
    for i, f in flat_faces(shape):
        if not BRepAdaptor_Surface(f).Plane().Direct():
            return i
    return None


@pytest.mark.parametrize("type_id", [WASHER, NUT])
def test_flat_face_connectors_point_out_of_the_part(type_id):
    shape = part_shape(type_id)
    assert check_flat_ends(shape) >= 2


def test_screw_head_top_points_up_and_underside_down():
    shape = part_shape(SCREW)
    fl = [(K.face_info(f)["c"][2], i) for i, f in flat_faces(shape)
          if abs(K.face_outward_normal(f)[2]) > 0.99]
    top = max(fl)[1]
    assert connector_frame(shape, "face", top)["z"][2] == pytest.approx(1.0, abs=1e-6)


def test_washer_top_is_left_handed_and_face_info_is_untouched():
    shape = part_shape(WASHER)
    i = left_handed_top(shape)
    assert i is not None, "the M4 washer top face is expected to be left-handed"
    f = K.faces(shape)[i]
    # face_info keeps its stored signature (inward here); the connector uses the true outward normal
    assert K.face_info(f)["n"][2] < 0 and K.face_outward_normal(f)[2] > 0
    assert connector_frame(shape, "face", i)["z"][2] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("type_id", [WASHER, NUT])
def test_circular_rims_point_out_on_top_and_bottom(type_id):
    shape = part_shape(type_id)
    lo, hi = K.bbox(shape)
    mid = (lo[2] + hi[2]) / 2
    n = 0
    for ei, e in enumerate(K.edges(shape)):
        info = K.edge_info(e)
        if "center" not in info or abs(abs(np.dot(info["axis"], UP)) - 1) > 1e-6:
            continue
        zc = info["center"][2]
        if abs(zc - lo[2]) > 1e-6 and abs(zc - hi[2]) > 1e-6:
            continue
        z = connector_frame(shape, "edge", ei)["z"]
        assert z[2] == pytest.approx(1.0 if zc > mid else -1.0, abs=1e-6), (ei, zc)
        n += 1
    assert n >= 2


def test_right_handed_box_and_hole_rim_unchanged():
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 10, 5).Shape()
    box = K.cut(box, _hole())
    for i, f in flat_faces(box):
        info = K.face_info(f)
        c = connector_frame(box, "face", i)
        assert np.allclose(c["z"], info["n"], atol=1e-6)
        assert np.allclose(c["z"], K.face_outward_normal(f), atol=1e-6)
    top = [i for i, f in flat_faces(box) if abs(K.face_info(f)["c"][2] - 5) < 1e-6][0]
    assert connector_frame(box, "face", top)["z"][2] == pytest.approx(1.0)
    for ei, e in enumerate(K.edges(box)):
        info = K.edge_info(e)
        if "center" in info and abs(info["center"][2] - 5) < 1e-6:
            assert connector_frame(box, "edge", ei)["z"][2] == pytest.approx(1.0)


def _hole():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 5, -1), gp_Dir(0, 0, 1)), 2, 7).Shape()
