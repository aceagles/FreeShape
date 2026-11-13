"""A loft keeps the holes in its profiles (I1.11): when every profile has the same number of holes, each
matched set of hole wires is lofted and cut from the outer loft; holes are matched by position, not by
their order in the profile; with differing hole counts the loft is outer only, as before.
"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

from conftest import TOP, add, only_part, pick_edges, region_refs  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_naming_core_features import assert_all_named  # noqa: E402

REL = 1e-6


def _circle(x, y, z, r):
    e = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r)).Edge()
    return BRepBuilderAPI_MakeWire(e).Wire()


def _disc(z, r, holes=(), at=(0.0, 0.0)):
    """A flat disc of radius r at height z, with circular holes (x, y, r) added in the order given."""
    mf = BRepBuilderAPI_MakeFace(_circle(at[0], at[1], z, r))
    for x, y, hr in holes:
        mf.Add(K._as_wire(_circle(x, y, z, hr).Reversed()))
    return mf.Face()


def _close(a, b, rel=REL):
    assert abs(a - b) <= rel * max(abs(a), abs(b)), (a, b)


# --------------------------------------------------------------------------- kernel

@pytest.mark.parametrize("ruled", [True, False])
def test_ring_loft_is_outer_minus_inner(ruled):
    body = K.loft([_disc(0, 20, [(0, 0, 10)]), _disc(25, 15, [(0, 0, 5)])], ruled)
    outer = K.loft([_disc(0, 20), _disc(25, 15)], ruled)
    inner = K.loft([_disc(0, 10), _disc(25, 5)], ruled)
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    _close(K.volume(body), K.volume(outer) - K.volume(inner))
    assert len(K.faces(body)) == 4          # outer wall, bore, two ring caps


def test_two_holes_are_matched_by_position_not_by_order():
    bottom = _disc(0, 30, [(-15, 0, 6), (15, 0, 3)])
    top = _disc(25, 30, [(15, 0, 3), (-15, 0, 6)])          # same holes, listed the other way round
    body = K.loft([bottom, top])
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    expect = (K.volume(K.loft([_disc(0, 30), _disc(25, 30)]))
              - K.volume(K.loft([_disc(0, 6, at=(-15, 0)), _disc(25, 6, at=(-15, 0))]))
              - K.volume(K.loft([_disc(0, 3, at=(15, 0)), _disc(25, 3, at=(15, 0))])))
    _close(K.volume(body), expect)


def test_matching_ignores_an_offset_between_the_profiles():
    bottom = _disc(0, 30, [(-15, 0, 6), (15, 0, 3)])
    top = _disc(25, 30, [(35, 0, 3), (5, 0, 6)], at=(20, 0))    # whole profile moved 20 along x
    body = K.loft([bottom, top])
    assert len(K.solids(body)) == 1 and K.is_valid(body)
    expect = (K.volume(K.loft([_disc(0, 30), _disc(25, 30, at=(20, 0))]))
              - K.volume(K.loft([_disc(0, 6, at=(-15, 0)), _disc(25, 6, at=(5, 0))]))
              - K.volume(K.loft([_disc(0, 3, at=(15, 0)), _disc(25, 3, at=(35, 0))])))
    _close(K.volume(body), expect)


def test_differing_hole_counts_loft_the_outline_only():
    body = K.loft([_disc(0, 20, [(0, 0, 10)]), _disc(25, 15)])
    outer = K.loft([_disc(0, 20), _disc(25, 15)])
    _close(K.volume(body), K.volume(outer))
    assert len(K.faces(body)) == len(K.faces(outer))


def test_loft_history_covers_the_cut():
    profs = [_disc(0, 20, [(0, 0, 10)]), _disc(25, 15, [(0, 0, 5)])]
    body, hist = K.loft(profs, with_history=True)
    made = set()
    for f in profs:
        for e in K._sub_shapes(f, K.TopAbs_EDGE):
            for g in hist.Generated(e):
                made.add(round(K.face_info(K._as_face(g))["area"], 6))
    assert len(made) == 2                  # the outer wall and the bore, each from its profile circle
    for f in profs:
        assert hist.Generated(f).Size() >= 1     # the caps


# --------------------------------------------------------------------------- document

def _ring_sketch(doc, plane, outer_d, inner_d):
    s = Sketch()
    c0 = s.add_point(0, 0)
    a = s.add_circle(c0, outer_d / 2)
    b = s.add_circle(s.add_point(0, 0), inner_d / 2)
    s.add_constraint("fix", c0)
    s.add_constraint("diameter", a, value=outer_d)
    k = s.add_constraint("diameter", b, value=inner_d)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=plane, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"]), doc.errors
    return f["id"], k["id"]


def _ring_ref(doc, sid):
    """The ring region (the largest one; the other is the disc inside the hole)."""
    big = max(K.face_info(r)["area"] for r in doc.state.sketches[sid].regions)
    refs = region_refs(doc, sid, lambda i: abs(i["area"] - big) < 1e-6)
    assert len(refs) == 1
    return refs[0]


def _ring_doc():
    doc = Document()
    a, inner_a = _ring_sketch(doc, TOP, 40, 20)
    pl = add(doc, "plane", type="offset", refs=[], ref=TOP, offset=25.0)
    b, _ = _ring_sketch(doc, {"kind": "plane", "fid": pl["id"]}, 30, 10)
    add(doc, "loft", op="new", profiles=[{"kind": "region", "ref": _ring_ref(doc, a)},
                                         {"kind": "region", "ref": _ring_ref(doc, b)}])
    return doc, a, inner_a


def _set_diameter(doc, sid, kid, value):
    f = copy.deepcopy(doc.feature(sid))
    s = Sketch.from_dict(f["params"]["sketch"])
    next(c for c in s.constraints if c["id"] == kid)["value"] = value
    s.solve()
    f["params"]["sketch"] = s.to_dict()
    doc.replace_feature(f)
    assert not doc.errors, doc.errors


def test_ring_loft_feature_names_every_face_and_edge():
    doc, _a, _k = _ring_doc()
    part = only_part(doc)
    assert K.is_valid(part.shape) and len(K.solids(part.shape)) == 1
    named = assert_all_named(doc.state, part)
    for e in K.edges(part.shape):
        assert named.name_of(e), "an edge has no name"
    assert len(named.names_of(N.FACE)) == 4


def test_fillet_on_the_bore_rim_survives_an_inner_diameter_edit():
    doc, a, kid = _ring_doc()
    part = only_part(doc)
    edge = pick_edges(part, lambda i: abs(i.get("r", 0) - 10) < 1e-6 and abs(i["c"][2]) < 1e-6)
    assert len(edge) == 1
    rim = [F.make_ref(part.id, edge[0], "edge", part.shape, doc.state.named(part.id))]
    assert rim[0].get("name"), rim          # the fillet finds its edge by element name
    add(doc, "fillet", edges=rim, radius=1.0)
    faces_before = len(K.faces(only_part(doc).shape))
    _set_diameter(doc, a, kid, 16.0)
    part = only_part(doc)
    assert K.is_valid(part.shape)
    assert len(K.faces(part.shape)) == faces_before
    rims = [K.edge_info(e) for e in K.edges(part.shape)]
    at_floor = [i.get("r", 0) for i in rims if i.get("r") is not None and abs(i["c"][2]) < 1e-6]
    assert not any(abs(r - 8) < 1e-6 for r in at_floor), at_floor   # the new rim is rounded off
    assert any(8.5 < r < 9.5 for r in at_floor), at_floor             # where the fillet meets the floor


def test_bore_rim_name_follows_an_inner_diameter_edit():
    doc, a, kid = _ring_doc()
    part = only_part(doc)
    edge = pick_edges(part, lambda i: abs(i.get("r", 0) - 10) < 1e-6 and abs(i["c"][2]) < 1e-6)
    name = doc.state.named(part.id).name_of(edge[0])
    assert name
    _set_diameter(doc, a, kid, 16.0)
    moved = assert_all_named(doc.state, only_part(doc)).shape_of(name)
    assert moved is not None, name
    info = K.edge_info(K._as_edge(moved))
    assert abs(info["r"] - 8) < 1e-6 and abs(info["c"][2]) < 1e-6, info
