"""Negative depth/angle flips direction (as in Onshape) and extrude/revolve/sweep/loft take a merge scope."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from conftest import add, block_doc, only_part, rect_lines, region_refs, sketch_feature, try_add  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402


def _plate(doc, x0, w, depth=20, **kw):
    sid = sketch_feature(doc, lambda s: rect_lines(s, x0, 0, w, 20))
    return add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, **kw)


def _two_blocks():
    """A at x 0..20 and B at x 30..50, both 20 deep and 10 tall (4000 mm3 each)."""
    doc = block_doc(w=20, d=20, h=10)
    _plate(doc, 30, 20, depth=10, op="new")
    (a, b) = sorted(doc.state.parts)
    return doc, a, b


def _vols(doc):
    return {k: round(K.volume(p.shape), 3) for k, p in doc.state.parts.items()}


def _zrange(doc):
    lo, hi = K.bbox(only_part(doc).shape)
    return round(float(lo[2]), 6), round(float(hi[2]), 6)


def _single(depth, **kw):
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 20))
    add(doc, "extrude", regions=region_refs(doc, sid), op="new", depth=depth, **kw)
    return doc


def test_negative_depth_equals_flip():
    neg = _single(-10)
    flipped = _single(10, flip=True)
    assert _zrange(neg) == _zrange(flipped) == (-10.0, 0.0)
    assert _zrange(_single(10)) == (0.0, 10.0)
    # flipping a negative depth goes back the original way
    assert _zrange(_single(-10, flip=True)) == (0.0, 10.0)


def test_negative_expression_depth():
    doc = Document()
    add(doc, "variable", name="wall", expr="10")
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 20))
    add(doc, "extrude", regions=region_refs(doc, sid), op="new", depth="-#wall")
    assert _zrange(doc) == (-10.0, 0.0)


def test_zero_depth_is_still_an_error():
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 20))
    _, err = try_add(doc, "extrude", regions=region_refs(doc, sid), op="new", depth=0)
    assert err


def test_negative_revolve_angle_reverses():
    def rev(angle):
        doc = Document()
        sid = sketch_feature(doc, lambda s: rect_lines(s, 5, 0, 5, 10), {"kind": "std", "name": "Front"})
        add(doc, "revolve", regions=region_refs(doc, sid), op="new", end="one", angle=angle,
            axis={"kind": "std", "name": "Z"})
        return K.bbox(only_part(doc).shape)
    (lo_p, hi_p), (lo_n, hi_n) = rev(90), rev(-90)
    assert not np.allclose(lo_p, lo_n) or not np.allclose(hi_p, hi_n)
    (lo_f, hi_f) = (lambda d: K.bbox(only_part(d).shape))(_rev_flipped())
    assert np.allclose(lo_n, lo_f, atol=1e-4) and np.allclose(hi_n, hi_f, atol=1e-4)


def _rev_flipped():
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 5, 0, 5, 10), {"kind": "std", "name": "Front"})
    add(doc, "revolve", regions=region_refs(doc, sid), op="new", end="one", angle=90, flip=True,
        axis={"kind": "std", "name": "Z"})
    return doc


def test_add_scoped_to_one_part():
    doc, a, b = _two_blocks()
    before = _vols(doc)
    assert before[a] == before[b] == 4000
    # a bridge x 15..35 touches both blocks; its free middle is 10 x 20 x 10
    _plate(doc, 15, 20, depth=10, op="add", scope=[a])
    after = _vols(doc)
    assert set(after) == {a, b}
    assert after[b] == 4000                        # untouched
    assert after[a] == 4000 + 15 * 20 * 10          # A plus the bridge outside A (x 20..35)


def test_add_empty_scope_merges_everything_touched():
    doc, a, b = _two_blocks()
    _plate(doc, 15, 20, depth=10, op="add")
    assert len(doc.state.parts) == 1
    assert round(K.volume(only_part(doc).shape), 3) == 50 * 20 * 10
    doc2, a, b = _two_blocks()
    _plate(doc2, 15, 20, depth=10, op="add", scope=[])
    assert len(doc2.state.parts) == 1


def test_remove_scoped_to_one_part():
    doc, a, b = _two_blocks()
    _plate(doc, 15, 20, depth=20, op="remove", scope=[b])
    v = _vols(doc)
    assert v[a] == 4000                            # not in scope: keeps its overlap
    assert v[b] == 4000 - 5 * 20 * 10              # B loses x 30..35


def test_remove_empty_scope_cuts_all():
    doc, a, b = _two_blocks()
    _plate(doc, 15, 20, depth=20, op="remove")
    v = _vols(doc)
    assert v[a] == 4000 - 5 * 20 * 10
    assert v[b] == 4000 - 5 * 20 * 10
