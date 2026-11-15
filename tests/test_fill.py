"""I3.5: Fill, a face across a closed boundary (kernel K.fill, runner run_fill). No window is involved: the
feature has no dialog yet (I3.6), so these tests drive the PartStudioDoc and the kernel directly.

The spike (I2.1) found filling=g1 and measured G1 errors of 0.08 to 0.5 rad on arcs with a support face. On the
card's case, a hole in a cylinder wall, the OCC default settings reach 0.0004 rad at 5 points on both builds (0.0006
over 60 points), well inside the card's 0.02."""
import itertools
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder  # noqa: E402
from OCP.GC import GC_MakeArcOfCircle  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402

from conftest import (TOP, add, block_doc, edge_refs, face_refs, normal_is, only_part,  # noqa: E402
                      rect_lines, region_refs, sketch_feature, try_add)
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402

BOX = (40.0, 20.0, 10.0)


# --------------------------------------------------------------------------- helpers

def edge_ref(doc, part, edge):
    return {"kind": "edge", "edge": F.make_ref(part.id, edge, "edge", part.shape, doc.state.named(part.id))}


def item(ref, continuity="position", faces=None):
    return {"ref": ref, "continuity": continuity, "faces": faces or []}


def area(shape):
    return sum(K.face_info(f)["area"] for f in K.faces(shape))


def line(a, b):
    return BRepBuilderAPI_MakeEdge(gp_Pnt(*a), gp_Pnt(*b)).Edge()


def arc(a, mid, b):
    return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(gp_Pnt(*a), gp_Pnt(*mid), gp_Pnt(*b)).Value()).Edge()


def dist_to_face(p, face):
    d = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex(), face)
    d.Perform()
    return d.Value()


def box_with_open_top(fill_op=None):
    """A 40 x 20 x 10 block, its top face removed with Delete face / leave open (a surface part of five faces)."""
    doc = block_doc(*BOX)
    part = only_part(doc)
    add(doc, "delete_face", faces=face_refs(part, normal_is((0, 0, 1)), expect=1), mode="open")
    part = only_part(doc)
    assert K.is_surface(part.shape)
    rim = [e for e in K.edges(part.shape)
           if (lambda i: i["type"] == int(K.GeomAbs_Line) and abs(i["p0"][2] - BOX[2]) < 1e-6
               and abs(i["p1"][2] - BOX[2]) < 1e-6)(K.edge_info(e))]
    assert len(rim) == 4
    return doc, part, [item(edge_ref(doc, part, e)) for e in rim]


def cylinder_wall_hole():
    """A solid cylinder (radius 10) with a radius 3 hole across its +y wall: (solid, the hole's rim edge, the wall face)."""
    cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 10.0, 30.0).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 15), gp_Dir(0, 1, 0)), 3.0, 15.0).Shape()
    body = K.cut(cyl, hole)
    rim = [e for e in K.edges(body) if K.discretize_edge(e)[:, 1].min() > 3]
    assert len(rim) == 1
    wall = [K._as_face(f) for f in K.edge_faces(body, rim[0]) if K.face_info(K._as_face(f)).get("r", 0) > 5]
    assert len(wall) == 1
    return body, rim[0], wall[0]


def square_boundary(n=10.0):
    c = [(0, 0, 0), (n, 0, 0), (n, n, 0), (0, n, 0)]
    return [line(c[i], c[(i + 1) % 4]) for i in range(4)]


def arc_triangle():
    """A three-sided boundary of arcs that is not planar."""
    c = [(0, 0, 0), (10, 0, 0), (5, 8, 0)]
    mids = [(5, -1, 1.5), (8.5, 4.5, -1.5), (2.5, 4.5, 1.0)]
    return [arc(c[i], mids[i], c[(i + 1) % 3]) for i in range(3)]


# --------------------------------------------------------------------------- the card's cases

