"""The display mesh default (I1.7): angular deflection 0.2, so a small hole is not faceted."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402


def _plate_with_hole():
    """A 100 mm disc with a 2 mm diameter through hole; built fresh so no earlier mesh is cached on it."""
    plate = K.cylinder((0, 0, 0), (0, 0, 1), 50, 5)
    return K.cut(plate, K.cylinder((0, 0, -1), (0, 0, 1), 1, 7))


def _hole(mesh, shape):
    """(triangles of the hole's wall, points of its rim polylines)."""
    tris = 0
    for f, (a, b) in zip(K.faces(shape), mesh.face_ranges):
        lo, hi = K.bbox(f)
        if np.linalg.norm(hi - lo) < 10:
            tris += b - a
    rim = [len(p) for p in mesh.edge_polys
           if len(p) > 2 and np.ptp(np.asarray(p), axis=0).max() < 2.5]
    return tris, rim


def test_default_is_the_finer_angle():
    shape = _plate_with_hole()
    tris, rim = _hole(K.tessellate(shape), shape)
    ref_shape = _plate_with_hole()
    ref_tris, ref_rim = _hole(K.tessellate(ref_shape, ang_defl=0.2), ref_shape)
    assert ref_tris > 0 and rim
    assert tris >= ref_tris
    assert sorted(rim) == sorted(ref_rim)
    assert sum(rim) >= sum(ref_rim)


def test_default_is_finer_than_the_old_angle():
    shape = _plate_with_hole()
    tris, _ = _hole(K.tessellate(shape), shape)
    old_shape = _plate_with_hole()
    old_tris, _ = _hole(K.tessellate(old_shape, ang_defl=0.3), old_shape)
    assert tris > old_tris
