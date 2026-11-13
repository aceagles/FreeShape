"""The axis feature (I1.1): through an edge, a round face or two points; type detection; use as a revolve axis."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402
from conftest import (EXAMPLES_DIR, add, block_doc, only_part, region_refs, shape_fingerprint,  # noqa: E402
                      sketch_feature)


def _hole_doc():
    doc = block_doc(20, 10, 5)
    hid = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(6, 4)))
    add(doc, "hole", sketch=hid, standard="custom", diameter=4.0, end="through", style="simple")
    return doc


def _eref(doc, pred):
    part = only_part(doc)
    for e in K.edges(part.shape):
        if pred(K.edge_info(e)):
            return {"k": "edge", "ref": F.make_ref(part.id, e, "edge", part.shape)}
    raise AssertionError("edge not found")


def _fref(doc, pred):
    part = only_part(doc)
    for f in K.faces(part.shape):
        if pred(K.face_info(f)):
            return {"k": "face", "ref": F.make_ref(part.id, f, "face", part.shape)}
    raise AssertionError("face not found")


def _vref(doc, pos):
    part = only_part(doc)
    return {"k": "vertex", "ref": F.make_vertex_ref(part.id, pos, part.shape)}


def _axis(doc, **params):
    f = doc.make_feature("axis")
    f["params"].update(params)
    st, err = doc.preview(f, doc.insert_index())
    assert err is None, err
    return st.axes[f["id"]]


def _err(doc, **params):
    f = doc.make_feature("axis")
    f["params"].update(params)
    return doc.preview(f, doc.insert_index())[1]


def _x_edge(i):
    return i["type"] == int(K.GeomAbs_Line) and abs(abs(i["d"][0]) - 1) < 1e-6 and i["c"][1] < 1 and i["c"][2] < 1


def _on_line(o, d, pt):
    r = np.asarray(pt, float) - np.asarray(o, float)
    return np.linalg.norm(r - (r @ d) * d) < 1e-6


def test_axis_through_a_straight_edge_and_flip():
    doc = block_doc(20, 10, 5)
    e = _eref(doc, _x_edge)
    ax = _axis(doc, refs=[e])
    d = np.array(ax["d"])
    assert abs(abs(d[0]) - 1) < 1e-9 and abs(d[1]) < 1e-9 and abs(d[2]) < 1e-9
    assert _on_line(ax["o"], d, (10, 0, 0))
    assert abs(ax["span"][1] - ax["span"][0] - 20) < 1e-6
    fl = _axis(doc, refs=[e], flip=True)
    assert np.allclose(fl["d"], -d)
    assert abs(fl["span"][1] - fl["span"][0] - 20) < 1e-6


def test_axis_from_hole_face_and_hole_edge_lie_on_the_centre_line():
    doc = _hole_doc()
    face = _fref(doc, lambda i: i["type"] == int(K.GeomAbs_Cylinder))
    circ = _eref(doc, lambda i: i["type"] == int(K.GeomAbs_Circle))
    for refs in ([face], [circ]):
        ax = _axis(doc, refs=refs)
        assert abs(abs(ax["d"][2]) - 1) < 1e-9
        assert _on_line(ax["o"], np.array(ax["d"]), (6, 4, 2.5)), ax
    fa = _axis(doc, refs=[face])
    assert abs(fa["span"][1] - fa["span"][0] - 5) < 1e-6
    assert _axis(doc, refs=[circ])["span"] is None


def test_axis_through_two_points_and_sketch_point_and_same_place():
    doc = block_doc(20, 10, 5)
    a, b = _vref(doc, (0, 0, 0)), _vref(doc, (20, 10, 5))
    ax = _axis(doc, refs=[a, b])
    assert _on_line(ax["o"], np.array(ax["d"]), (0, 0, 0)) and _on_line(ax["o"], np.array(ax["d"]), (20, 10, 5))
    assert abs(ax["span"][1] - ax["span"][0] - np.linalg.norm([20, 10, 5])) < 1e-6
    sid = sketch_feature(doc, lambda s: s.add_point_entity(s.add_point(7, 3)))
    sp = {"k": "sketch", "ref": {"sketch": sid, "entity": next(iter(doc.state.sketches[sid].sketch.entities)),
                                 "role": "point", "etype": "point"}}
    ax = _axis(doc, refs=[a, sp])                                  # sketch plane Top is z = 0
    assert _on_line(ax["o"], np.array(ax["d"]), (7, 3, 0)) and _on_line(ax["o"], np.array(ax["d"]), (0, 0, 0))
    assert _err(doc, refs=[a, a]) == "Those two points are in the same place"
    assert _err(doc, refs=[a, sp, b]) == "That selection does not fit an axis type"


def test_auto_detects_each_type_and_rejects_a_plain_face():
    doc = _hole_doc()
    e = _eref(doc, _x_edge)
    cyl = _fref(doc, lambda i: i["type"] == int(K.GeomAbs_Cylinder))
    v1, v2 = _vref(doc, (0, 0, 0)), _vref(doc, (20, 0, 0))
    assert F.axis_type({"type": "auto", "refs": [e]}) == "edge"
    assert F.axis_type({"type": "auto", "refs": [cyl]}) == "round_face"
    assert F.axis_type({"type": "auto", "refs": [v1, v2]}) == "two_points"
    top = _fref(doc, lambda i: i["type"] == int(K.GeomAbs_Plane) and i["n"][2] > 0.9)
    assert F.axis_type({"type": "auto", "refs": [top]}) is None
    assert _err(doc, refs=[top]) == "That selection does not fit an axis type"
    assert "not built yet" in _err(doc, type="two_planes", refs=[])
    assert _err(doc, type="edge", refs=[top]) == "Pick a straight or circular edge"


def _revolve_volume(axis_ref):
    doc = block_doc(20, 10, 5)
    e = _eref(doc, _x_edge)
    if axis_ref == "axis":
        af = add(doc, "axis", refs=[e])
        ref = {"kind": "axis", "fid": af["id"]}
    else:
        ref = {"kind": "edge", "edge": e["ref"]}
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(10, 5), 2), {"kind": "std", "name": "Top"})
    r = add(doc, "revolve", regions=region_refs(doc, sid), axis=ref, end="full", op="new")
    return doc, r, [p for p in doc.state.parts.values()][-1]


def test_revolve_about_an_axis_matches_revolve_about_its_edge_and_missing_axis():
    doc, r, part = _revolve_volume("axis")
    _, _, ref_part = _revolve_volume("edge")
    assert abs(K.volume(part.shape) - K.volume(ref_part.shape)) < 1e-6 * K.volume(ref_part.shape)
    af = next(f for f in doc.features if f["type"] == "axis")
    doc.delete_features({af["id"]})
    assert doc.errors.get(r["id"]) == "Missing axis"
    # a suppressed axis is missing too
    doc2, r2, _ = _revolve_volume("axis")
    f = copy.deepcopy(next(f for f in doc2.features if f["type"] == "axis"))
    f["suppressed"] = True
    doc2.replace_feature(f)
    assert doc2.errors.get(r2["id"]) == "Missing axis"


def test_axis_follows_the_sketch_dimension_on_rebuild():
    doc = Document()
    s = Sketch()
    p = [ORIGIN, s.add_point(20, 0), s.add_point(20, 10), s.add_point(0, 10)]
    L = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for line, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    s.add_constraint("distance", L[0], value=20)
    dd = s.add_constraint("distance", L[1], value=10)["id"]
    assert s.solve()
    sf = doc.make_feature("sketch")
    sf["params"]["sketch"] = s.to_dict()
    doc.add_feature(sf)
    add(doc, "extrude", regions=region_refs(doc, sf["id"]), depth=5, op="new")
    far = lambda i: i["type"] == int(K.GeomAbs_Line) and abs(abs(i["d"][0]) - 1) < 1e-6 and abs(i["c"][1] - 10) < 1e-6 and i["c"][2] < 1  # noqa: E731
    af = add(doc, "axis", refs=[_eref(doc, far)])
    assert abs(doc.state.axes[af["id"]]["o"][1] - 10) < 1e-6
    f = copy.deepcopy(doc.feature(sf["id"]))
    sk = Sketch.from_dict(f["params"]["sketch"])
    for c in sk.constraints:
        if c["id"] == dd:
            c["value"] = 16.0
    assert sk.solve()
    f["params"]["sketch"] = sk.to_dict()
    doc.replace_feature(f)
    assert not doc.errors, doc.errors
    assert abs(doc.state.axes[af["id"]]["o"][1] - 16) < 1e-6


def test_axis_saves_and_loads_and_a_studio_without_one_is_unchanged(tmp_path):
    doc = block_doc(20, 10, 5)
    af = add(doc, "axis", refs=[_eref(doc, _x_edge)], flip=True)
    path = str(tmp_path / "a.fshape")
    doc.save(path)
    back = type(doc).load(path)
    back.rebuild()
    assert not back.errors, back.errors
    g = next(f for f in back.features if f["type"] == "axis")
    assert g["params"] == af["params"]
    assert back.state.axes[af["id"]] == doc.state.axes[af["id"]]
    ex = os.path.join(EXAMPLES_DIR, "bracket.fshape")
    one, two = type(doc).load(ex), type(doc).load(ex)
    for d in (one, two):
        d.rebuild()
    assert one.state.axes == {} and two.state.axes == {}
    assert {k: shape_fingerprint(p.shape) for k, p in one.state.parts.items()} == \
        {k: shape_fingerprint(p.shape) for k, p in two.state.parts.items()}


def test_cone_and_torus_faces_give_their_axis():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeTorus
    cone = K.cone((1, 2, 3), (0, 0, 1), 5, 2, 10)
    f = next(f for f in K.faces(cone) if K.face_info(f)["type"] == int(K.GeomAbs_Cone))
    info = K.face_info(f)
    assert abs(abs(info["axis"][2]) - 1) < 1e-9
    o, d, span = K.face_axis(f)
    assert _on_line(o, d, (1, 2, 8)) and abs(span[1] - span[0] - 10) < 1e-6
    tor = BRepPrimAPI_MakeTorus(10.0, 2.0).Shape()
    f = next(f for f in K.faces(tor) if K.face_info(f)["type"] == int(K.GeomAbs_Torus))
    assert abs(abs(K.face_info(f)["axis"][2]) - 1) < 1e-9
    o, d, span = K.face_axis(f)
    assert _on_line(o, d, (0, 0, 0)) and span[1] - span[0] > 3.9
