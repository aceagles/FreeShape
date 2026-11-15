"""I3.3: Enclose, a solid from surfaces that surround a volume (kernel K.enclose and K.sew_closed, runner
run_enclose). No window is involved: the feature has no dialog yet (I3.4), so these tests drive the Document
directly. The spike chose BOPAlgo_MakerVolume (enclose=makervolume), so overhanging surfaces and planes work."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import FRONT, TOP, add, sketch_feature, try_add  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402

A = 10.0


def curve_refs(sid, *entities):
    return [{"kind": "sketch_chain", "sketch": sid, "entity": e} for e in entities]


def lines_of(doc, sid):
    sk = doc.state.sketches[sid].sketch
    return [i for i, e in sk.entities.items() if e["type"] == "line"]


def surfaces(doc):
    return [p for p in doc.state.parts.values() if K.is_surface(p.shape)]


def solids_of(doc):
    return [p for p in doc.state.parts.values() if not K.is_surface(p.shape)]


def square(doc, plane, p0, p1, lo, hi):
    """A surface part: the line p0-p1 (sketch coordinates) of `plane` extruded so that its extent along the
    plane normal runs from `lo` to `hi` (the sign of the normal is found by looking at the result)."""
    sid = sketch_feature(doc, lambda s: s.add_line(s.add_point(*p0), s.add_point(*p1)), plane)
    axis = 2 if plane == TOP else 1
    for flip in (False, True):
        f, err = try_add(doc, "extrude", creation="surface", curves=curve_refs(sid, lines_of(doc, sid)[0]),
                         end="blind", depth=hi, second=True, depth2=-lo, flip=flip, op="new")
        assert not err, err
        part = surfaces(doc)[-1]
        a, b = K.bbox(part.shape)
        if abs(a[axis] - lo) < 1e-6 and abs(b[axis] - hi) < 1e-6:
            return f
        doc.delete_features([f["id"]])
    raise AssertionError("extent not found")


def cube_doc(over=0.0, doc=None, dx=0.0):
    """Six square surface parts that form the cube [0, A]^3; with over each one is (1 + 2 over) times
    wider, so the squares overhang each other."""
    doc = doc or PartStudioDoc()
    n0 = len(surfaces(doc))
    lo, hi = -over * A, A + over * A
    for y in (0, A):                                               # walls y = 0 and y = A
        square(doc, TOP, (lo + dx, y), (hi + dx, y), lo, hi)
    for x in (0, A):                                               # walls x = 0 and x = A
        square(doc, TOP, (x + dx, lo), (x + dx, hi), lo, hi)
    for z in (0, A):                                               # floor and roof, in the Front plane
        square(doc, FRONT, (lo + dx, z), (hi + dx, z), lo, hi)
    assert len(surfaces(doc)) == n0 + 6
    return doc


def enclose(doc, **kw):
    kw.setdefault("entities", [{"kind": "part", "ref": p.id} for p in surfaces(doc)])
    return try_add(doc, "enclose", **kw)


@pytest.mark.parametrize("over", [0.0, 0.1])
def test_six_squares_make_a_cube_and_the_surfaces_are_gone(over):
    doc = cube_doc(over)
    f, err = enclose(doc)
    assert not err, err
    assert not surfaces(doc) and len(doc.state.parts) == 1
    sol = next(iter(doc.state.parts.values())).shape
    assert K.is_valid(sol) and len(K.faces(sol)) == 6
    assert K.volume(sol) == pytest.approx(A ** 3, rel=1e-6)


def test_five_faces_raise_the_named_error_and_leave_the_surfaces():
    doc = cube_doc()
    ents = [{"kind": "part", "ref": p.id} for p in surfaces(doc)][:5]
    f, err = enclose(doc, entities=ents)
    assert err == K.ENCLOSE_FAILED == "The selection does not close a volume"
    assert len(surfaces(doc)) == 6 and not solids_of(doc)


def test_open_c_surface_and_three_planes_make_the_prism():
    doc = PartStudioDoc()

    def build(s):
        p = [s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 5), s.add_point(0, 5)]
        for a, b in zip(p, p[1:]):
            s.add_line(a, b)
    sid = sketch_feature(doc, build, TOP)
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, lines_of(doc, sid)[0]), depth=7, op="new")
    (c,) = surfaces(doc)
    lo, hi = K.bbox(c.shape)
    top = add(doc, "plane", type="offset", refs=[{"k": "plane", "ref": TOP}], offset=float(hi[2]))
    base = add(doc, "plane", type="offset", refs=[{"k": "plane", "ref": TOP}], offset=float(lo[2]))
    right = {"kind": "std", "name": "Right"}                       # x = 0 closes the open side of the C
    ents = [{"kind": "part", "ref": c.id}, {"kind": "plane", "ref": {"kind": "plane", "fid": top["id"]}},
            {"kind": "plane", "ref": {"kind": "plane", "fid": base["id"]}}, {"kind": "plane", "ref": right}]
    f, err = enclose(doc, entities=ents)
    assert not err, err
    (p,) = solids_of(doc)
    assert not surfaces(doc)
    assert K.is_valid(p.shape)
    assert K.volume(p.shape) == pytest.approx(10 * 5 * 7, rel=1e-6)


def test_cylinder_surface_with_two_planes_gives_pi_r2_h():
    r, h = 6.0, 9.0
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: s.add_circle(s.add_point(0, 0), r), TOP)
    sk = doc.state.sketches[sid].sketch
    circle = next(i for i, e in sk.entities.items() if e["type"] == "circle")
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, circle), depth=h, op="new")
    (tube,) = surfaces(doc)
    lo, hi = K.bbox(tube.shape)
    roof = add(doc, "plane", type="offset", refs=[{"k": "plane", "ref": TOP}], offset=float(hi[2]))
    floor = add(doc, "plane", type="offset", refs=[{"k": "plane", "ref": TOP}], offset=float(lo[2]))
    ents = [{"kind": "part", "ref": tube.id}, {"kind": "plane", "ref": {"kind": "plane", "fid": roof["id"]}},
            {"kind": "plane", "ref": {"kind": "plane", "fid": floor["id"]}}]
    f, err = enclose(doc, entities=ents)
    assert not err, err
    (p,) = solids_of(doc)
    assert K.volume(p.shape) == pytest.approx(math.pi * r * r * h, rel=1e-4)


def test_two_disjoint_closed_sets_make_two_parts():
    doc = cube_doc()
    cube_doc(doc=doc, dx=30.0)
    assert len(surfaces(doc)) == 12
    f, err = enclose(doc)
    assert not err, err
    assert not surfaces(doc) and len(solids_of(doc)) == 2
    assert [round(K.volume(p.shape)) for p in solids_of(doc)] == [1000, 1000]


def test_keep_tools_keeps_the_surface_parts():
    doc = cube_doc()
    f, err = enclose(doc, keep_tools=True)
    assert not err, err
    assert len(surfaces(doc)) == 6 and len(solids_of(doc)) == 1


def test_a_solid_part_used_as_a_wall_is_never_deleted():
    doc = cube_doc()
    roof = max(surfaces(doc), key=lambda p: K.bbox(p.shape)[0][2])
    del doc.state.parts[roof.id]                       # five walls; a block under them is the floor
    sid = sketch_feature(doc, lambda s: [s.add_line(s.add_point(*a), s.add_point(*b)) for a, b in
                                         (((0, 0), (A, 0)), ((A, 0), (A, A)), ((A, A), (0, A)), ((0, A), (0, 0)))], TOP)
    from conftest import region_refs
    add(doc, "extrude", regions=region_refs(doc, sid), depth=3.0, flip=True, op="new")
    block = solids_of(doc)[0]
    assert K.bbox(block.shape)[1][2] == pytest.approx(0.0, abs=1e-6)
    f, err = enclose(doc, entities=[{"kind": "part", "ref": p.id} for p in doc.state.parts.values()])
    assert not err, err
    assert block.id in doc.state.parts and not surfaces(doc)
    assert any(abs(K.volume(p.shape) - A ** 3) < 1e-4 for p in solids_of(doc))


def test_reversed_input_faces_still_give_a_positive_volume():
    doc = cube_doc()
    shapes = [p.shape.Reversed() for p in surfaces(doc)]
    (sol,) = K.enclose(shapes)
    assert K.volume(sol) == pytest.approx(A ** 3, rel=1e-6)


def _names(doc):
    (p,) = solids_of(doc)
    named = doc.state.named(p.id)
    assert named is not None and not named.unnamed()
    return {named.name_of(x): K.face_info(x)["c"] for x in K.faces(p.shape)}


def test_face_names_are_unchanged_after_one_surface_changes_size():
    doc = cube_doc(0.1)
    f, err = enclose(doc)
    assert not err, err
    before = _names(doc)
    assert len(before) == 6 and all(before)
    sk = [x for x in doc.features if x["type"] == "extrude"][0]      # the y = 0 wall gets wider
    f2 = dict(sk)
    f2["params"] = dict(sk["params"], depth=sk["params"]["depth"] + 0.5)
    doc.replace_feature(f2)
    assert not doc.errors, doc.errors
    after = _names(doc)
    assert set(after) == set(before)
    for k in before:
        assert np.linalg.norm(np.subtract(before[k], after[k])) < 1e-6


# --------------------------------------------------------------------------- K.sew_closed

def cube_faces():
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax3, gp_Pln
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    out = []
    for p, n, xd in (((A / 2, A / 2, 0), (0, 0, 1), (1, 0, 0)), ((A / 2, A / 2, A), (0, 0, 1), (1, 0, 0)),
                     ((A / 2, 0, A / 2), (0, 1, 0), (1, 0, 0)), ((A / 2, A, A / 2), (0, 1, 0), (1, 0, 0)),
                     ((0, A / 2, A / 2), (1, 0, 0), (0, 1, 0)), ((A, A / 2, A / 2), (1, 0, 0), (0, 1, 0))):
        pl = gp_Pln(gp_Ax3(gp_Pnt(*p), gp_Dir(*n), gp_Dir(*xd)))
        out.append(BRepBuilderAPI_MakeFace(pl, -A / 2, A / 2, -A / 2, A / 2).Face())
    return out


def test_sew_closed_makes_a_solid_from_a_closed_set():
    (sol,) = K.sew_closed(cube_faces())
    assert K.is_valid(sol) and K.volume(sol) == pytest.approx(A ** 3, rel=1e-6)


def test_sew_closed_reversed_faces_give_a_positive_volume():
    (sol,) = K.sew_closed([f.Reversed() for f in cube_faces()])
    assert K.volume(sol) == pytest.approx(A ** 3, rel=1e-6)


def test_sew_closed_refuses_an_open_set():
    assert K.sew_closed(cube_faces()[:5]) == []


def test_sew_closed_tube_and_two_flat_discs():
    from OCP.gp import gp_Pnt, gp_Dir, gp_Ax2, gp_Circ
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec
    r, h = 5.0, 8.0
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    edge = BRepBuilderAPI_MakeEdge(gp_Circ(ax, r)).Edge()
    tube = BRepPrimAPI_MakePrism(edge, gp_Vec(0, 0, h)).Shape()
    discs = []
    for z in (0.0, h):
        w = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), r)).Edge()).Wire()
        discs.append(BRepBuilderAPI_MakeFace(w).Face())
    (sol,) = K.sew_closed([tube] + discs)
    assert K.volume(sol) == pytest.approx(math.pi * r * r * h, rel=1e-4)
