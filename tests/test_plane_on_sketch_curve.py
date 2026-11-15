"""A plane on a sketch curve or a sketch point: the Curve point, Point normal, Plane point and Three point
types take sketch lines, arcs, circles, splines and sketch points as well as solid edges and vertices."""
import itertools
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.dialogs import SCHEMAS, item_label, pick_to_item  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from conftest import add, region_refs, sketch_feature  # noqa: E402

C45 = 10 / np.sqrt(2)


def _plane(doc, **params):
    f = doc.make_feature("plane")
    f["params"].update(params)
    st, err = doc.preview(f, doc.insert_index())
    assert err is None, err
    return st.planes[f["id"]]


def _plane_error(doc, refs):
    f = doc.make_feature("plane")
    f["params"].update(refs=refs)
    return doc.preview(f, doc.insert_index())[1]


def _curve(sid, eid, etype):
    return {"k": "sketch_line", "ref": {"kind": "sketch_line", "sketch": sid, "line": eid, "etype": etype}}


def _point(sid, eid):
    return {"k": "sketch", "ref": {"sketch": sid, "entity": eid, "role": "point", "etype": "point"}}


def _arc_sketch(doc, split=False):
    """Arc centre (0, 0) radius 10 on Top from (10, 0) to (0, 10) with a point entity at 45 degrees. With `split`
    the arc is two arcs meeting at 45 degrees and closed by two lines to the centre, so it can be extruded."""
    ids = {}

    def build(s):
        c, a, m, b = s.add_point(0, 0), s.add_point(10, 0), s.add_point(C45, C45), s.add_point(0, 10)
        if split:
            ids["arc"] = s.add_arc(c, a, m)
            ids["arc2"] = s.add_arc(c, m, b)
            s.add_line(c, a)
            s.add_line(b, c)
        else:
            ids["arc"] = s.add_arc(c, a, b)
        ids["pt"] = s.add_point_entity(m)
        ids["pid"] = m
    sid = sketch_feature(doc, build)
    return sid, ids


def test_curve_point_on_a_sketch_arc_and_a_sketch_point():
    doc = Document()
    sid, ids = _arc_sketch(doc)
    refs = [_curve(sid, ids["arc"], "arc"), _point(sid, ids["pt"])]
    assert F.plane_type({"type": "auto", "refs": refs}) == "curve_point"
    pl = _plane(doc, refs=refs)
    assert np.allclose(pl.origin, [C45, C45, 0], atol=1e-6)
    radial = np.array([1, 1, 0]) / np.sqrt(2)
    assert abs(float(pl.normal @ radial)) < 1e-9 and abs(float(pl.normal @ [0, 0, 1])) < 1e-9


def test_same_plane_from_the_solid_edge_and_vertex():
    doc = Document()
    sid, ids = _arc_sketch(doc, split=True)
    sk_plane = _plane(doc, refs=[_curve(sid, ids["arc"], "arc"), _point(sid, ids["pt"])])
    add(doc, "extrude", regions=region_refs(doc, sid), depth=5, op="new")
    (part,) = doc.state.parts.values()
    vpos = np.array([C45, C45, 0])         # either half of the split arc ends at the vertex
    arc = next(e for e in K.edges(part.shape) if K.edge_info(e)["type"] == int(K.GeomAbs_Circle)
               and abs(K.edge_info(e)["c"][2]) < 1e-6 and np.linalg.norm(K.edge_closest(e, vpos)[0] - vpos) < 1e-6)
    eref = {"k": "edge", "ref": F.make_ref(part.id, arc, "edge", part.shape)}
    vref = {"k": "vertex", "ref": F.make_vertex_ref(part.id, (C45, C45, 0), part.shape)}
    assert F.plane_type({"type": "auto", "refs": [eref, vref]}) == "curve_point"
    solid_plane = _plane(doc, refs=[eref, vref])
    assert np.allclose(solid_plane.origin, sk_plane.origin, atol=1e-6)
    assert abs(abs(float(solid_plane.normal @ sk_plane.normal)) - 1) < 1e-9


def test_point_normal_from_a_sketch_line_and_a_sketch_point_off_it():
    doc = Document()
    ids = {}

    def build(s):
        ids["l"] = s.add_line(s.add_point(0, 0), s.add_point(10, 10))
        ids["pt"] = s.add_point_entity(s.add_point(3, 20))
    sid = sketch_feature(doc, build)
    for etype in ("line", None):                       # a reference without etype counts as a line
        c = _curve(sid, ids["l"], "line")
        if etype is None:
            del c["ref"]["etype"]
        refs = [c, _point(sid, ids["pt"])]
        assert F.plane_type({"type": "auto", "refs": refs}) == "point_normal"
        pl = _plane(doc, refs=refs)
        assert np.allclose(pl.origin, [3, 20, 0], atol=1e-9)           # through the point, not its foot on the line
        assert abs(abs(float(pl.normal @ [1, 1, 0])) / np.sqrt(2) - 1) < 1e-9


