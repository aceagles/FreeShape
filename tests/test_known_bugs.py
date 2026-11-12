"""Product bugs found while writing the golden suite, pinned as strict xfails.

When one of these starts passing the bug has been fixed: drop the xfail mark (strict=True makes the
unexpected pass fail the run so it can't be forgotten).
"""
import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.TopAbs import TopAbs_REVERSED

from conftest import add, block_doc, edge_refs, both, line_along, centre_near, only_part
from freeshape import assembly as A
from freeshape import kernel as K

NORMAL_BUG = ("kernel.face_info / planar_face_plane take gp_Pln.Axis().Direction() as the normal, which is "
              "inward when the plane's gp_Ax3 is left-handed (chamfer faces from BRepFilletAPI_MakeChamfer)")


def _true_outward(face):
    ad = BRepAdaptor_Surface(face)
    u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
    v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
    n = BRepLProp_SLProps(ad, u, v, 1, 1e-7).Normal()
    n = np.array([n.X(), n.Y(), n.Z()])
    return -n if face.Orientation() == TopAbs_REVERSED else n


def _chamfered():
    doc = block_doc(40, 20, 10)
    edge = both(line_along([1, 0, 0]), centre_near([20, 0, 10]))
    add(doc, "chamfer", edges=edge_refs(only_part(doc), edge, expect=1), distance=2.0)
    part = only_part(doc)
    face = next(f for f in K.faces(part.shape)
                if (n := K.face_info(f).get("n")) is not None and abs(abs(n[1]) - 2 ** -0.5) < 1e-6)
    return part, face


def test_chamfer_face_true_normal_points_out():
    """Sanity check of the reference used below: the 45 degree face points out of the part (-y, +z)."""
    _part, face = _chamfered()
    assert np.allclose(_true_outward(face), [0, -2 ** -0.5, 2 ** -0.5], atol=1e-9)


@pytest.mark.xfail(strict=True, reason=NORMAL_BUG)
def test_face_info_normal_is_outward_on_chamfer():
    _part, face = _chamfered()
    assert np.dot(K.face_info(face)["n"], _true_outward(face)) > 0.99


@pytest.mark.xfail(strict=True, reason=NORMAL_BUG + " — a sketch on a chamfer face faces into the part")
def test_sketch_plane_on_chamfer_faces_out():
    _part, face = _chamfered()
    assert np.dot(K.planar_face_plane(face).normal, _true_outward(face)) > 0.99


@pytest.mark.xfail(strict=True, reason=NORMAL_BUG + " — a mate connector on a chamfer face points into the part")
def test_mate_connector_on_chamfer_points_out():
    part, face = _chamfered()
    idx = next(i for i, f in enumerate(K.faces(part.shape)) if f.IsSame(face))
    fr = A.connector_frame(part.shape, "face", idx)
    assert np.dot(fr["z"], _true_outward(face)) > 0.99
