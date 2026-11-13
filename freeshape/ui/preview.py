"""Offscreen preview renderer: a QImage of a published part, a sketch or a mate connector, drawn with QPainter.

No OpenGL: viewport.render_thumbnail grabs the live GL viewport, which is not dependable offscreen. Here the view
is isometric, fitted to the item's bounding box; triangles are flat-shaded and depth-sorted (painter's algorithm).
Images are cached by (content hash, size) in memory and as PNGs beside the Home thumbnails (home.keyed_thumb_path).
"""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPolygonF

from ..engine.contract import ConnectorOut, PartOut, SketchOut

BACKGROUND = QColor(245, 245, 247)
_EYE = np.array([1.0, -1.0, 1.0]) / np.sqrt(3)       # isometric, Z up; the viewer sits at +eye
_RIGHT = np.array([1.0, 1.0, 0.0]) / np.sqrt(2)
_UP = np.array([-1.0, 1.0, 2.0]) / np.sqrt(6)        # right x up == eye: a right-handed screen
_LIGHT = (_EYE * 2 + _UP + _RIGHT * -1.0)
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)
_MARGIN = 0.1
_AXIS_COLORS = (QColor(220, 60, 50), QColor(60, 170, 70), QColor(60, 100, 220))   # x, y, z

_memory: dict = {}


def render_preview(item, size=96, part: PartOut | None = None) -> QImage:
    """Preview of a PartOut, SketchOut or ConnectorOut as a size x size (or (w, h)) QImage.

    A connector is drawn as its triad on `part` (its instance's PartOut); without `part` the triad is drawn alone.
    Cached by (content hash, size): a repeat request returns the cached image without drawing."""
    w, h = (size, size) if isinstance(size, int) else (int(size[0]), int(size[1]))
    key = (content_hash(item, part), w, h)
    img = _memory.get(key)
    if img is None:
        img = _load_disk(key)
        if img is None:
            img = _draw(item, w, h, part)
            _save_disk(key, img)
        _memory[key] = img
    return img.copy()


def clear_memory_cache():
    _memory.clear()


def content_hash(item, part=None) -> str:
    if isinstance(item, PartOut):
        return "part:" + item.hash + ":" + _color_key(item.color)
    if isinstance(item, SketchOut):
        blob = json.dumps({"plane": item.plane, "sketch": item.sketch}, sort_keys=True, default=_jsonable)
        return "sketch:" + hashlib.sha256(blob.encode()).hexdigest()
    if isinstance(item, ConnectorOut):
        blob = json.dumps({"frame": item.frame, "part": part.hash if part is not None else None},
                          sort_keys=True, default=_jsonable)
        return "connector:" + hashlib.sha256(blob.encode()).hexdigest()
    raise TypeError(f"cannot preview {type(item).__name__}")


def _jsonable(o):
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)


def _color_key(c):
    return ",".join(f"{float(v):.3f}" for v in c)


# ------------------------------------------------------------------ disk cache
def _disk_path(key):
    from .home import thumbs_dir
    name = hashlib.sha1(f"preview:{key[0]}:{key[1]}x{key[2]}".encode()).hexdigest()[:20]
    return os.path.join(thumbs_dir(), "preview-" + name + ".png")


def _load_disk(key):
    try:
        p = _disk_path(key)
        if os.path.exists(p):
            img = QImage(p)
            if not img.isNull() and (img.width(), img.height()) == (key[1], key[2]):
                return img.convertToFormat(QImage.Format_RGB32)
    except OSError:
        pass
    return None


def _save_disk(key, img):
    try:
        img.save(_disk_path(key))
    except OSError:
        pass


# ------------------------------------------------------------------ drawing
def _draw(item, w, h, part=None) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(BACKGROUND)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    try:
        if isinstance(item, PartOut):
            _draw_part(p, item, w, h)
        elif isinstance(item, SketchOut):
            _draw_sketch(p, item, w, h)
        elif isinstance(item, ConnectorOut):
            _draw_connector(p, item, w, h, part)
        else:
            raise TypeError(f"cannot preview {type(item).__name__}")
    finally:
        p.end()
    return img


def _project(pts):
    """(n, 3) world points -> (n, 2) screen coordinates (x right, y up) and (n,) depth (larger = nearer)."""
    pts = np.asarray(pts, float)
    return np.stack([pts @ _RIGHT, pts @ _UP], axis=1), pts @ _EYE


class _Fit:
    """Maps projected points into the image: uniform scale, centred, with a margin."""

    def __init__(self, xy, w, h):
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        span = np.maximum(hi - lo, 1e-9)
        avail = np.array([w, h]) * (1 - 2 * _MARGIN)
        self.s = float(min(avail / span))
        self.c = (lo + hi) / 2
        self.w, self.h = w, h

    def __call__(self, xy):
        out = (np.asarray(xy) - self.c) * self.s
        return np.stack([self.w / 2 + out[..., 0], self.h / 2 - out[..., 1]], axis=-1)