def test_a_sketch_spline_gives_the_tangent_at_the_projected_parameter():
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAPI import GeomAPI_ProjectPointOnCurve
    from OCP.gp import gp_Pnt, gp_Vec
    doc = Document()
    ids = {}

    def build(s):
        ids["s"] = s.add_spline([s.add_point(*q) for q in ((0, 0), (10, 6), (20, 0), (30, 8))])
        ids["pt"] = s.add_point_entity(s.add_point(12, 9))
    sid = sketch_feature(doc, build)
    refs = [_curve(sid, ids["s"], "spline"), _point(sid, ids["pt"])]
    assert F.plane_type({"type": "auto", "refs": refs}) == "curve_point"
    pl = _plane(doc, refs=refs)
    sk = doc.state.sketches[sid]
    (edge,) = K.make_sketch_edges(sk.plane, [c for c in sk.sketch.curves() if c["id"] == ids["s"]])
    ad = BRepAdaptor_Curve(edge)
    curve = ad.Curve().Curve()                         # the Geom curve (the edge has no location)
    proj = GeomAPI_ProjectPointOnCurve(gp_Pnt(12, 9, 0), curve)
    u = proj.LowerDistanceParameter()
    p, v = gp_Pnt(), gp_Vec()
    ad.D1(u, p, v)
    t = np.array([v.X(), v.Y(), v.Z()])
    t /= np.linalg.norm(t)
    assert abs(abs(float(pl.normal @ t)) - 1) < 1e-6
    assert np.allclose(pl.origin, [p.X(), p.Y(), p.Z()], atol=1e-6)


def test_a_sketch_circle_is_a_curve_and_an_ellipse_too():
    assert F.plane_role("sketch_line", {"etype": "circle"}) == "curve"
    assert F.plane_role("sketch_line", {"etype": "ellipse"}) == "curve"
    assert F.plane_role("sketch_line", {"etype": "spline"}) == "curve"
    assert F.plane_role("sketch_line", {"etype": "arc"}) == "curve"
    assert F.plane_role("sketch_line", {"etype": "line"}) == "line"
    assert F.plane_role("sketch_line", {}) == "line"
    assert F.plane_role("sketch", {"etype": "point"}) == "point"
    doc = Document()
    ids = {}

    def build(s):
        ids["c"] = s.add_circle(s.add_point(0, 0), 5)
        ids["pt"] = s.add_point_entity(s.add_point(0, 5))
    sid = sketch_feature(doc, build)
    pl = _plane(doc, refs=[_curve(sid, ids["c"], "circle"), _point(sid, ids["pt"])])
    assert np.allclose(pl.origin, [0, 5, 0], atol=1e-6) and abs(abs(float(pl.normal @ [1, 0, 0])) - 1) < 1e-9


def test_three_sketch_points_give_a_three_point_plane():
    doc = Document()
    ids = []

    def build(s):
        for q in ((0, 0), (10, 0), (0, 10)):
            ids.append(s.add_point_entity(s.add_point(*q)))
    sid = sketch_feature(doc, build)
    refs = [_point(sid, i) for i in ids]
    assert F.plane_type({"type": "auto", "refs": refs}) == "three_point"
    pl = _plane(doc, refs=refs)
    assert np.allclose(pl.origin, [0, 0, 0], atol=1e-9) and abs(abs(float(pl.normal[2])) - 1) < 1e-9
    for q in ((0, 0, 0), (10, 0, 0), (0, 10, 0)):
        assert abs(float((np.array(q) - pl.origin) @ pl.normal)) < 1e-9


def test_plane_point_takes_a_sketch_point():
    doc = Document()
    ids = {}

    def build(s):
        ids["pt"] = s.add_point_entity(s.add_point(4, 5))
    sid = sketch_feature(doc, build)
    refs = [{"k": "plane", "ref": {"kind": "std", "name": "Front"}}, _point(sid, ids["pt"])]
    assert F.plane_type({"type": "auto", "refs": refs}) == "plane_point"
    pl = _plane(doc, refs=refs)
    assert np.allclose(pl.origin, [4, 5, 0], atol=1e-9) and abs(abs(float(pl.normal[1])) - 1) < 1e-9


