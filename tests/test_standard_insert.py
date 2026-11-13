"""hole_targets: which holes a selection of faces and edges points at (headless)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir

from freeshape import kernel as K
from freeshape import assembly as A
from freeshape.features import Part
from freeshape.standard_insert import hole_targets

DIAS = [3, 4, 5, 6, 3.5, 4.5, 5.5, 6.5, 8, 2.5, 7, 3.2]     # one per hole, all different


def cyl(r, h, at=(0, 0, 0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


def hole_xy():
    return [(15 + 25 * i, 15 + 25 * j) for j in range(3) for i in range(4)]


def rounded_plate():
    """100 x 75 x 10 plate, corners rounded (r 5), 12 through holes of different diameters."""
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100, 75, 10).Shape()
    vert = [e for e in K.edges(b) if "d" in (i := K.edge_info(e)) and abs(abs(i["d"][2]) - 1) < 1e-9]
    b = K.fillet(b, vert, 5.0)
    for (x, y), d in zip(hole_xy(), DIAS):
        b = K.cut(b, cyl(d / 2, 12, (x, y, -1)))
    return b


def round_plate():
    b = cyl(30, 10)
    for x, d in ((-12, 4), (0, 5), (12, 6)):
        b = K.cut(b, cyl(d / 2, 12, (x, 0, -1)))
    return b


def counterbore():
    b = BRepPrimAPI_MakeBox(gp_Pnt(-20, -20, 0), 40, 40, 10).Shape()
    b = K.cut(b, cyl(3, 12, (0, 0, -1)))
    return K.cut(b, cyl(5, 4, (0, 0, 6)))


def arc_only():
    """A plate with a half-round notch: its rim is an arc, not a full circle."""
    b = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 20, 10).Shape()
    return K.cut(b, cyl(5, 12, (20, 0, -1)))


def washer():
    return K.cut(cyl(8, 2), cyl(4, 4, (0, 0, -1)))


SHAPES = {"washer": washer, "rounded": rounded_plate, "round": round_plate, "cb": counterbore, "arc": arc_only}


def make(*names, T=None):
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", src["part"], SHAPES[src["part"]](), (0.7, 0.7, 0.7), None)
    ids = []
    for n in names:
        ids.append(a.insert({"kind": "studio", "uid": "u", "part": n}, T=T if ids else None))
    return a, ids


def find_face(a, iid, pred):
    for i, f in enumerate(K.faces(a.parts[iid].shape)):
        if pred(K.face_info(f)):
            return i
    raise AssertionError("face not found")


def top_face(a, iid):
    return find_face(a, iid, lambda i: "n" in i and i["n"][2] > 0.99)


def cyl_face(a, iid, radius):
    return find_face(a, iid, lambda i: "r" in i and abs(i["r"] - radius) < 1e-6)


def edges_of_rim(a, iid, centre_xy, z):
    """Index of the circular edge of the first hole at xy and height z."""
    for i, e in enumerate(K.edges(a.parts[iid].shape)):
        ei = K.edge_info(e)
        if "center" in ei and np.allclose(ei["center"][:2], centre_xy) and abs(ei["center"][2] - z) < 1e-9:
            return i
    raise AssertionError("edge not found")


def pick(iid, kind, index, click=None):
    return {"inst": iid, "kind": kind, "index": index, "click": click}


def test_top_face_of_plate_gives_12_holes_and_no_outer_edges():
    a, (p,) = make("rounded")
    t = hole_targets(a, [pick(p, "face", top_face(a, p))])
    assert len(t) == 12
    assert sorted(x["diameter"] for x in t) == pytest.approx(sorted(DIAS))
    # the rounded corners and the outer boundary (lines and arcs) never appear
    es = K.edges(a.parts[p].shape)
    assert all(K.edge_info(es[x["edge"]])["r"] < 5 for x in t)
    assert all(x["inst"] == p for x in t)


def test_outer_boundary_circle_of_round_plate_is_excluded():
    a, (p,) = make("round")
    t = hole_targets(a, [pick(p, "face", top_face(a, p))])
    assert sorted(x["diameter"] for x in t) == pytest.approx([4, 5, 6])      # no 60 mm outer circle
    # ...but the same circle picked as an edge is a target
    outer = edges_of_rim(a, p, (0, 0), 10)
    t2 = hole_targets(a, [pick(p, "edge", outer)])
    assert [x["diameter"] for x in t2] == pytest.approx([60])


def test_arcs_and_non_circles_give_nothing():
    a, (p,) = make("arc")
    es = K.edges(a.parts[p].shape)
    arcs = [i for i, e in enumerate(es) if "center" in K.edge_info(e)]
    lines = [i for i, e in enumerate(es) if "d" in K.edge_info(e)]
    assert arcs and lines
    assert hole_targets(a, [pick(p, "edge", i) for i in arcs + lines]) == []
    assert hole_targets(a, [pick(p, "face", top_face(a, p))]) == []
    assert hole_targets(a, [pick(p, "face", cyl_face(a, p, 5))]) == []


def test_same_hole_by_edge_cylinder_and_top_face_is_one_target():
    a, (p,) = make("round")
    top = top_face(a, p)
    cf = cyl_face(a, p, 2.5)                  # the 5 mm hole
    hole_rim = [i for i, e in enumerate(K.edges(a.parts[p].shape))
                if abs(K.edge_info(e).get("r", 0) - 2.5) < 1e-9 and abs(K.edge_info(e)["center"][2] - 10) < 1e-9][0]
    t = hole_targets(a, [pick(p, "edge", hole_rim), pick(p, "face", cf, (2.5, 0, 9)), pick(p, "face", top)])
    assert len(t) == 3                                   # three holes on the plate in all
    assert sum(abs(x["diameter"] - 5) < 1e-9 for x in t) == 1
    assert hole_targets(a, [pick(p, "edge", hole_rim), pick(p, "face", cf, (2.5, 0, 9))]) \
        == hole_targets(a, [pick(p, "edge", hole_rim)])
    assert len(hole_targets(a, [pick(p, "edge", hole_rim)] * 3)) == 1


def test_cylindrical_face_click_chooses_rim_and_default_is_top():
    a, (p,) = make("round")
    cf = cyl_face(a, p, 2.5)
    bottom = hole_targets(a, [pick(p, "face", cf, (2.5, 0, 1.0))])
    top = hole_targets(a, [pick(p, "face", cf, (2.5, 0, 9.0))])
    none = hole_targets(a, [pick(p, "face", cf)])
    assert [t["centre"][2] for t in bottom] == pytest.approx([0])
    assert [t["centre"][2] for t in top] == pytest.approx([10])
    assert [t["centre"][2] for t in none] == pytest.approx([10])      # higher along the connector Z


def test_counterbore_floor_gives_inner_rim_only():
    a, (p,) = make("cb")
    floor = find_face(a, p, lambda i: "n" in i and i["n"][2] > 0.99 and abs(i["c"][2] - 6) < 1e-6)
    t = hole_targets(a, [pick(p, "face", floor)])
    assert [x["diameter"] for x in t] == pytest.approx([6])
    assert t[0]["centre"][2] == pytest.approx(6)
    # the top face has the 10 mm counterbore rim, not the 6 mm one
    t = hole_targets(a, [pick(p, "face", top_face(a, p))])
    assert [x["diameter"] for x in t] == pytest.approx([10])


def test_target_edge_index_is_the_connector_edge_index_and_world_centre_follows_placement():
    T = A.translation([100, -30, 7]) @ A.rot_about([1, 2, 3], 0.9)
    a, (p, q) = make("round", "rounded", T=T)
    for iid in (p, q):
        t = hole_targets(a, [pick(iid, "face", top_face(a, iid))])
        assert t
        for x in t:
            c = a.connector(iid, "edge", x["edge"])
            o = a.world_frame(c)["o"]
            assert np.allclose(o, x["centre"], atol=1e-6)
    # the placed instance really moved: the centre is not the part's own
    t = hole_targets(a, [pick(q, "face", top_face(a, q))])
    local = {(x, y) for x, y in hole_xy()}
    assert not any((round(x["centre"][0], 3), round(x["centre"][1], 3)) in local for x in t)
    # world centres are the transformed local ones
    es = K.edges(a.parts[q].shape)
    for x in t:
        lc = np.array(K.edge_info(es[x["edge"]])["center"])
        assert np.allclose(a.T(q)[:3, :3] @ lc + a.T(q)[:3, 3], x["centre"], atol=1e-9)


def test_cylinder_click_is_in_world_coordinates():
    T = A.translation([0, 0, 50]) @ A.rot_about([1, 0, 0], 0.5)
    a, (p, q) = make("round", "round", T=T)
    cf = cyl_face(a, q, 2.5)
    near_bottom = a.T(q)[:3, :3] @ np.array([2.5, 0, 1.0]) + a.T(q)[:3, 3]
    t = hole_targets(a, [pick(q, "face", cf, near_bottom)])
    lc = np.linalg.inv(a.T(q))[:3, :3] @ np.array(t[0]["centre"]) + np.linalg.inv(a.T(q))[:3, 3]
    assert lc[2] == pytest.approx(0, abs=1e-6)


def test_same_geometry_on_two_instances_is_two_targets_and_pick_shapes():
    from freeshape.ui.viewport import Pick  # the viewport's own pick type (no Qt needed to build one)
    a, (p, q) = make("round", "round", T=A.translation([200, 0, 0]))
    ft = top_face(a, p)
    t = hole_targets(a, [pick(p, "face", ft), (q, "face", ft), Pick("face", (p, ft)), {"inst": "nope", "kind": "face", "index": 0},
                         pick(p, "vertex", 0), pick(p, "face", 9999), "junk", None])
    assert len(t) == 6
    assert {x["inst"] for x in t} == {p, q}


def test_washer_top_inner_edge_gives_one_target_on_the_washer():
    a, (p, w) = make("rounded", "washer", T=A.translation([0, 0, 30]) @ A.rot_about([0, 1, 0], 0.7))
    rim = [i for i, e in enumerate(K.edges(a.parts[w].shape))
           if abs(K.edge_info(e).get("r", 0) - 4) < 1e-9 and abs(K.edge_info(e)["center"][2] - 2) < 1e-9][0]
    t = hole_targets(a, [pick(w, "edge", rim)])
    assert len(t) == 1 and t[0]["inst"] == w and t[0]["diameter"] == pytest.approx(8)
    assert np.allclose(a.world_frame(a.connector(w, "edge", t[0]["edge"]))["o"], t[0]["centre"], atol=1e-6)
    # the washer's top face: its inner rim is a target, its outer rim (outer wire) is not
    t = hole_targets(a, [pick(w, "face", top_face(a, w))])
    assert [x["diameter"] for x in t] == pytest.approx([8])
