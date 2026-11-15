"""I1.8: Rib, parallel to the sketch plane (features.run_rib).

An L bracket (base 100 x 50 x 10, wall 10 x 50 x 60 at x 0..10) with a sketch on the mid plane y = 25, whose
line runs from (40, 10) on the base top to (10, 40) on the wall face (sketch u = x, v = z).
"""
import copy
import math

import numpy as np
import pytest
from conftest import FRONT, TOP, add, only_part, rect_lines, region_refs, sketch_feature, try_add

from freeshape import features as F
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.sketch import Sketch

OUTSIDE = "The rib must be entirely within the bounds of the part"


def bracket():
    doc = Document()
    base = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 100, 50), TOP)
    add(doc, "extrude", regions=region_refs(doc, base), depth=10, op="new")
    wall = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 50), TOP)
    wall_f = add(doc, "extrude", regions=region_refs(doc, wall), depth=60, op="add")
    pl = add(doc, "plane", type="offset", refs=[], ref=FRONT, offset=-25.0)
    assert abs(doc.state.planes[pl["id"]].origin[1] - 25.0) < 1e-9
    return doc, {"kind": "plane", "fid": pl["id"]}, wall_f


def rib_sketch(doc, plane, build):
    ids = {}

    def make(s):
        ids["e"] = build(s)
    sid = sketch_feature(doc, make, plane)
    return {"kind": "sketch_chain", "sketch": sid, "entity": ids["e"]}


def line(a, b):
    def build(s):
        return s.add_line(s.add_point(*a), s.add_point(*b))
    return build


def rib(doc, ref, **kw):
    pid = next(iter(doc.state.parts))
    params = {"path": [ref], "parts": [pid], "thickness": 6.0}
    params.update(kw)
    return try_add(doc, "rib", **params)


def volume(shape):
    return K.volume(shape)


def test_rib_registered_with_defaults():
    assert F.FEATURE_LABELS["rib"] == "Rib"
    assert F.RUNNERS["rib"] is F.run_rib
    p = F.default_params("rib")
    assert p == {"path": [], "parts": [], "thickness": 2.0, "mode": "parallel", "flip": False,
                 "extend": True, "merge": True}


def test_rib_fills_the_corner():
    doc, plane, _ = bracket()
    part = only_part(doc)
    before, bb0 = volume(part.shape), K.bbox(part.shape)
    f, err = rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))))
    assert err is None
    part = only_part(doc)
    assert len(K.solids(part.shape)) == 1 and len(doc.state.parts) == 1
    assert abs(volume(part.shape) - before - 0.5 * 30 * 30 * 6) < 1e-3
    lo, hi = K.bbox(part.shape)
    assert np.allclose(lo, bb0[0], atol=1e-6) and np.allclose(hi, bb0[1], atol=1e-6)
    assert K.is_valid(part.shape)


def test_rib_short_of_both_faces_is_extended():
    doc, plane, _ = bracket()
    before = volume(only_part(doc).shape)
    k = 2 / math.sqrt(2)
    f, err = rib(doc, rib_sketch(doc, plane, line((40 - k, 10 + k), (10 + k, 40 - k))))
    assert err is None
    assert abs(volume(only_part(doc).shape) - before - 2700) < 1e-3


def test_rib_not_extended_stops_at_its_ends():
    doc, plane, _ = bracket()
    before = volume(only_part(doc).shape)
    k = 2 / math.sqrt(2)
    f, err = rib(doc, rib_sketch(doc, plane, line((40 - k, 10 + k), (10 + k, 40 - k))), extend=False)
    assert err is None
    # the triangle loses a 2 mm strip at each end; the slab is the region between the line and the corner
    assert volume(only_part(doc).shape) - before < 2700 - 1
    part = only_part(doc)
    nm = doc.state.named(part.id)
    ends = [nm.name_of(fc) for fc in K.faces(part.shape) if 21 < K.face_info(fc)["c"][1] < 29]
    assert all(ends) and any(n.startswith("l3s") or ";SKT;" in n for n in ends)
    assert sum(1 for n in ends if n.startswith("l3;")) == 2        # sides A and B


def test_rib_flip_and_a_line_leaving_the_wall_give_the_named_error():
    doc, plane, _ = bracket()
    before = volume(only_part(doc).shape)
    f, err = rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))), flip=True)
    assert err == OUTSIDE
    assert volume(only_part(doc).shape) == pytest.approx(before)
    doc2, plane2, _ = bracket()
    f, err = rib(doc2, rib_sketch(doc2, plane2, line((40, 10), (14, 58))))      # meets the wall plane above z 60
    assert err == OUTSIDE
    assert volume(only_part(doc2).shape) == pytest.approx(before)


def test_rib_large_faces_are_planes_at_the_sides():
    doc, plane, _ = bracket()
    rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))))
    part = only_part(doc)
    ys = []
    for fc in K.faces(part.shape):
        info = K.face_info(fc)
        if info.get("n") and abs(abs(info["n"][1]) - 1) < 1e-9 and 20 < info["c"][1] < 30:
            ys.append(info["c"][1])
    assert sorted(round(y, 9) for y in ys) == [22.0, 28.0]
    assert all(abs(y - 22) < 1e-9 or abs(y - 28) < 1e-9 for y in ys)


