"""Display meshes are coarser for a small part in a big model (I2.3, #66): fewer triangles, never more than the
part's default mesh; the same faces in the same order (so a pick holds); edges and STL/3MF export untouched; and the
engine worker frees a shape's triangulation once its MeshOut exists."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRep import BRep_Tool  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeSphere  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.engine.encode import Encoder  # noqa: E402
from freeshape.import_timing import mesh_stats  # noqa: E402

def _sphere(c, r):
    return BRepPrimAPI_MakeSphere(gp_Pnt(*c), r).Shape()


def _box(x, y, z):
    return BRepPrimAPI_MakeBox(x, y, z).Shape()


from test_step_import_instances import COPIES, step_path  # noqa: E402,F401 -- the module's STEP fixture

BIG = 5000.0         # the diagonal of the model the small part sits in


def _screw():
    """A shank with a round head and a hole through it: cylinders, a sphere and a plane, 10 mm long."""
    shank = K.cylinder((0, 0, 0), (0, 0, 1), 1.5, 10)
    head = _sphere((0, 0, 10), 2.5)
    return K.cut(K.fuse(shank, head), K.cylinder((0, 0, 4), (0, 0, 1), 0.6, 3))


def _tris(shape, model_diag=None):
    return len(K.tessellate(shape, model_diag=model_diag).tris)


def test_a_small_part_in_a_large_model_has_fewer_triangles_than_alone():
    alone, inside = _tris(_screw()), _tris(_screw(), BIG)
    assert 0 < inside < alone


def test_a_model_never_adds_triangles():
    for make in (_screw, lambda: K.cylinder((0, 0, 0), (0, 0, 1), 40, 5), lambda: _sphere((0, 0, 0), 20)):
        alone = _tris(make())
        for diag in (1.0, 50.0, 500.0, BIG, 1e6):
            assert _tris(make(), diag) <= alone


def test_a_part_that_fills_its_model_is_meshed_as_alone():
    shape = _screw()
    assert _tris(shape, 12.0) == _tris(_screw())


def test_an_explicit_deflection_ignores_the_model():
    """STL / 3MF export passes its own deflection (meshexport.py): the display model's size does not reach it."""
    a = K.tessellate(_screw(), 0.05, 0.3, edges_too=False)
    b = K.tessellate(_screw(), 0.05, 0.3, edges_too=False, model_diag=BIG)
    assert len(a.tris) == len(b.tris)


def test_edges_keep_the_parts_own_deflection():
    a, b = K.tessellate(_screw()), K.tessellate(_screw(), model_diag=BIG)
    assert [len(p) for p in a.edge_polys] == [len(p) for p in b.edge_polys]


def _face_hit(mesh, origin, direction):
    """The face index of the first triangle of `mesh` a ray meets (Moller-Trumbore), or None."""
    v = np.asarray(mesh.verts, float)
    t = np.asarray(mesh.tris, np.int64)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    w = (q @ direction) * inv
    dist = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6) & (dist > 0)
    if not hit.any():
        return None
    return int(mesh.tri_face[np.where(hit, dist, np.inf).argmin()])


def test_picking_hits_the_same_face_on_the_coarse_and_the_default_mesh():
    shape = _screw()
    fine, coarse = K.tessellate(shape), K.tessellate(_screw(), model_diag=BIG)
    assert len(coarse.face_ranges) == len(fine.face_ranges) == len(K.faces(shape))
    checked = 0
    for fi, (lo, hi) in enumerate(coarse.face_ranges):
        if hi == lo:
            continue
        ti = lo + int(np.argmax([_area(coarse, k) for k in range(lo, hi)]))      # the face's biggest triangle
        tri = coarse.tris[ti]
        centre = coarse.verts[tri].astype(float).mean(axis=0)
        n = np.asarray(coarse.normals[tri], float).mean(axis=0)
        n /= np.linalg.norm(n)
        # a pick ray comes from outside, along the inward normal, to the centre of that coarse triangle
        assert _face_hit(coarse, centre + n * 0.5, -n) == fi
        assert _face_hit(fine, centre + n * 0.5, -n) == fi, f"face {fi} is picked as another face on the default mesh"
        checked += 1
    assert checked == len(K.faces(shape))


def _area(mesh, k):
    a, b, c = mesh.verts[mesh.tris[k]].astype(float)
    return float(np.linalg.norm(np.cross(b - a, c - a)))


# ------------------------------------------------------------------ the Encoder

def test_the_encoder_meshes_relative_to_its_model_and_keeps_the_names():
    big = K.cylinder((0, 0, 0), (0, 0, 1), 1500, 20)
    screw, same = _screw(), _screw()       # (a shape that already holds a fine triangulation is not meshed coarser)
    enc = Encoder()
    alone = enc.mesh(screw, "alone")
    enc.set_model([big, screw])
    assert enc.model_diag is not None and enc.model_diag > 1000
    inside = enc.mesh(same, "alone")           # same hash, another model: not served from the first mesh
    assert len(inside.tris) < len(alone.tris)
    assert len(inside.face_ranges) == len(alone.face_ranges)
    enc.set_model([screw])
    assert enc.mesh(same, "alone") is not inside        # another model, another mesh ...
    enc.set_model([big, screw])
    assert enc.mesh(same, "alone") is inside            # ... and back in the first one it is still cached


def test_a_small_edit_to_the_model_keeps_every_mesh_cached():
    enc = Encoder()
    screw = _screw()
    enc.set_model([_box(3000, 3000, 3000), screw])
    first = enc.model_diag
    enc.set_model([_box(3000, 3000, 3030), screw])      # 0.3% bigger
    assert enc.model_diag == first


def test_the_worker_frees_the_triangulation_once_the_mesh_exists_and_the_mesh_survives():
    kept, freed = Encoder(), Encoder()
    freed.release = True
    for enc, shape in ((kept, _screw()), (freed, _screw())):
        mesh = enc.mesh(shape, "h")
        has = all(BRep_Tool.Triangulation_s(f, TopLoc_Location()) is not None for f in K.faces(shape))
        assert has is (enc is kept)
        assert len(mesh.tris) > 0 and len(mesh.face_ranges) == len(K.faces(shape))
    # a later re-mesh of a freed shape (another model size) works
    shape = _screw()
    freed.mesh(shape, "h2")
    freed.set_model([_box(3000, 3000, 3000)])
    assert len(freed.mesh(shape, "h2").tris) > 0


def test_the_worker_engine_turns_release_on_and_the_window_engine_does_not():
    from freeshape.engine import InProcessEngine
    from freeshape.engine.worker import _WorkerEngine
    with InProcessEngine() as e:
        assert e.encoder.release is False
    with _WorkerEngine() as e:
        assert e.encoder.release is True


# ------------------------------------------------------------------ the window (engine mode)

def test_the_engine_window_draws_a_model_whose_worker_freed_its_triangulation(step_path):  # noqa: F811
    from test_import_in_engine import _close, _window
    from test_partstudio_engine import _children
    before = set(_children())
    w = _window()
    try:
        w.import_step_file(step_path)
        eng, doc = w.studio_engine, w.doc
        assert eng.settle(180) and not doc.errors
        assert len(eng.displayed(doc)) == COPIES
        tris, mb = mesh_stats(w)
        assert tris > 0 and mb > 0
        for p in doc.state.parts.values():          # every drawn part has one mesh face range per face (a pick's index)
            m = w.viewport.mesh_for(p.shape)
            assert len(m.face_ranges) == len(K.faces(p.shape))
    finally:
        _close(w, before)