def test_moving_the_sketch_point_moves_the_plane_and_deleting_it_is_an_error():
    doc = Document()
    sid, ids = _arc_sketch(doc)
    refs = [_curve(sid, ids["arc"], "arc"), _point(sid, ids["pt"])]
    f = add(doc, "plane", refs=refs)
    assert np.allclose(doc.state.planes[f["id"]].origin, [C45, C45, 0], atol=1e-6)
    sk = doc.feature(sid)["params"]["sketch"]
    sk["points"][ids["pid"]] = [0.0, 10.0]
    doc.rebuild()
    assert not doc.errors.get(f["id"])
    pl = doc.state.planes[f["id"]]
    assert np.allclose(pl.origin, [0, 10, 0], atol=1e-6) and abs(abs(float(pl.normal[0])) - 1) < 1e-9
    del sk["entities"][ids["pt"]]                      # the sketch point is gone
    doc.rebuild()
    assert "no longer exists" in (doc.errors.get(f["id"]) or "")
    sk["entities"][ids["pt"]] = {"type": "point", "p": ids["pid"], "construction": True}
    del sk["entities"][ids["arc"]]                     # the curve is gone
    doc.rebuild()
    assert "is gone" in (doc.errors.get(f["id"]) or "")


# the roles a selection could hold before sketch geometry was added, and the answer the old rules gave
def _old_detect(roles):
    n = {r: roles.count(r) for r in ("plane", "surface", "line", "curve", "point", "?")}
    total = len(roles)
    if total == 0:
        return "offset"
    if n["?"]:
        return None
    if n["point"] == 3 and total == 3:
        return "three_point"
    if n["point"] == 1 and n["plane"] == 1 and total == 2:
        return "plane_point"
    if n["point"] == 1 and n["line"] == 1 and total == 2:
        return "point_normal"
    if n["point"] == 1 and n["curve"] == 1 and total == 2:
        return "curve_point"
    if n["plane"] == 2 and total == 2:
        return "mid_plane"
    if n["line"] == 1 and total - n["line"] - n["plane"] == 0 and n["plane"] <= 1:
        return "line_angle"
    if n["surface"] == 1 and n["point"] <= 1 and total == 1 + n["point"]:
        return "tangent"
    if n["plane"] == 1 and total == 1:
        return "offset"
    return None


def test_detect_plane_type_gives_its_old_answers_for_every_old_role_list():
    roles = ("plane", "surface", "line", "curve", "point", "?")
    for n in range(5):
        for combo in itertools.product(roles, repeat=n):
            assert F.detect_plane_type(list(combo)) == _old_detect(list(combo)), combo
    # spot checks the table pins by hand
    assert F.detect_plane_type(["point", "curve"]) == "curve_point"
    assert F.detect_plane_type(["curve"]) is None
    assert F.detect_plane_type(["point", "line"]) == "point_normal"


def test_old_item_kinds_keep_their_roles():
    assert F.plane_role("vertex", {}) == "point"
    assert F.plane_role("plane", {}) == "plane"
    assert F.plane_role("axis", {}) == "line"
    assert F.plane_role("face", {"type": int(K.GeomAbs_Plane)}) == "plane"
    assert F.plane_role("face", {"type": int(K.GeomAbs_Cylinder)}) == "surface"
    assert F.plane_role("edge", {"type": int(K.GeomAbs_Line)}) == "line"
    assert F.plane_role("edge", {"type": int(K.GeomAbs_Circle)}) == "curve"


def _vp(doc, sid):
    sk = doc.state.sketches[sid]
    return SimpleNamespace(part_shapes={}, sketches=[(sid, SimpleNamespace(sketch=sk.sketch), None)])


def test_plane_dialog_picks_store_the_entity_type_and_the_label_is_curve_point():
    assert F.PLANE_TYPE_NAMES["curve_point"] == "Curve point"
    assert dict(F.PLANE_TYPES)["curve_point"] == "Curve point"
    assert "Curve + point" not in [lbl for _, lbl in F.PLANE_TYPES]
    sel = next(f for f in SCHEMAS["plane"] if f.get("key") == "refs_sel")
    assert {"sketch_line", "sketch_point"} <= sel["want"]
    doc = Document()
    sid, ids = _arc_sketch(doc)
    vp = _vp(doc, sid)
    kind, ref = pick_to_item(vp, Pick("sketch_line", (sid, ids["arc"])), sel["want"], doc.state)
    assert kind == "sketch_line" and ref == {"kind": "sketch_line", "sketch": sid, "line": ids["arc"], "etype": "arc"}
    assert item_label(doc, (kind, ref)).endswith("arc")
    kind, ref = pick_to_item(vp, Pick("sketch_point", (sid, ids["pt"])), sel["want"], doc.state)
    assert kind == "sketch" and ref["entity"] == ids["pt"] and ref["role"] == "point"
    # the Axis tool's own sketch_line items keep their old shape (no etype)
    axis_sel = next(f for f in SCHEMAS["axis"] if f.get("key") == "refs_sel")
    _, aref = pick_to_item(vp, Pick("sketch_line", (sid, ids["arc"])), axis_sel["want"], doc.state)
    assert "etype" not in aref