def _rgb(color):
    c = np.array(color[:3], float) if color is not None and len(color) >= 3 else np.array([0.7, 0.72, 0.78])
    if c.max() > 1.0:
        c = c / 255.0
    return np.clip(c, 0, 1)


def _mesh_tris(part):
    m = part.mesh
    if m is None or len(m.tris) == 0:
        return None
    return np.asarray(m.verts, float)[np.asarray(m.tris, np.int64)]      # (m, 3, 3)


def _paint_tris(p, tris, fit, color, alpha=255):
    xy, depth = _project(tris.reshape(-1, 3))
    xy = fit(xy).reshape(-1, 3, 2)
    depth = depth.reshape(-1, 3).mean(axis=1)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1)
    n = n / np.maximum(ln, 1e-12)[:, None]
    n = np.where((n @ _EYE < 0)[:, None], -n, n)                          # two-sided: light the visible side
    shade = 0.3 + 0.7 * np.clip(n @ _LIGHT, 0, 1)
    order = np.argsort(depth, kind="stable")                              # far to near
    base = _rgb(color)
    cols = np.clip(base[None, :] * shade[:, None] * 255 + 0.5, 0, 255).astype(np.int64)
    last = None
    for i in order:
        if ln[i] < 1e-12:
            continue
        c = (int(cols[i, 0]), int(cols[i, 1]), int(cols[i, 2]))
        if c != last:
            qc = QColor(c[0], c[1], c[2], alpha)
            p.setBrush(QBrush(qc))
            p.setPen(QPen(qc, 0.6))            # a hairline in the fill colour closes the seams between triangles
            last = c
        t = xy[i]
        p.drawPolygon(QPolygonF([QPointF(t[0, 0], t[0, 1]), QPointF(t[1, 0], t[1, 1]), QPointF(t[2, 0], t[2, 1])]))


def _draw_part(p, part, w, h):
    tris = _mesh_tris(part)
    if tris is None:
        return
    xy, _ = _project(tris.reshape(-1, 3))
    _paint_tris(p, tris, _Fit(xy, w, h), part.color)


def _plane_axes(plane):
    o = np.array(plane["origin"], float)
    x = np.array(plane["xdir"], float)
    z = np.array(plane["normal"], float)
    x = x / np.linalg.norm(x)
    z = z / np.linalg.norm(z)
    return o, x, np.cross(z, x)


def _draw_sketch(p, sk, w, h):
    from ..sketch import Sketch
    s = Sketch.from_dict(sk.sketch)
    o, x, y = _plane_axes(sk.plane)
    lines = []
    for eid, e in s.entities.items():
        pl = s.polyline(eid)
        if len(pl) >= 2:
            lines.append((pl, bool(e.get("construction"))))
    pts2 = [pl for pl, _ in lines]
    if not pts2:
        return
    allp = np.vstack(pts2)
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    pad = max(float(np.max(hi - lo)) * 0.08, 1e-6)
    lo, hi = lo - pad, hi + pad
    def to3(a):
        a = np.asarray(a, float)
        return o + a[:, :1] * x + a[:, 1:2] * y
    quad = to3(np.array([[lo[0], lo[1]], [hi[0], lo[1]], [hi[0], hi[1]], [lo[0], hi[1]]]))
    xy, _ = _project(quad)
    fit = _Fit(xy, w, h)
    p.setPen(QPen(QColor(150, 175, 215), 1.0))
    p.setBrush(QColor(205, 220, 245))
    p.drawPolygon(QPolygonF([QPointF(*fit(v)) for v in xy]))
    for pl, construction in lines:
        sxy = fit(_project(to3(pl))[0])
        pen = QPen(QColor(120, 120, 130) if construction else QColor(20, 40, 90), 2.0)
        if construction:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(0, 0, 0, 0))
        if len(sxy) == 1:
            p.drawPoint(QPointF(*sxy[0]))
        else:
            p.drawPolyline(QPolygonF([QPointF(*v) for v in sxy]))


def _draw_connector(p, con, w, h, part):
    fr = con.frame
    o = np.array(fr["o"], float)
    z = np.array(fr["z"], float)
    x = np.array(fr["x"], float)
    z = z / np.linalg.norm(z)
    x = x - z * (x @ z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    tris = _mesh_tris(part) if part is not None else None
    if tris is not None:
        diag = float(np.linalg.norm(tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0)))
    else:
        diag = 1.0
    length = 0.3 * diag
    ends = [o + a * length for a in (x, y, z)]
    pts = np.vstack([o[None, :]] + [e[None, :] for e in ends])
    if tris is not None:
        pts = np.vstack([tris.reshape(-1, 3), pts])
    fit = _Fit(_project(pts)[0], w, h)
    if tris is not None:
        _paint_tris(p, tris, fit, part.color if part is not None else None, alpha=150)
    so = fit(_project(o[None, :])[0])[0]
    for e, col in zip(ends, _AXIS_COLORS):
        se = fit(_project(e[None, :])[0])[0]
        p.setPen(QPen(col, max(2.0, min(w, h) / 32)))
        p.drawLine(QPointF(*so), QPointF(*se))
