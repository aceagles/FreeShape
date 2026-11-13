"""Extrude up to a vertex, a plane or a parallel face (I3.5)."""
import copy
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from conftest import add, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402

NOT_PARALLEL = "Up to needs a vertex, or a face or plane parallel to the sketch"


def _other(doc):
    return [p for p in doc.state.parts.values()][-1]


def _build(end, upto_of=None, h=17.0, base_z=0.0, plane_sketch=None):
    """Part A: a 10 x 10 block of height h on Top (starting at base_z via the Top plane when 0).
    Part B: a 10 x 10 sketch at x 50 extruded with `end`; `upto_of(doc, A)` makes the reference."""
    from freeshape.document import Document
    doc = Document()
    sa = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    fa = add(doc, "extrude", regions=region_refs(doc, sa), depth=h, op="new")
    a = next(iter(doc.state.parts.values()))
    sb = sketch_feature(doc, lambda s: rect_lines(s, 50, 0, 10, 10), plane_sketch)
    kw = {"upto": upto_of(doc, a)} if upto_of else {}
    if end == "upto":
        f, err = try_add(doc, "extrude", regions=region_refs(doc, sb), end="upto", op="new", depth=3, **kw)
    else:
        f, err = try_add(doc, "extrude", regions=region_refs(doc, sb), end="blind", depth=h, op="new")
    return doc, fa, f, err


def _vertex_at_top(doc, a):
    pts = K.vertex_points(a.shape)
    top = max(pts, key=lambda q: q[2])
    return F.make_vertex_ref(a.id, top, a.shape, doc.state.named(a.id))


def _top_face_plane(doc, a):
    f = next(x for x in K.faces(a.shape) if K.face_info(x)["c"][2] > 1 and abs(K.face_info(x)["n"][2]) > 0.99)
    pl = K.planar_face_plane(f)
    return {"kind": "face", "face": F.make_ref(a.id, f, "face", a.shape, doc.state.named(a.id)),
            "origin": pl.origin.tolist(), "xdir": pl.xdir.tolist()}


def _bb(doc):
    return K.bbox(_other(doc).shape)


def _names(doc):
    part = _other(doc)
    nm = doc.state.named(part.id)
    return sorted(nm.name_of(f) for f in K.faces(part.shape))


def test_upto_vertex_ends_at_17_with_blind_names():
    doc, _, f, err = _build("upto", _vertex_at_top)
    assert err is None
    lo, hi = _bb(doc)
    assert lo[2] == pytest.approx(0, abs=1e-6) and hi[2] == pytest.approx(17, abs=1e-6)
    ref, _, _, rerr = _build("blind")
    assert rerr is None
    assert _names(doc) == _names(ref)


def test_target_follows_an_edit_of_the_other_part():
    doc, fa, f, err = _build("upto", _vertex_at_top)
    assert err is None
    edited = copy.deepcopy(doc.feature(fa["id"]))
    edited["params"]["depth"] = 22
    doc.replace_feature(edited)
    assert not doc.errors.get(f["id"])
    assert _bb(doc)[1][2] == pytest.approx(22, abs=1e-6)


def test_target_below_the_sketch_extrudes_down():
    # the target block spans z -17..0 (opposite direction); the other sketch lies on Top at z=0
    def lowest(doc, a):
        low = min(K.vertex_points(a.shape), key=lambda q: q[2])
        return F.make_vertex_ref(a.id, low, a.shape, doc.state.named(a.id))
    doc, _, f, err = _build("upto", lowest, h=-17.0)
    assert err is None
    lo, hi = _bb(doc)
    assert lo[2] == pytest.approx(-17, abs=1e-6) and hi[2] == pytest.approx(0, abs=1e-6)


def test_flip_is_ignored():
    from freeshape.document import Document
    doc, _, f, _ = _build("upto", _vertex_at_top)
    g = copy.deepcopy(doc.feature(f["id"]))
    g["params"]["flip"] = True
    doc.replace_feature(g)
    assert not doc.errors.get(f["id"])
    assert _bb(doc)[1][2] == pytest.approx(17, abs=1e-6)


def test_parallel_face_works():
    doc, _, f, err = _build("upto", _top_face_plane)
    assert err is None
    assert _bb(doc)[1][2] == pytest.approx(17, abs=1e-6)


def test_std_plane_works_and_zero_is_refused():
    doc, _, f, err = _build("upto", lambda d, a: {"kind": "std", "name": "Top"})
    assert err and "zero" in err


def test_non_parallel_face_is_refused():
    def side(doc, a):
        f = next(x for x in K.faces(a.shape) if abs(K.face_info(x)["n"][0]) > 0.99)
        return {"kind": "face", "face": F.make_ref(a.id, f, "face", a.shape, doc.state.named(a.id))}
    doc, _, f, err = _build("upto", side)
    assert err and NOT_PARALLEL in err


def test_non_parallel_std_plane_is_refused():
    doc, _, f, err = _build("upto", lambda d, a: {"kind": "std", "name": "Right"})
    assert err and NOT_PARALLEL in err


def test_missing_target_is_refused():
    doc, _, f, err = _build("upto", None)
    assert err and "Select a vertex" in err


def test_second_depth_does_not_combine():
    from freeshape.document import Document
    doc, _, f, _ = _build("upto", _vertex_at_top)
    g = copy.deepcopy(doc.feature(f["id"]))
    g["params"].update(second=True, depth2=5, flip=True)
    doc.replace_feature(g)
    assert not doc.errors.get(f["id"])
    lo, hi = _bb(doc)
    assert lo[2] == pytest.approx(0, abs=1e-6) and hi[2] == pytest.approx(17, abs=1e-6)


def test_cut_up_to_a_vertex():
    from freeshape.document import Document
    doc = Document()
    sa = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 40))
    add(doc, "extrude", regions=region_refs(doc, sa), depth=30, op="new")
    base = next(iter(doc.state.parts.values()))
    sb = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 5, 5))
    add(doc, "extrude", regions=region_refs(doc, sb), depth=12, op="new")
    other = [p for p in doc.state.parts.values()][-1]
    pts = K.vertex_points(other.shape)
    top = max(pts, key=lambda q: q[2])
    ref = F.make_vertex_ref(other.id, top, other.shape, doc.state.named(other.id))
    sc = sketch_feature(doc, lambda s: rect_lines(s, 10, 10, 5, 5))
    v0 = K.volume(base.shape)
    add(doc, "extrude", regions=region_refs(doc, sc), end="upto", upto=ref, op="remove", scope=[base.id])
    cut = doc.state.parts[base.id] if base.id in doc.state.parts else next(iter(doc.state.parts.values()))
    assert K.volume(cut.shape) == pytest.approx(v0 - 5 * 5 * 12, rel=1e-6)


def test_upto_fields_round_trip():
    from freeshape.ui import dialogs as D
    ref = {"kind": "vertex", "part": "P", "c": [0, 0, 1], "name": "n"}
    p = {"end": "upto", "upto": ref, "regions": [], "faces": []}
    f = D.params_to_fields("extrude", p)
    assert f["upto_sel"] == [("vertex", ref)]
    q = {"end": "upto"}
    D.fields_to_params("extrude", f, q)
    assert q["upto"] == ref
    q2 = {"end": "blind", "upto": ref}
    D.fields_to_params("extrude", f, q2)
    assert "upto" not in q2
