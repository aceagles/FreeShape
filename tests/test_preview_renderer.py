"""I6.1: the offscreen preview renderer draws a part, a sketch and a connector with QPainter (no OpenGL)."""
import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QStandardPaths  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from freeshape.engine.contract import ConnectorOut, MeshOut, PartOut, SketchOut  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui import preview as PV  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Own cache dir and an empty memory cache for every test."""
    monkeypatch.setattr("freeshape.ui.home.thumbs_dir", lambda: str(tmp_path))
    PV.clear_memory_cache()
    yield
    PV.clear_memory_cache()


def box_mesh(sx, sy, sz):
    v = np.array([[x, y, z] for x in (0, sx) for y in (0, sy) for z in (0, sz)], np.float32)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = np.array([t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))], np.uint32)
    return MeshOut(verts=v, normals=np.zeros_like(v), tris=tris, tri_face=np.zeros(len(tris), np.int32),
                   edge_polys=[], vertices=v)


def make_part(h, mesh=None, color=(0.6, 0.7, 0.8)):
    return PartOut(id="p", name="p", color=color, hidden=False, brep=b"", hash=h, names={},
                   mesh=mesh or box_mesh(40, 20, 10))


def make_sketch():
    s = Sketch()
    a, b, c, d = (s.add_point(x, y) for x, y in ((0, 0), (40, 0), (40, 25), (0, 25)))
    for p, q in ((a, b), (b, c), (c, d), (d, a)):
        s.add_line(p, q)
    s.add_circle(s.add_point(20, 12), 6)
    plane = {"origin": [0, 0, 0], "xdir": [1, 0, 0], "normal": [0, 0, 1]}
    return SketchOut(id="s", name="s", plane=plane, sketch=s.to_dict(), regions=[])


def make_connector():
    fr = {"o": [40, 10, 10], "x": [1, 0, 0], "z": [0, 0, 1], "what": "flat face"}
    return ConnectorOut(instance="i", kind="face", index=1, frame=fr, world=fr)


def ink(img):
    bg = PV.BACKGROUND.rgb()
    px = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :img.width()]
    bgc = np.array([(bg >> 16) & 255, (bg >> 8) & 255, bg & 255])
    return float(np.mean(np.any(px[..., [2, 1, 0]] != bgc, axis=-1)))


def raw(img):
    return bytes(img.constBits())


@pytest.mark.parametrize("size", [64, 128])
def test_part_sketch_connector_have_ink_and_requested_size(size):
    part = make_part("h1")
    for img in (PV.render_preview(part, size), PV.render_preview(make_sketch(), size),
                PV.render_preview(make_connector(), size, part=part), PV.render_preview(make_connector(), size)):
        assert isinstance(img, QImage)
        assert (img.width(), img.height()) == (size, size)
        assert ink(img) > 0.02


def test_different_parts_differ_and_same_part_is_byte_identical():
    a, b = make_part("ha"), make_part("hb", box_mesh(10, 10, 40))
    ia, ib = PV.render_preview(a, 96), PV.render_preview(b, 96)
    assert raw(ia) != raw(ib)
    PV.clear_memory_cache()
    for f in os.listdir(os.path.dirname(PV._disk_path(("x", 1, 1)))):   # also identical when redrawn, not cached
        os.remove(os.path.join(os.path.dirname(PV._disk_path(("x", 1, 1))), f))
    assert raw(PV.render_preview(make_part("ha"), 96)) == raw(ia)


def test_second_request_does_not_draw(monkeypatch):
    calls = []
    real = PV._draw
    monkeypatch.setattr(PV, "_draw", lambda *a, **k: calls.append(1) or real(*a, **k))
    part = make_part("hc")
    PV.render_preview(part, 96)
    PV.render_preview(part, 96)
    assert len(calls) == 1
    PV.render_preview(part, 64)          # a different size is a different key
    assert len(calls) == 2
    PV.clear_memory_cache()
    PV.render_preview(part, 96)          # served from the disk cache
    assert len(calls) == 2
    assert os.path.exists(PV._disk_path((PV.content_hash(part), 96, 96)))


def test_20000_triangles_under_a_second():
    rng = np.random.default_rng(1)
    n = 20000
    verts = rng.uniform(0, 100, (n * 3, 3)).astype(np.float32)
    tris = np.arange(n * 3, dtype=np.uint32).reshape(n, 3)
    mesh = MeshOut(verts=verts, normals=np.zeros_like(verts), tris=tris, tri_face=np.zeros(n, np.int32),
                   edge_polys=[], vertices=verts)
    t = time.perf_counter()
    img = PV.render_preview(make_part("big", mesh), 96)
    assert time.perf_counter() - t < 1.0
    assert ink(img) > 0.02


def test_unknown_item_is_rejected():
    with pytest.raises(TypeError):
        PV.render_preview(object(), 96)
