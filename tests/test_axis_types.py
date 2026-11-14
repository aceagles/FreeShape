"""The axis feature (I1.2): two planes, perpendicular to a face, parallel through a point, mate connector;
detection of all eight types. Companion to test_axis_feature.py (I1.1)."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from conftest import add, block_doc, only_part, sketch_feature  # noqa: E402
from test_axis_feature import _axis, _eref, _err, _fref, _hole_doc, _on_line, _vref, _x_edge  # noqa: E402

PLANE = lambda name: {"k": "plane", "ref": {"kind": "std", "name": name}}  # noqa: E731
TOP_FACE = lambda i: i["type"] == int(K.GeomAbs_Plane) and i["n"][2] > 0.9  # noqa: E731
CYL = lambda i: i["type"] == int(K.GeomAbs_Cylinder)  # noqa: E731


def _line_gap(o1, d1, o2, d2):
    """Distance between two lines (0 when they meet or coincide)."""
    n = np.cross(d1, d2)
    if np.linalg.norm(n) < 1e-9:
        r = np.asarray(o2, float) - o1
        return float(np.linalg.norm(r - (r @ d1) * d1))
    return abs(float((np.asarray(o2, float) - o1) @ n)) / float(np.linalg.norm(n))


def _parallel(d, v):
    return abs(abs(float(np.dot(d, v))) - 1) < 1e-9


def test_two_standard_planes_give_a_world_axis_on_both_planes():
    doc = block_doc(20, 10, 5)
    ax = _axis(doc, refs=[PLANE("Top"), PLANE("Right")])
    assert _parallel(ax["d"], (0, 1, 0)) and ax["span"] is None
    o = np.array(ax["o"])
    assert abs(o[2]) < 1e-9 and abs(o[0]) < 1e-9                  # on Top (z = 0) and Right (x = 0)
    ax = _axis(doc, refs=[PLANE("Top"), PLANE("Front")])
    assert _parallel(ax["d"], (1, 0, 0)) and abs(ax["o"][2]) < 1e-9 and abs(ax["o"][1]) < 1e-9
    ax = _axis(doc, refs=[PLANE("Right"), PLANE("Front")])
    assert _parallel(ax["d"], (0, 0, 1))
    fl = _axis(doc, refs=[PLANE("Top"), PLANE("Right")], flip=True)
    assert np.allclose(fl["d"], -np.array(_axis(doc, refs=[PLANE("Top"), PLANE("Right")])["d"]))


def test_two_planes_origin_is_nearest_the_first_plane_origin_and_faces_work():
    doc = block_doc(20, 10, 5, x0=3, y0=4)
    top = _fref(doc, TOP_FACE)
    side = _fref(doc, lambda i: i["type"] == int(K.GeomAbs_Plane) and i["n"][0] > 0.9)       # x = 23
    ax = _axis(doc, refs=[top, side])
    assert _parallel(ax["d"], (0, 1, 0))
    assert abs(ax["o"][2] - 5) < 1e-9 and abs(ax["o"][0] - 23) < 1e-9
    assert abs(ax["o"][1] - 9) < 1e-6                              # the foot of the first face's centre
    mixed = _axis(doc, refs=[PLANE("Right"), top])
    assert _parallel(mixed["d"], (0, 1, 0)) and abs(mixed["o"][0]) < 1e-9 and abs(mixed["o"][2] - 5) < 1e-9


def test_parallel_planes_are_the_named_error():
    doc = block_doc(20, 10, 5)
    top = _fref(doc, TOP_FACE)
    assert _err(doc, refs=[PLANE("Top"), top]) == "Those planes are parallel"
    assert _err(doc, type="two_planes", refs=[PLANE("Top"), PLANE("Top")]) == "Those planes are parallel"
    assert _err(doc, type="two_planes", refs=[PLANE("Top")]) == "Pick two planes or planar faces"


def test_point_normal_on_a_planar_face_is_vertical_through_the_vertex():
    doc = block_doc(20, 10, 5)
    v = _vref(doc, (20, 10, 0))
    ax = _axis(doc, refs=[v, _fref(doc, TOP_FACE)])
    assert _parallel(ax["d"], (0, 0, 1)) and ax["span"] is None
    assert _on_line(ax["o"], np.array(ax["d"]), (20, 10, 0)) and _on_line(ax["o"], np.array(ax["d"]), (20, 10, 5))
    assert ax["d"][2] > 0.99                                       # the outward normal of the top face
    pl = _axis(doc, refs=[PLANE("Right"), v])                      # a plane works, in either order
    assert _parallel(pl["d"], (1, 0, 0)) and _on_line(pl["o"], np.array(pl["d"]), (20, 10, 0))
    assert np.allclose(_axis(doc, refs=[v, _fref(doc, TOP_FACE)], flip=True)["d"], -np.array(ax["d"]))


def test_point_normal_on_a_cylinder_crosses_the_centre_line():
    doc = _hole_doc()
    v = _vref(doc, (0, 0, 5))
    ax = _axis(doc, refs=[v, _fref(doc, CYL)])
    assert _on_line(ax["o"], np.array(ax["d"]), (0, 0, 5))
    assert _line_gap(np.array(ax["o"]), np.array(ax["d"]), np.array([6.0, 4, 0]), np.array([0.0, 0, 1])) < 1e-6
    assert abs(ax["d"][2]) < 1e-9                                  # radial, so horizontal here


def test_face_point_on_planar_and_curved_faces_and_without_click():
    doc = _hole_doc()
    top = _fref(doc, TOP_FACE)
    ax = _axis(doc, refs=[top], click=[12.0, 7.0, 5.0])
    assert _parallel(ax["d"], (0, 0, 1)) and ax["d"][2] > 0
    assert _on_line(ax["o"], np.array(ax["d"]), (12, 7, 5))
    off = _axis(doc, refs=[top], click=[12.0, 7.0, 9.0], type="face_point")        # a click off the face is put onto it
    assert np.allclose(off["o"], [12, 7, 5])
    cyl = _fref(doc, CYL)
    ax = _axis(doc, refs=[cyl], click=[8.0, 4.0, 2.5], type="face_point")
    assert _line_gap(np.array(ax["o"]), np.array(ax["d"]), np.array([6.0, 4, 0]), np.array([0.0, 0, 1])) < 1e-6
    assert _on_line(ax["o"], np.array(ax["d"]), (8, 4, 2.5))
    assert _err(doc, type="face_point", refs=[top]) == "Click the face where the axis should be"
    assert _err(doc, type="face_point", refs=[top], click=None) == "Click the face where the axis should be"


def test_point_parallel_to_an_edge_a_sketch_line_and_an_axis_feature():
    doc = block_doc(20, 10, 5)
    v = _vref(doc, (20, 10, 5))
    ax = _axis(doc, refs=[v, _eref(doc, _x_edge)])
    assert _parallel(ax["d"], (1, 0, 0)) and ax["span"] is None
    assert _on_line(ax["o"], np.array(ax["d"]), (20, 10, 5)) and abs(ax["o"][2] - 5) < 1e-9

    ids = {}

    def build(s):
        ids["a"], ids["b"] = s.add_point(0, 0), s.add_point(0, 8)
        ids["l"] = s.add_line(ids["a"], ids["b"])
    sid = sketch_feature(doc, build)
    sl = {"k": "sketch_line", "ref": {"kind": "sketch_line", "sketch": sid, "line": ids["l"]}}
    ax = _axis(doc, refs=[sl, v])
    assert _parallel(ax["d"], (0, 1, 0)) and _on_line(ax["o"], np.array(ax["d"]), (20, 10, 5))

    af = add(doc, "axis", refs=[_eref(doc, lambda i: i["type"] == int(K.GeomAbs_Line) and abs(abs(i["d"][2]) - 1) < 1e-6)])
    ax = _axis(doc, refs=[v, {"k": "axis", "ref": {"kind": "axis", "fid": af["id"]}}])
    assert _parallel(ax["d"], (0, 0, 1)) and _on_line(ax["o"], np.array(ax["d"]), (20, 10, 5))
    assert abs(ax["o"][0] - 20) < 1e-9
    curved = _err(_hole_doc(), type="point_parallel", refs=[_vref(_hole_doc(), (0, 0, 0)), _fref(_hole_doc(), CYL)])
    assert curved == "Pick one point and one straight line"


def _connector(doc, name="C"):
    part = only_part(doc)
    i = next(i for i, f in enumerate(K.faces(part.shape))
             if K.face_info(f).get("n") is not None and K.face_info(f)["n"][2] > 0.9)
    ref = A._ref_signature(part.shape, "face", i, doc.state.named(part.id))
    fr = A.connector_frame(part.shape, "face", i)
    return add(doc, "mate_connector", name=name, ref=ref, kind="face", x_ref=A.connector_x_ref(fr), flip=False)


def test_connector_axes_follow_the_frame_and_the_connector():
    doc = block_doc(20, 10, 5)
    c = _connector(doc)
    fr = doc.state.connectors[c["id"]]["frame"]
    z, x = np.array(fr["z"]), np.array(fr["x"])
    want = {"z": z, "x": x, "y": np.cross(z, x)}
    ref = {"k": "connector", "ref": {"fid": c["id"]}}
    for which, d in want.items():
        ax = _axis(doc, refs=[ref], connector_axis=which)
        assert np.allclose(ax["d"], d, atol=1e-9) and np.allclose(ax["o"], fr["o"]) and ax["span"] is None
        o, dd = F.resolve_axis(doc.state, {"kind": "connector", "fid": c["id"], "axis": which})
        assert np.allclose(o, fr["o"]) and np.allclose(dd, d, atol=1e-9)
    assert np.allclose(_axis(doc, refs=[ref])["d"], z)             # z is the default
    assert np.allclose(F.resolve_axis(doc.state, {"kind": "connector", "fid": c["id"]})[1], z)
    assert np.allclose(_axis(doc, refs=[ref], connector_axis="x", flip=True)["d"], -x)
    af = add(doc, "axis", refs=[ref])
    ext = next(f for f in doc.features if f["type"] == "extrude")
    ext = copy.deepcopy(ext)
    ext["params"]["depth"] = 9.0
    doc.replace_feature(ext)
    assert not doc.errors, doc.errors
    assert abs(doc.state.axes[af["id"]]["o"][2] - 9.0) < 1e-6      # the connector's face moved, so did the axis


def test_missing_connector_and_deleted_connector():
    doc = block_doc(20, 10, 5)
    assert _err(doc, type="connector", refs=[{"k": "connector", "ref": {"fid": "nope"}}]) == "Missing mate connector"
    c = _connector(doc)
    af = add(doc, "axis", refs=[{"k": "connector", "ref": {"fid": c["id"]}}])
    doc.delete_features({c["id"]})
    assert doc.errors.get(af["id"]) == "Missing mate connector"
    try:
        F.resolve_axis(doc.state, {"kind": "connector", "fid": c["id"], "axis": "z"})
        raise AssertionError("expected an error")
    except K.KernelError as e:
        assert str(e) == "Missing mate connector"


def test_auto_detects_all_eight_types_in_order():
    doc = _hole_doc()
    top, cyl = _fref(doc, TOP_FACE), _fref(doc, CYL)
    e = _eref(doc, _x_edge)
    circ = _eref(doc, lambda i: i["type"] == int(K.GeomAbs_Circle))
    v1, v2 = _vref(doc, (0, 0, 0)), _vref(doc, (20, 0, 0))
    c = {"k": "connector", "ref": {"fid": "x"}}
    af = {"k": "axis", "ref": {"kind": "axis", "fid": "y"}}
    t = lambda refs, **kw: F.axis_type({"type": "auto", "refs": refs, **kw})  # noqa: E731
    assert t([v1, v2]) == "two_points"
    assert t([PLANE("Top"), top]) == "two_planes" and t([top, PLANE("Right")]) == "two_planes"
    assert t([v1, e]) == "point_parallel" and t([af, v1]) == "point_parallel"
    assert t([v1, top]) == "point_normal" and t([cyl, v1]) == "point_normal" and t([v1, PLANE("Top")]) == "point_normal"
    assert t([c]) == "connector"
    assert t([cyl]) == "round_face"
    assert t([e]) == "edge" and t([circ]) == "edge"
    assert t([top], click=[1, 2, 5]) == "face_point"
    assert t([top]) is None and t([]) is None and t([v1]) is None and t([v1, v2, e]) is None
    assert t([cyl], click=[1, 2, 5]) == "round_face"               # a round face wins over a click
    assert t([v1, e], click=[1, 2, 5]) == "point_parallel"         # and so does point + line
    assert F.detect_axis_type(["plane", "plane"]) == "two_planes"
    # and each detected type builds through type: auto
    ax = _axis(doc, refs=[top], click=[12.0, 7.0, 5.0])
    assert _parallel(ax["d"], (0, 0, 1))
    assert "not built yet" not in (_err(doc, refs=[v1, top]) or "")
