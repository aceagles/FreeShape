"""Variable-radius fillet (I8.2): K.fillet_variable and the fillet feature's fillet_type "variable".
Route and measured ease come from scripts/spikes/op2_fillet.result.txt (I8.1)."""
import math
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402

from conftest import FREEFORM, add, edge_refs, only_part, try_add  # noqa: E402
from test_naming_blends import block, named_faces, set_depth  # noqa: E402

RESULT = os.path.join(os.path.dirname(__file__), "..", "scripts", "spikes", "op2_fillet.result.txt")


def ease():
    """{25: .., 50: .., 75: ..} from the last DECISION line of the spike result."""
    line = [ln for ln in open(RESULT).read().splitlines() if ln.startswith("DECISION fillet")][-1]
    return {int(k): float(v) for k, v in re.findall(r"ease_(\d+)pct=([\d.]+)", line)}


def front_top_edge(i):
    return (i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6 and abs(i["c"][1]) < 1e-6)


def vref(part, pos, doc=None):
    named = doc.state.named(part.id) if doc is not None else None
    return F.make_vertex_ref(part.id, pos, part.shape, named)


def named_edge_ref(doc, pred):
    part = only_part(doc)
    es = [e for e in K.edges(part.shape) if pred(K.edge_info(e))]
    assert len(es) == 1, len(es)
    return F.make_ref(part.id, es[0], "edge", part.shape, doc.state.named(part.id))


def variable(doc, r0=2.0, r1=6.0, default=3.0, edge=None):
    part = only_part(doc)
    edge = edge or edge_refs(part, front_top_edge, expect=1)
    return dict(edges=edge, radius=default, fillet_type="variable",
                vertex_radii=[{"vertex": vref(part, (0, 0, 10), doc), "radius": r0},
                              {"vertex": vref(part, (40, 0, 10), doc), "radius": r1}])


def end_offsets(doc):
    """(offset on the top face, offset on the front face) at x = 0 and at x = 40."""
    part = only_part(doc)
    out = {}
    for f in K.faces(part.shape):
        n = K.face_info(f).get("n")
        if n is None:
            continue
        pts = [p for p in K.vertex_points(f)]
        if n[2] > 0.999:
            for x in (0, 40):
                out.setdefault(x, [None, None])[0] = min(p[1] for p in pts if abs(p[0] - x) < 1e-6)
        elif n[1] < -0.999:
            for x in (0, 40):
                out.setdefault(x, [None, None])[1] = 10 - max(p[2] for p in pts if abs(p[0] - x) < 1e-6)
    return out


def top_contact_edges(part):
    out = []
    for f in K.faces(part.shape):
        n = K.face_info(f).get("n")
        if n is not None and n[2] > 0.999 and abs(K.bbox(f)[1][2] - 10) < 1e-6:
            for e in K.edges(f):
                lo, hi = K.bbox(e)
                if abs(hi[1] - 20) < 1e-6 and abs(lo[1] - 20) < 1e-6:
                    continue
                if (abs(hi[0]) < 1e-6) or (abs(lo[0] - 40) < 1e-6):
                    continue
                out.append(e)
    return out


def offset_at(doc, x):
    """Distance from the original edge point (x, 0, 10) to the line where the fillet meets the top face."""
    part = only_part(doc)
    d = BRepExtrema_DistShapeShape()
    d.LoadS1(BRepBuilderAPI_MakeVertex(gp_Pnt(x, 0, 10)).Vertex())
    best = None
    for e in top_contact_edges(part):
        d.LoadS2(e)
        d.Perform()
        if d.IsDone():
            best = d.Value() if best is None else min(best, d.Value())
    return best


def test_radii_at_the_two_ends():
    doc, _ = block()
    add(doc, "fillet", **variable(doc))
    off = end_offsets(doc)
    assert off[0] == pytest.approx([2.0, 2.0], abs=1e-3)
    assert off[40] == pytest.approx([6.0, 6.0], abs=1e-3)


def test_the_transition_follows_the_measured_ease():
    doc, _ = block()
    add(doc, "fillet", **variable(doc))
    e = ease()
    for pct in (25, 50, 75):
        assert offset_at(doc, 40 * pct / 100) == pytest.approx(e[pct], abs=0.05), pct
    assert offset_at(doc, 20) == pytest.approx(4.0, abs=0.1)