def test_open_box_top_position_gives_a_plane():
    doc, part, rim = box_with_open_top()
    add(doc, "fill", edges=rim, op="new")
    surfaces = [p for p in doc.state.parts.values() if K.is_surface(p.shape)]
    assert len(surfaces) == 2                                    # the open box and the new face
    new = [p for p in surfaces if p.id != part.id][0]
    assert len(K.faces(new.shape)) == 1
    assert abs(area(new.shape) - BOX[0] * BOX[1]) < 1e-6
    assert K.face_info(K.faces(new.shape)[0])["type"] == int(K.GeomAbs_Plane)      # exact, not a plate


def test_open_box_top_add_closes_the_solid():
    doc, part, rim = box_with_open_top()
    add(doc, "fill", edges=rim, op="add")
    assert len(doc.state.parts) == 1
    solid = only_part(doc)
    assert not K.is_surface(solid.shape) and len(K.solids(solid.shape)) == 1
    assert abs(K.volume(solid.shape) - BOX[0] * BOX[1] * BOX[2]) < 1e-3


def test_open_box_top_add_with_scope_only_uses_the_scoped_parts():
    doc, part, rim = box_with_open_top()
    add(doc, "fill", edges=rim, op="add", scope=["nothing"])     # the open box is not in scope: a lone surface
    assert len(doc.state.parts) == 2
    assert all(K.is_surface(p.shape) for p in doc.state.parts.values())


def test_hole_in_cylinder_wall_position():
    body, rim, wall = cylinder_wall_hole()
    face = K.fill([rim], [None])
    assert K.is_valid(face) and len(K.faces(face)) == 1
    assert K.fill_deviation([rim], face) < 1e-3


# The card's bound. BRepFill_Filling treats G1 as a soft target (the spike saw 0.08 to 0.5 rad on arc loops), so this
# holds for a smooth wall like this one, not for every boundary; the dialog must not promise tangent continuity.
TANGENT_BOUND = 0.02


def test_hole_in_cylinder_wall_tangency():
    body, rim, wall = cylinder_wall_hole()
    face = K.fill([rim], [wall])
    assert K.is_valid(face) and len(K.faces(face)) == 1
    assert K.fill_deviation([rim], face) < 1e-3
    ang = K.fill_tangent_error(face, [rim], wall, samples=5)
    print(f"tangency angle on the cylinder wall: {ang:.4f} rad (bound {TANGENT_BOUND})")
    assert ang < TANGENT_BOUND


def test_tangency_through_a_feature_finds_the_wall():
    """No `faces` given: the support is the largest face at the edge (the wall, not the hole's bore)."""
    from freeshape.document import PartStudioDoc
    doc = PartStudioDoc()
    s0 = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, s0), depth=30, op="new")
    s1 = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 15), 3), {"kind": "std", "name": "Front"})
    add(doc, "extrude", regions=region_refs(doc, s1), depth=15, op="remove", end="blind", second=True, depth2=15)
    part = only_part(doc)
    rims = edge_refs(part, lambda i: i["c"][1] > 5 and i["type"] != int(K.GeomAbs_Line))
    add(doc, "fill", edges=[item({"kind": "edge", "edge": rims[0]}, "tangency")], op="new")
    new = [p for p in doc.state.parts.values() if K.is_surface(p.shape)]
    assert len(new) == 1
    wall = [f for f in K.faces(part.shape) if K.face_info(f).get("r", 0) > 5][0]
    rim = [e for e in K.edges(part.shape) if K.edge_info(e)["c"][1] > 5 and K.edge_info(e)["type"] != int(K.GeomAbs_Line)][0]
    assert K.fill_tangent_error(K.faces(new[0].shape)[0], [rim], wall) < TANGENT_BOUND


