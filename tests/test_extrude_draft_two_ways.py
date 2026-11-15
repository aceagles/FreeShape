"""Draft with Symmetric and with a second blind direction (I1.5)."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.GeomAbs import GeomAbs_Plane  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from conftest import edge_refs, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402

T5 = math.tan(math.radians(5))


def _square(s):
    rect_lines(s, -10, -10, 20, 20)


def _part(doc):
    return next(iter(doc.state.parts.values()))


def _faces_at(shape, z):
    return [f for f in K.faces(shape)
            if BRepAdaptor_Surface(f).GetType() == GeomAbs_Plane and abs(K.face_info(f)["c"][2] - z) < 1e-6]


def _side(face):
    lo, hi = K.bbox(face)
    return (hi - lo)[:2]


def _body(depth=10.0, **kw):
    doc = Document()
    sid = sketch_feature(doc, _square)
    f, err = try_add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="new", **kw)
    return doc, f, err


def _names(doc):
    part = _part(doc)
    nm = doc.state.named(part.id)
    return sorted(nm.name_of(f) or "" for f in K.faces(part.shape))


def test_symmetric_draft_both_ends_shrink_and_plane_section_is_full():
    doc, _, err = _body(20.0, end="symmetric", draft=True, draft_angle=5.0)
    assert not err, err
    sh = _part(doc).shape
    assert len(K.solids(sh)) == 1 and K.is_valid(sh)
    want = 20 - 2 * 10 * T5
    for z in (10.0, -10.0):
        (face,) = _faces_at(sh, z)
        assert _side(face) == pytest.approx([want, want], abs=1e-4)
    # the section at the sketch plane keeps the sketch size
    mid = K.section_cut(sh, (0, 0, 1e-6), (0, 0, 1))
    lo, hi = K.bbox(mid)
    assert (hi - lo)[:2] == pytest.approx([20, 20], abs=1e-4)
    a1, a2 = 20.0 ** 2, want ** 2
    assert K.volume(sh) == pytest.approx(2 * 10 / 3 * (a1 + a2 + math.sqrt(a1 * a2)), abs=0.01)


def test_blind_with_second_depth_drafts_both_ends():
    doc, _, err = _body(10.0, second=True, depth2=4.0, draft=True, draft_angle=5.0)
    assert not err, err
    sh = _part(doc).shape
    assert len(K.solids(sh)) == 1 and K.is_valid(sh)
    (far,) = _faces_at(sh, 10.0)
    (second,) = _faces_at(sh, -4.0)
    assert _side(far) == pytest.approx([18.2503, 18.2503], abs=1e-4 + 2e-6)
    s2 = 20 - 2 * 4 * T5
    assert _side(second) == pytest.approx([s2, s2], abs=1e-4)


def test_draft_off_two_ways_is_the_plain_prism():
    for kw in (dict(end="symmetric"), dict(second=True, depth2=4.0)):
        plain, _, e0 = _body(10.0, **kw)
        off, _, e1 = _body(10.0, draft=False, draft_angle=5.0, draft_flip=True, **kw)
        assert not (e0 or e1)
        a, b = _part(plain).shape, _part(off).shape
        assert K.volume(a) == pytest.approx(K.volume(b), abs=1e-9)
        assert K.volume(a) == pytest.approx(20 * 20 * (10 if "end" in kw else 14), abs=1e-6)
        assert len(K.faces(a)) == len(K.faces(b)) == 6
        assert _names(plain) == _names(off)


def test_draft_names_are_unique_and_present():
    doc, _, err = _body(10.0, end="symmetric", draft=True, draft_angle=5.0)
    assert not err, err
    names = _names(doc)
    assert all(names) and len(set(names)) == len(names) == 10


@pytest.mark.parametrize("end", ["through", "up_to_next", "to_face"])
def test_other_ends_with_draft_are_refused(end):
    doc, _, err = _body(10.0, end=end, draft=True, draft_angle=5.0)
    assert err == "Draft works with Blind and Symmetric ends"


def test_second_depth_that_is_zero_drafts_one_way():
    doc, _, err = _body(10.0, second=True, depth2=0.0, draft=True, draft_angle=5.0)
    assert not err, err
    (far,) = _faces_at(_part(doc).shape, 10.0)
    assert _side(far) == pytest.approx([18.2503, 18.2503], abs=1e-4 + 2e-6)


def test_flip_grows_both_ends():
    doc, _, err = _body(20.0, end="symmetric", draft=True, draft_angle=5.0, draft_flip=True)
    assert not err, err
    (far,) = _faces_at(_part(doc).shape, 10.0)
    assert _side(far) == pytest.approx([20 + 2 * 10 * T5] * 2, abs=1e-4)


def test_fillet_on_a_drafted_edge_survives_a_depth_change():
    doc, f, err = _body(10.0, second=True, depth2=4.0, draft=True, draft_angle=5.0)
    assert not err, err
    part = _part(doc)
    edges = edge_refs(part, lambda i: abs(i["c"][2] - 10.0) < 1e-6 and i["c"][0] > 5 and abs(i["c"][1]) < 1e-6,
                      expect=1)
    fi, e2 = try_add(doc, "fillet", edges=edges, radius=1)
    assert not e2, e2
    v0 = K.volume(_part(doc).shape)
    g = dict(f, params=dict(f["params"], depth=12.0))
    doc.replace_feature(g)
    assert not doc.errors, doc.errors
    sh = _part(doc).shape
    assert K.volume(sh) > v0
    assert K.is_valid(sh)
    assert len(K.faces(sh)) == 11            # ten drafted faces plus the fillet