def volume_of(**kw):
    doc, _ = block()
    add(doc, "fillet", **variable(doc, **kw))
    return K.volume(only_part(doc).shape)


def test_swapping_the_radii_mirrors_the_result():
    assert volume_of(r0=2.0, r1=6.0) == pytest.approx(volume_of(r0=6.0, r1=2.0), rel=1e-6)


def test_equal_radii_match_the_constant_fillet():
    d1, _ = block()
    add(d1, "fillet", edges=edge_refs(only_part(d1), front_top_edge, expect=1), radius=3.0)
    assert volume_of(r0=3.0, r1=3.0, default=3.0) == pytest.approx(K.volume(only_part(d1).shape), rel=FREEFORM)


def corner_block():
    """The 40 x 20 x 10 block with its (40, 0) corner rounded at 8: the top edges along y = 0, the arc and
    x = 40 are one tangent chain (line, arc, line)."""
    doc, _ = block()
    part = only_part(doc)
    add(doc, "fillet", radius=8.0, edges=edge_refs(
        part, lambda i: i["type"] == int(K.GeomAbs_Line) and abs(i["c"][0] - 40) < 1e-6 and abs(i["c"][1]) < 1e-6
        and abs(i["c"][2] - 5) < 1e-6, expect=1))
    return doc


def chain_start(i):
    return i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6 and abs(i["c"][1]) < 1e-6


def chain_params(doc, default, ends, interior=()):
    part = only_part(doc)
    vr = [{"vertex": vref(part, p, doc), "radius": r} for p, r in list(ends) + list(interior)]
    return dict(edges=edge_refs(part, chain_start, expect=1), radius=default, fillet_type="variable",
                vertex_radii=vr)


def test_tangent_chain_is_one_fillet_with_the_default_inside():
    ends = [((0, 0, 10), 2.0), ((40, 20, 10), 4.0)]
    interior = [((32, 0, 10), 3.0), ((40, 8, 10), 3.0)]
    d1 = corner_block()
    add(d1, "fillet", **chain_params(d1, 3.0, ends))
    d2 = corner_block()
    add(d2, "fillet", **chain_params(d2, 3.0, ends, interior))
    d3 = corner_block()
    add(d3, "fillet", **chain_params(d3, 5.0, ends))
    s1 = only_part(d1).shape
    assert K.is_valid(s1) and len(K.solids(s1)) == 1
    assert K.volume(s1) == pytest.approx(K.volume(only_part(d2).shape), rel=1e-9)     # interior = default
    assert abs(K.volume(s1) - K.volume(only_part(d3).shape)) > 1e-3                   # and it is the default
    d4 = corner_block()
    p4 = only_part(d4)
    add(d4, "fillet", radius=3.0, edges=edge_refs(p4, chain_start, expect=1))
    assert len(K.faces(s1)) == len(K.faces(only_part(d4).shape))                     # one round, not three


def test_closed_loop_builds_or_says_why():
    doc, _ = block()
    part = only_part(doc)
    add(doc, "fillet", radius=8.0, edges=edge_refs(
        part, lambda i: i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 5) < 1e-6, expect=4))
    part = only_part(doc)
    top = edge_refs(part, lambda i: abs(i["c"][2] - 10) < 1e-6 and abs(i["c"][1]) < 1e-6
                    and i["type"] == int(K.GeomAbs_Line), expect=1)
    f, err = try_add(doc, "fillet", edges=top, radius=3.0, fillet_type="variable", vertex_radii=[
        {"vertex": vref(part, (8, 0, 10), doc), "radius": 2.0}, {"vertex": vref(part, (32, 0, 10), doc), "radius": 5.0}])
    if err:
        assert "Fillet" in err and len(err) > 20
    else:
        assert K.is_valid(only_part(doc).shape)