def test_rib_arc_profile():
    doc, plane, _ = bracket()
    before = volume(only_part(doc).shape)

    def arc(s):
        c = s.add_point(10, 10)
        return s.add_arc(c, s.add_point(40, 10), s.add_point(10, 40))
    f, err = rib(doc, rib_sketch(doc, plane, arc))
    assert err is None
    assert abs(volume(only_part(doc).shape) - before - math.pi * 900 / 4 * 6) < 1e-3
    assert K.is_valid(only_part(doc).shape)


def test_rib_two_line_chain():
    doc, plane, _ = bracket()
    before = volume(only_part(doc).shape)

    def chain(s):
        a, b, c = s.add_point(40, 10), s.add_point(25, 15), s.add_point(10, 40)
        s.add_line(a, b)
        return s.add_line(b, c)
    f, err = rib(doc, rib_sketch(doc, plane, chain))
    assert err is None
    # the polyline (40,10) (25,15) (10,40) cuts the corner triangle: area of polygon (10,10) (40,10) (25,15) (10,40)
    xy = [(10, 10), (40, 10), (25, 15), (10, 40)]
    area = 0.5 * abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(xy, xy[1:] + xy[:1])))
    assert abs(volume(only_part(doc).shape) - before - area * 6) < 1e-3


def test_merge_off_makes_a_new_part():
    doc, plane, _ = bracket()
    rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))), merge=False)
    assert len(doc.state.parts) == 2


def test_rib_errors():
    doc, plane, _ = bracket()
    ref = rib_sketch(doc, plane, line((40, 10), (10, 40)))
    pid = next(iter(doc.state.parts))
    assert try_add(doc, "rib", path=[], parts=[pid], thickness=6.0)[1] == "Select the sketch curves for the rib"
    assert try_add(doc, "rib", path=[ref], parts=[], thickness=6.0)[1] == "Select the part for the rib"
    assert try_add(doc, "rib", path=[ref], parts=[pid], thickness=0.0)[1] == "Rib thickness must be positive"
    assert "parallel" in try_add(doc, "rib", path=[ref], parts=[pid], thickness=6.0, mode="normal")[1]


def _named_faces(doc):
    part = only_part(doc)
    nm = doc.state.named(part.id)
    out = {}
    for fc in K.faces(part.shape):
        info = K.face_info(fc)
        if info.get("n") and abs(abs(info["n"][1]) - 1) < 1e-9 and 20 < info["c"][1] < 30:
            out[round(info["c"][1], 6)] = nm.name_of(fc)
    return out


def test_rib_follows_the_wall_and_keeps_its_names():
    doc, plane, wall_f = bracket()
    f, err = rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))))
    assert err is None
    names = _named_faces(doc)
    assert set(names) == {22.0, 28.0} and all(names.values()) and len(set(names.values())) == 2
    before = volume(only_part(doc).shape)
    w = copy.deepcopy(doc.feature(wall_f["id"]))
    w["params"]["depth"] = 80                      # the wall gets taller: the rib is rebuilt against it
    doc.replace_feature(w)
    assert doc.errors == {}
    assert _named_faces(doc) == names
    assert volume(only_part(doc).shape) == pytest.approx(before + 20 * 10 * 50)
    assert K.is_valid(only_part(doc).shape)


def test_a_fillet_on_a_rib_edge_survives_the_rebuild():
    doc, plane, wall_f = bracket()
    f, err = rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))))
    assert err is None
    part = only_part(doc)
    nm = doc.state.named(part.id)
    edge = next(e for e in K.edges(part.shape) if np.allclose(K.edge_info(e)["c"], [25, 22, 25], atol=1e-6))
    ref = F.make_ref(part.id, edge, "edge", part.shape, nm)
    assert ref.get("name")
    add(doc, "fillet", edges=[ref], radius=1.0)
    filleted = volume(only_part(doc).shape)
    w = copy.deepcopy(doc.feature(wall_f["id"]))
    w["params"]["depth"] = 80
    doc.replace_feature(w)
    assert doc.errors == {}
    assert volume(only_part(doc).shape) == pytest.approx(filleted + 20 * 10 * 50)
    assert K.is_valid(only_part(doc).shape)


def test_every_rib_face_has_a_name():
    doc, plane, _ = bracket()
    rib(doc, rib_sketch(doc, plane, line((40, 10), (10, 40))))
    part = only_part(doc)
    nm = doc.state.named(part.id)
    for fc in K.faces(part.shape):
        c = K.face_info(fc)["c"]
        if 21 < c[1] < 29 and 10 < c[0] < 41 and c[2] > 10.5 or abs(c[1] - 22) < 1e-9 or abs(c[1] - 28) < 1e-9:
            assert nm.name_of(fc), c