def test_arc_triangle_fills_and_edge_order_does_not_matter():
    edges = arc_triangle()
    ref = K.fill(edges, [None] * 3)
    assert K.is_valid(ref) and len(K.faces(ref)) == 1
    assert K.fill_deviation(edges, ref) < 1e-3
    a0 = area(ref)
    assert a0 > 0
    for perm in itertools.permutations(range(3)):
        got = K.fill([edges[i] for i in perm], [None] * 3)
        assert abs(area(got) - a0) < 1e-3 * a0, perm


def test_open_boundary_names_the_gap():
    c = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]
    three = [line(c[i], c[i + 1]) for i in range(3)]             # the side from (0,10,0) back to (0,0,0) is missing
    with pytest.raises(K.KernelError) as exc:
        K.fill(three, [None] * 3)
    msg = str(exc.value)
    assert msg.startswith(K.FILL_GAP) and "(0.000, 5.000, 0.000)" in msg, msg


def test_open_boundary_in_a_feature_is_a_red_feature_not_a_crash():
    doc, part, rim = box_with_open_top()
    f, err = try_add(doc, "fill", edges=rim[:3], op="new")
    assert err and err.startswith(K.FILL_GAP), err


def test_guide_vertex_lifts_the_face():
    edges = square_boundary()
    flat = K.fill(edges, [None] * 4)
    assert K.face_info(flat)["type"] == int(K.GeomAbs_Plane)
    face = K.fill(edges, [None] * 4, points=[(5, 5, 2)])
    assert K.face_info(face)["type"] != int(K.GeomAbs_Plane)
    assert dist_to_face((5, 5, 2), face) < 1e-3
    assert K.fill_deviation(edges, face) < 1e-3


def test_guide_vertex_through_a_feature_with_sketch_curves():
    """Sketch curves are Position (a Tangency request on one is ignored), the guide is a vertex of another part."""
    from freeshape.document import PartStudioDoc
    doc = PartStudioDoc()
    sq = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    sk = sketch_feature(doc, lambda s: rect_lines(s, 4, 4, 2, 2), TOP)
    add(doc, "extrude", regions=region_refs(doc, sk), depth=2, op="new")
    lines = [i for i, e in doc.state.sketches[sq].sketch.entities.items() if e["type"] == "line"]
    ref = {"kind": "sketch_chain", "sketch": sq, "entity": lines[0]}
    add(doc, "fill", edges=[item(ref, "tangency")], guides=[{"part": only_part(doc).id, "c": [4, 4, 2]}], op="new")
    new = [p for p in doc.state.parts.values() if K.is_surface(p.shape)]
    assert len(new) == 1
    face = K.faces(new[0].shape)[0]
    assert K.face_info(face)["type"] != int(K.GeomAbs_Plane)
    assert dist_to_face((4, 4, 2), face) < 1e-3


def test_bad_fill_edges_and_ops_are_reported():
    doc, part, rim = box_with_open_top()
    assert try_add(doc, "fill", edges=[], op="new")[1] == "Select the edges of the boundary"
    assert "Unknown continuity" in try_add(doc, "fill", edges=[item(rim[0]["ref"], "curvature")], op="new")[1]
    assert "new surface" in try_add(doc, "fill", edges=rim, op="remove")[1]


# Fill areas pinned from a measured run; both OCP builds must agree within 1e-3 relative (no exact areas in goldens).
PINNED = {"arc_triangle": 61.987913, "guide": 107.836114, "wall_position": 31.508208}


def _areas():
    body, rim, wall = cylinder_wall_hole()
    return {"arc_triangle": area(K.fill(arc_triangle(), [None] * 3)),
            "guide": area(K.fill(square_boundary(), [None] * 4, points=[(5, 5, 2)])),
            "wall_position": area(K.fill([rim], [None]))}


def test_non_planar_areas_agree_across_builds():
    got = _areas()
    print("fill areas", {k: round(v, 6) for k, v in got.items()})
    for k, v in got.items():
        assert PINNED[k] is not None and abs(v - PINNED[k]) < 1e-3 * PINNED[k], (k, v, PINNED[k])