def test_three_edges_at_a_corner():
    doc, _ = block()
    part = only_part(doc)
    at_corner = lambda i: any(np.linalg.norm(np.array(i["c"]) - np.array(q)) < 1e-6
                              for q in [(20, 0, 10), (0, 10, 10), (0, 0, 5)])
    edges = edge_refs(part, at_corner, expect=3)
    vr = [((0, 0, 10), 3.0), ((40, 0, 10), 1.0), ((0, 20, 10), 2.0), ((0, 0, 0), 4.0)]
    f, err = try_add(doc, "fillet", edges=edges, radius=2.0, fillet_type="variable",
                     vertex_radii=[{"vertex": vref(part, p, doc), "radius": r} for p, r in vr])
    if err:
        assert re.search(r"vertex \(-?[\d.]+, -?[\d.]+, -?[\d.]+\)", err), err
    else:
        s = only_part(doc).shape
        assert K.is_valid(s) and len(K.solids(s)) == 1


def test_faces_are_named_and_radii_stay_on_their_vertices():
    doc, ex = block()
    part = only_part(doc)
    edge = [F.make_ref(part.id, e, "edge", part.shape, doc.state.named(part.id))
            for e in K.edges(part.shape) if front_top_edge(K.edge_info(e))]
    add(doc, "fillet", **variable(doc, edge=edge))
    named_faces(doc)                                  # every face has a unique name
    set_depth(doc, ex, 14.0)
    named_faces(doc)
    part = only_part(doc)
    off = {}
    for f in K.faces(part.shape):
        n = K.face_info(f).get("n")
        if n is not None and n[2] > 0.999:
            pts = K.vertex_points(f)
            off = {x: min(p[1] for p in pts if abs(p[0] - x) < 1e-6) for x in (0, 40)}
    assert off[0] == pytest.approx(2.0, abs=1e-3) and off[40] == pytest.approx(6.0, abs=1e-3)


def test_a_later_feature_still_finds_a_variable_fillet_face():
    doc, ex = block()
    add(doc, "fillet", **variable(doc))
    part = only_part(doc)
    named = doc.state.named(part.id)
    rnd = [f for f in K.faces(part.shape) if K.face_info(f).get("n") is None]
    assert len(rnd) == 1
    add(doc, "delete_face", faces=[F.make_ref(part.id, rnd[0], "face", part.shape, named)], mode="heal")
    set_depth(doc, ex, 14.0)
    assert not doc.errors, doc.errors


def test_a_lost_vertex_fails_naming_it():
    doc, _ = block()
    p = variable(doc)
    p["vertex_radii"][0]["vertex"] = dict(p["vertex_radii"][0]["vertex"], name="gone-vertex-name")
    f, err = try_add(doc, "fillet", **p)
    assert err and "gone-vertex-name" in err and "vertex 1" in err


def test_a_vertex_off_the_contour_is_ignored():
    doc, _ = block()
    p = variable(doc)
    part = only_part(doc)
    p["vertex_radii"].append({"vertex": vref(part, (40, 20, 0)), "radius": 5.0})
    add(doc, "fillet", **p)
    assert volume_of() == pytest.approx(K.volume(only_part(doc).shape), rel=1e-9)


def test_oversized_message_gives_a_percentage_that_builds():
    doc, _ = block()
    f, err = try_add(doc, "fillet", **variable(doc, r0=15.0, r1=15.0, default=15.0))
    assert err and "percent of these radii" in err, err
    pct = int(re.search(r"about (\d+) percent", err).group(1))
    doc2, _ = block()
    add(doc2, "fillet", **variable(doc2, r0=0.15 * pct, r1=0.15 * pct, default=0.15 * pct))


def test_missing_and_constant_type_match():
    d1, _ = block()
    add(d1, "fillet", edges=edge_refs(only_part(d1), front_top_edge, expect=1), radius=3.0)
    d2, _ = block()
    add(d2, "fillet", edges=edge_refs(only_part(d2), front_top_edge, expect=1), radius=3.0, fillet_type="constant")
    a, b = only_part(d1), only_part(d2)
    assert K.volume(a.shape) == pytest.approx(K.volume(b.shape), abs=1e-9)
    assert sorted(n for n in named_faces(d1)) == sorted(n for n in named_faces(d2))


def test_expression_radius():
    doc, _ = block()
    add(doc, "variable", name="w", expr="6")
    p = variable(doc, r1="#w")
    add(doc, "fillet", **p)
    assert end_offsets(doc)[40] == pytest.approx([6.0, 6.0], abs=1e-3)
