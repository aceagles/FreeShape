"""Draft inside Extrude: blind, one direction (I1.4)."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from conftest import add, block_doc, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402

T5 = math.tan(math.radians(5))


def _gtype(face):
    return BRepAdaptor_Surface(face).GetType()


def _part(doc):
    return next(iter(doc.state.parts.values()))


def _faces_at(shape, z):
    return [f for f in K.faces(shape)
            if _gtype(f) == GeomAbs_Plane and abs(K.face_info(f)["c"][2] - z) < 1e-6]


def _extent(face):
    lo, hi = K.bbox(face)
    return hi - lo


def _body(sketch, op="new", depth=10.0, **kw):
    doc = Document()
    sid = sketch_feature(doc, sketch)
    f, err = try_add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op=op, **kw)
    return doc, f, err


def _square(s):
    rect_lines(s, -10, -10, 20, 20)


def _circle(s):
    s.add_circle(s.add_point(0, 0), 10)


def test_square_draft_five_degrees():
    doc, _, err = _body(_square, draft=True, draft_angle=5.0)
    assert not err, err
    sh = _part(doc).shape
    far, near = _faces_at(sh, 10.0), _faces_at(sh, 0.0)
    assert len(far) == 1 and len(near) == 1
    assert _extent(far[0])[:2] == pytest.approx([18.2503, 18.2503], abs=1e-4 + 2e-6)
    assert _extent(near[0])[:2] == pytest.approx([20, 20], abs=1e-4)
    assert K.volume(sh) == pytest.approx(3660.3, abs=0.1)
    lo, hi = K.bbox(sh)
    assert (hi - lo)[2] == pytest.approx(10, abs=1e-5)
    sides = [f for f in K.faces(sh) if not any(f.IsSame(g) for g in far + near)]
    assert len(sides) == 4 and all(_gtype(f) == GeomAbs_Plane for f in sides)
    assert K.is_valid(sh)


def test_draft_flip_makes_the_far_end_larger():
    doc, _, err = _body(_square, draft=True, draft_angle=5.0, draft_flip=True)
    assert not err, err
    far = _faces_at(_part(doc).shape, 10.0)
    assert _extent(far[0])[:2] == pytest.approx([21.7497, 21.7497], abs=1e-4 + 2e-6)


def test_circle_draft_is_a_cone():
    doc, _, err = _body(_circle, draft=True, draft_angle=5.0)
    assert not err, err
    sh = _part(doc).shape
    side = [f for f in K.faces(sh) if _gtype(f) == GeomAbs_Cone]
    assert len(side) == 1
    far = _faces_at(sh, 10.0)[0]
    assert _extent(far)[0] / 2 == pytest.approx(9.1251, abs=1e-3)


def test_inner_hole_grows_where_the_outside_shrinks():
    def sk(s):
        rect_lines(s, -20, -20, 40, 40)
        s.add_circle(s.add_point(0, 0), 5)
    doc = Document()
    sid = sketch_feature(doc, sk)
    ring = region_refs(doc, sid, lambda i: abs(i["area"] - (1600 - math.pi * 25)) < 1)
    assert len(ring) == 1
    f, err = try_add(doc, "extrude", regions=ring, depth=10.0, op="new", draft=True, draft_angle=5.0)
    assert not err, err
    sh = _part(doc).shape
    cones = [x for x in K.faces(sh) if _gtype(x) == GeomAbs_Cone]
    assert len(cones) == 1
    far = _faces_at(sh, 10.0)[0]
    lo, hi = K.bbox(far)
    assert (hi - lo)[0] == pytest.approx(40 - 2 * 10 * T5, abs=1e-4 + 2e-6)
    r_far = [e for e in K.edges(far) if BRepAdaptor_CurveType(e) == "circle"]
    assert r_far
    assert BRepRadius(r_far[0]) == pytest.approx(5 + 10 * T5, abs=1e-4)


def BRepAdaptor_CurveType(e):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    return "circle" if BRepAdaptor_Curve(e).GetType() == GeomAbs_Circle else "other"


def BRepRadius(e):
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    return BRepAdaptor_Curve(e).Circle().Radius()


def test_rounded_rectangle():
    def sk(s):
        w, h, r = 40.0, 20.0, 4.0
        p = lambda x, y: s.add_point(x, y)  # noqa: E731
        a1, a2, b1, b2 = p(r, 0), p(w - r, 0), p(w, r), p(w, h - r)
        c1, c2, d1, d2 = p(w - r, h), p(r, h), p(0, h - r), p(0, r)
        s.add_line(a1, a2)
        s.add_arc(p(w - r, r), a2, b1)
        s.add_line(b1, b2)
        s.add_arc(p(w - r, h - r), b2, c1)
        s.add_line(c1, c2)
        s.add_arc(p(r, h - r), c2, d1)
        s.add_line(d1, d2)
        s.add_arc(p(r, r), d2, a1)
    doc, _, err = _body(sk, draft=True, draft_angle=3.0)
    assert not err, err
    sh = _part(doc).shape
    types = [_gtype(f) for f in K.faces(sh) if _gtype(f) in (GeomAbs_Cone, GeomAbs_Cylinder) or
             (_gtype(f) == GeomAbs_Plane and abs(K.face_info(f)["c"][2] - 5) < 4.9)]
    assert types.count(GeomAbs_Cone) == 4 and GeomAbs_Cylinder not in types
    assert len(K.solids(sh)) == 1 and K.is_valid(sh)


def test_remove_with_draft_removes_the_drafted_volume():
    tool_doc, _, err = _body(_square, draft=True, draft_angle=5.0)
    assert not err, err
    tool_vol = K.volume(_part(tool_doc).shape)
    doc = block_doc(60, 60, 30, -30, -30)
    before = K.volume(_part(doc).shape)
    sid = sketch_feature(doc, _square)
    # the cut goes up into the block from the sketch plane (the block's base)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="remove", draft=True, draft_angle=5.0,
        flip=True)
    assert before - K.volume(_part(doc).shape) == pytest.approx(tool_vol, abs=1e-6)


def test_too_deep_a_draft_fails():
    doc, f, err = _body(lambda s: rect_lines(s, -10, -10, 20, 20), depth=100.0, draft=True, draft_angle=10.0)
    assert err and ("Draft" in err or "draft" in err), err


def test_spline_profile_names_the_limit():
    def sk(s):
        pts = [s.add_point(0, 0), s.add_point(10, 5), s.add_point(20, 0), s.add_point(10, -8)]
        s.add_spline(pts, closed=True)
    doc, f, err = _body(sk, draft=True, draft_angle=5.0)
    assert err and "straight and circular profile edges" in err, err


def test_angle_limits():
    for ang in (0, -3, 90, 120):
        doc, f, err = _body(_square, draft=True, draft_angle=ang)
        assert err and "less than 90" in err, (ang, err)


def _names(doc):
    part = _part(doc)
    nm = doc.state.named(part.id)
    return sorted(nm.name_of(f) or "" for f in K.faces(part.shape))


def test_draft_off_equals_no_key_and_names_match_draft_on():
    plain, _, e0 = _body(_square)
    off, _, e1 = _body(_square, draft=False, draft_angle=5.0, draft_flip=True)
    on, _, e2 = _body(_square, draft=True, draft_angle=5.0)
    assert not (e0 or e1 or e2), (e0, e1, e2)
    assert K.volume(_part(plain).shape) == pytest.approx(K.volume(_part(off).shape), abs=1e-9)
    assert _names(plain) == _names(off)
    assert _names(on) == _names(plain)
    assert all(_names(on))


def test_command_search_finds_extrude_by_draft_and_taper():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    from freeshape.ui.palette import search_commands
    win = MainWindow()
    cmds = win._palette_commands()
    for word in ("taper", "draft"):
        assert "Extrude" in [c.name for c in search_commands(cmds, word)]


def test_schema_shows_draft_for_blind_without_second_direction():
    from freeshape.ui.dialogs import SCHEMAS
    spec = {s["key"]: s for s in SCHEMAS["extrude"]}
    for k in ("draft", "draft_angle", "draft_flip"):
        assert k in spec
    vis = lambda k, p: spec[k]["visible"](p)  # noqa: E731
    assert vis("draft", {"end": "blind"})
    assert vis("draft", {"end": "blind", "second": True})      # I1.5: two-direction ends draft too
    assert vis("draft", {"end": "symmetric"})
    assert not vis("draft", {"end": "through"})
    assert not vis("draft_angle", {"end": "blind"})
    assert vis("draft_angle", {"end": "blind", "draft": True})
    assert vis("draft_flip", {"end": "blind", "draft": True})
