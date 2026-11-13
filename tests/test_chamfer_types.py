"""Chamfer types (I3.4): equal distance (today's path), two distances and distance + angle, with a Flip tick.
The face that takes the first distance is chosen by element name, so it survives an upstream edit."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402

from conftest import add, edge_refs, only_part, region_refs, try_add  # noqa: E402
from test_naming_blends import block, named_faces, set_depth  # noqa: E402


def front_top_edge(i):
    """The box's top edge along X at y = 0 (between the top face and the y = 0 face)."""
    return (i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - 10) < 1e-6 and abs(i["c"][1]) < 1e-6)


def setbacks(doc):
    """(setback on the top face, setback on the front face): how far the chamfer cut the two faces back."""
    part = only_part(doc)
    named = doc.state.named(part.id)
    top = front = None
    for f in K.faces(part.shape):
        n = K.face_info(f).get("n")
        if n is None:
            continue
        lo, hi = K.bbox(f)
        if n[2] > 0.999:
            top = lo[1]                       # the top face now starts this far in from y = 0
        elif n[1] < -0.999:
            front = 10.0 - hi[2]              # the front face now stops this far below the top
    return top, front


def chamfer(doc, **params):
    return add(doc, "chamfer", edges=edge_refs(only_part(doc), front_top_edge, expect=1), **params)


def test_two_distances_and_flip_swaps_them():
    doc, _ = block()
    f = chamfer(doc, chamfer_type="two", distance=2.0, distance2=4.0)
    a = setbacks(doc)
    assert sorted(a) == pytest.approx([2.0, 4.0], abs=1e-6)
    f = dict(f, params=dict(f["params"], flip=True))
    doc.replace_feature(f)
    assert not doc.errors, doc.errors
    b = setbacks(doc)
    assert b == pytest.approx(a[::-1], abs=1e-6)


def test_angle_second_setback_is_distance_times_tan():
    doc, _ = block()
    chamfer(doc, chamfer_type="angle", distance=3.0, angle=30.0)
    assert sorted(setbacks(doc)) == pytest.approx(sorted([3.0, 3.0 * math.tan(math.radians(30))]), abs=1e-6)


def test_same_face_keeps_the_first_distance_after_an_upstream_change():
    doc, ex = block()
    chamfer(doc, chamfer_type="two", distance=2.0, distance2=4.0)
    part = only_part(doc)
    named = doc.state.named(part.id)
    top_name = next(named.name_of(f) for f in K.faces(part.shape)
                    if (K.face_info(f).get("n") or [0, 0, 0])[2] > 0.999)
    first_on_top = abs(setbacks(doc)[0] - 2.0) < 1e-6
    set_depth(doc, ex, 14.0)
    part = only_part(doc)
    assert top_name in named_faces(doc)
    top2, front2 = setbacks_at(doc, 14.0)
    assert (abs(top2 - 2.0) < 1e-6) == first_on_top
    assert sorted([top2, front2]) == pytest.approx([2.0, 4.0], abs=1e-6)


def setbacks_at(doc, height):
    part = only_part(doc)
    top = front = None
    for f in K.faces(part.shape):
        n = K.face_info(f).get("n")
        lo, hi = K.bbox(f)
        if n is not None and n[2] > 0.999:
            top = lo[1]
        elif n is not None and n[1] < -0.999:
            front = height - hi[2]
    return top, front


def test_oversized_two_chamfer_is_refused():
    doc, _ = block()
    f, err = try_add(doc, "chamfer", edges=edge_refs(only_part(doc), front_top_edge, expect=1),
                     chamfer_type="two", distance=2.0, distance2=50.0)
    assert err and "Chamfer too large" in err


def test_equal_and_missing_type_match():
    d1, _ = block()
    chamfer(d1, distance=2.0)
    d2, _ = block()
    chamfer(d2, chamfer_type="equal", distance=2.0)
    assert K.volume(only_part(d1).shape) == pytest.approx(K.volume(only_part(d2).shape), abs=1e-9)
    assert setbacks(d1) == pytest.approx((2.0, 2.0), abs=1e-6)


def test_distance2_accepts_an_expression():
    doc, _ = block()
    add(doc, "variable", name="w", expr="4")
    chamfer(doc, chamfer_type="two", distance=2.0, distance2="#w")
    assert sorted(setbacks(doc)) == pytest.approx([2.0, 4.0], abs=1e-6)
