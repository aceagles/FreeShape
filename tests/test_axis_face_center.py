"""The axis feature: a flat face alone gives an axis through the face centre along its outward normal, where a
mate connector on that face sits (the end face of a shaft or a washer: the axis through its middle)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from conftest import add, block_doc, only_part, region_refs, sketch_feature  # noqa: E402
from test_axis_feature import _axis, _eref, _err, _fref, _vref  # noqa: E402

CX, CY, R, BORE, H = 7.0, 3.0, 5.0, 2.0, 8.0
PLANAR = lambda i: i["type"] == int(K.GeomAbs_Plane)  # noqa: E731
TOP = lambda i: PLANAR(i) and i["n"][2] > 0.9  # noqa: E731
BOTTOM = lambda i: PLANAR(i) and i["n"][2] < -0.9  # noqa: E731


def _round_doc(bore=None):
    """A cylinder of radius R and height H standing on Top at (CX, CY); with `bore`, a tube (annular end faces)."""
    doc = Document()

    def build(s):
        c = s.add_point(CX, CY)
        s.add_circle(c, R)
        if bore:
            s.add_circle(c, bore)

    sid = sketch_feature(doc, build)
    ring = (lambda i: i["area"] > math.pi * bore * bore * 1.5) if bore else None
    add(doc, "extrude", regions=region_refs(doc, sid, ring), depth=H, op="new")
    return doc


def _named(doc, pred):
    """A face reference as the dialog stores it: with the face's element name."""
    part = only_part(doc)
    face = next(f for f in K.faces(part.shape) if pred(K.face_info(f)))
    return {"k": "face", "ref": F.make_ref(part.id, face, "face", part.shape, doc.state.named(part.id))}


def test_disc_end_face_gives_the_axis_through_its_centre_along_the_outward_normal():
    doc = _round_doc()
    top = _named(doc, TOP)
    assert F.axis_type({"type": "auto", "refs": [top]}) == "face_center"
    ax = _axis(doc, refs=[top])
    assert np.allclose(ax["o"], [CX, CY, H], atol=1e-6) and np.allclose(ax["d"], [0, 0, 1], atol=1e-9)
    assert ax["span"] is None
    bottom = _axis(doc, refs=[_named(doc, BOTTOM)])
    assert np.allclose(bottom["o"], [CX, CY, 0], atol=1e-6) and np.allclose(bottom["d"], [0, 0, -1], atol=1e-9)
    flipped = _axis(doc, refs=[top], flip=True)
    assert np.allclose(flipped["o"], ax["o"]) and np.allclose(flipped["d"], [0, 0, -1], atol=1e-9)


def test_annular_end_face_gives_the_axis_through_the_hole_centre():
    doc = _round_doc(bore=BORE)
    info = K.face_info(next(f for f in K.faces(only_part(doc).shape) if TOP(K.face_info(f))))
    assert abs(info["area"] - math.pi * (R * R - BORE * BORE)) < 1e-6          # the face is the ring
    ax = _axis(doc, refs=[_named(doc, TOP)])
    assert np.allclose(ax["o"], [CX, CY, H], atol=1e-6) and np.allclose(ax["d"], [0, 0, 1], atol=1e-9)


def test_it_is_the_frame_a_mate_connector_on_that_face_has():
    doc = _round_doc(bore=BORE)
    part = only_part(doc)
    index = next(i for i, f in enumerate(K.faces(part.shape)) if TOP(K.face_info(f)))
    fr = A.connector_frame(part.shape, "face", index)
    ax = _axis(doc, refs=[_named(doc, TOP)])
    assert np.allclose(ax["o"], fr["o"], atol=1e-9) and np.allclose(ax["d"], fr["z"], atol=1e-9)


def test_any_flat_face_and_a_plane_alone():
    doc = block_doc(20, 10, 5, x0=3, y0=4)
    ax = _axis(doc, refs=[_named(doc, TOP)])
    assert np.allclose(ax["o"], [13, 9, 5], atol=1e-6) and np.allclose(ax["d"], [0, 0, 1], atol=1e-9)
    side = _axis(doc, refs=[_named(doc, lambda i: PLANAR(i) and i["n"][0] < -0.9)])
    assert np.allclose(side["o"], [3, 9, 2.5], atol=1e-6) and np.allclose(side["d"], [-1, 0, 0], atol=1e-9)
    std = _axis(doc, refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}])
    assert np.allclose(std["o"], [0, 0, 0], atol=1e-9) and abs(abs(std["d"][2]) - 1) < 1e-9


