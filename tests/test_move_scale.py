"""Uniform scale in Move (I3.2): scale applies first (about a vertex or the origin), then rotate and translate."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from conftest import add, block_doc, only_part, try_add  # noqa: E402


def _names(doc):
    part = only_part(doc)
    named = doc.state.named(part.id)
    assert named is not None
    return {named.name_of(f) for f in K.faces(part.shape)}


def _size(shape):
    lo, hi = K.bbox(shape)
    return hi - lo


def test_scale_two_about_origin_doubles_the_box():
    doc = block_doc(10, 20, 30)
    v0 = K.volume(only_part(doc).shape)
    lo0, _ = K.bbox(only_part(doc).shape)
    add(doc, "move", parts=list(doc.state.parts), scale=2.0)
    shape = only_part(doc).shape
    assert np.allclose(_size(shape), (20, 40, 60), atol=1e-6)
    assert K.volume(shape) == pytest.approx(8 * v0, rel=1e-9)
    assert np.allclose(K.bbox(shape)[0], lo0 * 2, atol=1e-6)      # origin is the centre
    assert K.is_valid(shape)


def test_scale_about_a_selected_vertex_leaves_it_fixed():
    doc = block_doc(10, 20, 30, x0=5, y0=7)
    part = only_part(doc)
    pts = K.vertex_points(part.shape)
    pt = pts[3]
    ref = F.make_vertex_ref(part.id, pt, part.shape, doc.state.named(part.id))
    add(doc, "move", parts=[part.id], scale=3.0, center=ref)
    shape = only_part(doc).shape
    assert np.allclose(_size(shape), (30, 60, 90), atol=1e-6)
    moved = K.vertex_points(shape)
    assert min(np.linalg.norm(np.asarray(m) - pt) for m in moved) < 1e-6


def test_scale_then_translate():
    doc = block_doc(10, 20, 30)
    lo0, _ = K.bbox(only_part(doc).shape)
    add(doc, "move", parts=list(doc.state.parts), scale=2.0, dx=5.0)
    assert np.allclose(K.bbox(only_part(doc).shape)[0], lo0 * 2 + [5, 0, 0], atol=1e-6)


def test_face_names_match_a_move_with_scale_one():
    a, b = block_doc(), block_doc()
    add(a, "move", parts=list(a.state.parts), dx=3.0, scale=1.0)
    add(b, "move", parts=list(b.state.parts), dx=3.0, scale=2.0)
    na = _names(a)
    assert len(na) == 6 and na == _names(b)


def test_scale_accepts_a_variable_expression():
    doc = block_doc(10, 20, 30)
    add(doc, "variable", name="k", expr="2")
    add(doc, "move", parts=list(doc.state.parts), scale="#k * 1.5")
    assert np.allclose(_size(only_part(doc).shape), (30, 60, 90), atol=1e-6)


@pytest.mark.parametrize("bad", [0, 0.0, -1, -0.5])
def test_zero_and_negative_scale_are_refused(bad):
    doc = block_doc()
    _f, err = try_add(doc, "move", parts=list(doc.state.parts), scale=bad)
    assert err and "scale" in err.lower()
    assert np.allclose(_size(only_part(doc).shape), (40, 20, 10), atol=1e-6)


def test_kernel_transform_refuses_bad_scale():
    shape = only_part(block_doc()).shape
    with pytest.raises(K.KernelError):
        K.transform(shape, scale=-1)
    with pytest.raises(K.KernelError):
        K.transform(shape, scale=0)


def test_no_scale_takes_the_plain_path():
    a, b = block_doc(), block_doc()
    add(a, "move", parts=list(a.state.parts), dx=4.0, rotate=0.0)
    add(b, "move", parts=list(b.state.parts), dx=4.0, rotate=0.0, scale=1)
    assert np.allclose(K.bbox(only_part(a).shape), K.bbox(only_part(b).shape))
    assert _names(a) == _names(b)
