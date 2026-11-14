"""Named dimension ends and centre marks on section and detail views (I4.1): they follow what the view shows
(DrawingDoc.view_shape), so a cut-away element is lost, not attached elsewhere, and a hole the view does not
show has no centre mark."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402
from freeshape import naming as N  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc  # noqa: E402

from conftest import block_doc, only_part  # noqa: E402

# 40 x 20 x 10 block, holes along z (r = 3): A and E go right through; B is blind from the top (z 6..10), C
# blind from the bottom (z 0..4). A section at z = 5 looking down at the part below it keeps A, E and C.
HOLES = {"A": ((8.0, 10.0), -1.0, 12.0), "E": ((32.0, 10.0), -1.0, 12.0),
         "B": ((20.0, 16.0), 6.0, 5.0), "C": ((20.0, 4.0), -1.0, 5.0)}
RIM_Z = {"A": 0.0, "E": 0.0, "B": 10.0, "C": 0.0}


def drilled_block():
    doc = block_doc()
    part = only_part(doc)
    shape, named = part.shape, doc.state.named(part.id)
    for tag, ((x, y), z0, h) in HOLES.items():
        tool = K.cylinder((x, y, z0), (0, 0, 1), 3.0, h)
        shape, hist = K.cut(shape, tool, with_history=True)
        named = N.name_result(shape, hist, [named, N.seed(tool, "HOLE" + tag)], "HOLE" + tag, N.OP_CUT)
    return shape, named


def drawing():
    cur = {}
    cur["shape"], cur["named"] = drilled_block()
    g = DrawingDoc()
    g.source = {"kind": "studio", "part": "blk"}
    g.resolver = lambda src: cur["shape"]
    g.named_resolver = lambda src: cur["named"]
    g.rebuild = lambda: (cur.update(zip(("shape", "named"), drilled_block())), g.refresh(notify=False))
    g.refresh(notify=False)
    return g


def section(g):
    v = g.add_view((0, 0, 1), (0, 1, 0), (100, 150), kind="section", checkpoint=False)
    v["cut"] = {"p0": [0.0, 0.0, 5.0]}
    v["hidden"] = False
    g._items = None
    g._by_owner = None
    return v


def rim_end(g, hole):
    """A dimension end on the rim circle (a round edge) of a hole, as picking it in a Top view gives."""
    (x, y), _z0, _h = HOLES[hole]
    _verts, circles = g._snapdata()
    ci = next(i for i, c in enumerate(circles)
              if not c[4] and abs(c[0][0] - x) < 1e-6 and abs(c[0][1] - y) < 1e-6
              and abs(c[0][2] - RIM_Z[hole]) < 1e-6)
    c = circles[ci]
    return {"kind": "centre", "p": [float(q) for q in c[0]], "r": c[2], "i": ci, "n": len(circles)}


def dim(g, v, h1, h2):
    return g.add_annot({"type": "lin", "view": v["id"], "a": rim_end(g, h1), "b": rim_end(g, h2),
                        "orient": "h", "pos": [100, 120]})


def layers_of(g, d):
    return {it[1] for it in g.items() if it[2] == d["id"]}


def marks(g, v):
    return [it for it in g.items() if it[0] == "line" and it[1] == "CENTER" and it[2] == v["id"]]


def test_section_dimension_between_two_cut_holes_resolves_by_name():
    g = drawing()
    v = section(g)
    d = dim(g, v, "A", "E")
    assert d["a"]["name"] and d["b"]["name"]
    d["a"]["i"] = d["b"]["i"] = 0                       # a stale index must not matter to a named end
    g.rebuild()                                         # the model is rebuilt: ends are found again
    assert not d["a"].get("lost") and not d["b"].get("lost") and g.dimension_errors() == {}, g.dimension_errors()
    assert abs(g.dim_geometry(d)[2] - 24.0) < 1e-6
    assert "DIM" in layers_of(g, d) and "DIM_LOST" not in layers_of(g, d)


def test_section_dimension_to_an_edge_the_cut_removed_is_lost():
    g = drawing()
    v = section(g)
    kept, gone = dim(g, v, "A", "C"), dim(g, v, "A", "B")
    g.rebuild()
    assert not kept["b"].get("lost") and kept["id"] not in g.dimension_errors()
    assert gone["b"].get("lost") and not gone["a"].get("lost")
    assert "cut away" in g.dimension_errors()[gone["id"]] and gone["b"]["name"] in g.dimension_errors()[gone["id"]]
    assert gone["id"] not in g.recovered_dimensions()          # lost, not found by position
    assert "DIM_LOST" in layers_of(g, gone) and "DIM_LOST" not in layers_of(g, kept)   # drawn as a lost one


def test_same_dimensions_on_an_ordinary_view_are_unchanged():
    g = drawing()
    v = g.add_view(*DIRECTIONS["Top"], (100, 150), checkpoint=False)
    d1, d2 = dim(g, v, "A", "E"), dim(g, v, "A", "B")
    g.rebuild()
    assert g.dimension_errors() == {} and g.recovered_dimensions() == []
    assert not any(d[k].get("lost") for d in (d1, d2) for k in ("a", "b"))
    assert abs(g.dim_geometry(d1)[2] - 24.0) < 1e-6
    assert layers_of(g, d2) >= {"DIM"} and "DIM_LOST" not in layers_of(g, d2)
    assert len(marks(g, v)) == 8                                 # a cross for each of the four holes


def test_section_has_centre_marks_only_for_holes_it_shows():
    g = drawing()
    v = section(g)
    ls = marks(g, v)
    assert len(ls) == 6                                          # A, E and C; B is blind from the cut-away side
    s, o = g.view_scale(v), np.asarray(v["o"], float)
    centres = {(round((a[0] + b[0]) / 2, 3), round((a[1] + b[1]) / 2, 3)) for _k, _l, _o, a, b in ls}
    want = {(round(o[0] + s * x, 3), round(o[1] + s * y, 3)) for (x, y) in ((8, 10), (32, 10), (20, 4))}
    assert centres == want


def test_detail_view_draws_no_centre_mark_for_a_hole_outside_its_circle():
    g = drawing()
    top = g.add_view(*DIRECTIONS["Top"], (100, 150), checkpoint=False)
    s = g.view_scale(top)
    hole = g.to_paper(top, g.project(top, np.array([8.0, 10.0, 10.0])))
    det = g.add_detail(top, tuple(hole), 5.0 * s, (200, 150), checkpoint=False)
    g._items = None
    g._by_owner = None
    ls = marks(g, det)
    assert ls, "the hole inside the circle has its mark"
    ds = g.view_scale(det)
    near = det["o"][0] + ds * g.project(top, np.array([8.0, 10.0, 10.0]))[0]
    far = det["o"][0] + ds * g.project(top, np.array([32.0, 10.0, 10.0]))[0]
    xs = [p[0] for it in ls for p in (it[3], it[4])]
    assert all(abs(x - near) < abs(x - far) for x in xs)