def test_detection_leaves_the_other_types_alone():
    doc = _round_doc()
    top, cyl = _fref(doc, TOP), _fref(doc, lambda i: i["type"] == int(K.GeomAbs_Cylinder))
    circ = _eref(doc, lambda i: i["type"] == int(K.GeomAbs_Circle) and i["c"][2] > H - 1e-6)
    v = _vref(doc, (CX + R, CY, H))
    plane = {"k": "plane", "ref": {"kind": "std", "name": "Top"}}
    t = lambda refs, **kw: F.axis_type({"type": "auto", "refs": refs, **kw})  # noqa: E731
    assert t([top]) == "face_center" and t([top], click=None) == "face_center" and t([plane]) == "face_center"
    assert t([top], click=[1, 2, H]) == "face_point"               # a stored click: the axis stays at the click
    assert t([cyl]) == "round_face" and t([cyl], click=[1, 2, 3]) == "round_face"
    assert t([circ]) == "edge"
    assert t([top, plane]) == "two_planes" and t([v, top]) == "point_normal"
    assert t([]) is None and t([v]) is None and t([top, plane, v]) is None
    assert F.detect_axis_type(["surface"]) is None and F.detect_axis_type(["surface"], True) == "face_point"
    # a circular edge alone was already the axis through its centre; it agrees with the face it bounds
    edge, face = _axis(doc, refs=[circ]), _axis(doc, refs=[top])
    assert np.allclose(edge["o"], face["o"], atol=1e-6) and abs(abs(float(np.dot(edge["d"], face["d"]))) - 1) < 1e-9


def test_a_file_that_stored_a_click_keeps_its_axis_at_the_click():
    doc = _round_doc()
    ax = _axis(doc, type="auto", refs=[_named(doc, TOP)], click=[CX + 2.0, CY - 1.0, H])
    assert np.allclose(ax["o"], [CX + 2.0, CY - 1.0, H], atol=1e-6)
    explicit = _axis(doc, type="face_center", refs=[_named(doc, TOP)], click=[CX + 2.0, CY - 1.0, H])
    assert np.allclose(explicit["o"], [CX, CY, H], atol=1e-6)        # the chosen type wins over a left-over click


def test_the_face_is_stored_by_name_and_the_axis_follows_an_edit():
    doc = _round_doc()
    top = _named(doc, TOP)
    assert top["ref"].get("name")
    f = add(doc, "axis", refs=[top])
    assert f["params"]["refs"][0]["ref"]["name"] == top["ref"]["name"]
    assert np.allclose(doc.state.axes[f["id"]]["o"], [CX, CY, H], atol=1e-6)
    ext = next(x for x in doc.features if x["type"] == "extrude")
    ext["params"]["depth"] = H + 6
    doc.rebuild()
    assert not doc.errors.get(f["id"])
    assert np.allclose(doc.state.axes[f["id"]]["o"], [CX, CY, H + 6], atol=1e-6)
    assert f["id"] not in getattr(doc.state, "recovered", ())        # found by name, not by nearest match


def test_a_lost_name_is_an_error_not_a_nearest_face():
    doc = _round_doc()
    top = _named(doc, TOP)
    top["ref"]["name"] = "no-such-face"
    err = _err(doc, refs=[top])
    assert err and "fit an axis type" not in err


def test_face_center_rejects_what_has_no_flat_face():
    doc = _round_doc()
    cyl = _fref(doc, lambda i: i["type"] == int(K.GeomAbs_Cylinder))
    assert _err(doc, type="face_center", refs=[cyl]) == "Pick one flat face or plane"
    assert _err(doc, type="face_center", refs=[]) == "Pick one flat face or plane"
    assert F.AXIS_TYPE_NAMES["face_center"] == "Perpendicular to face at centre"
