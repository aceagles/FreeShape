"""Mouse cursors for the sketch tools and view drags, drawn with QPainter like the icons (no image assets).

A sketch tool's cursor is a crosshair whose centre is the hot spot, with the tool's toolbar glyph on a small
tile below and to the right of it, as in Onshape: the point being clicked stays exact and the glyph says which
tool will act on it. Select keeps the system arrow; pan and zoom drags use the system hand and resize cursors.
"""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap, QPolygonF

from . import icons

SIZE = 32                # X11 and the browser stream both handle 32 x 32 cursors without scaling
HOT = 7                  # the crosshair centre, the cursor's hot spot
TILE = QRectF(13.5, 13.5, 17, 17)

# sketch tool -> the icon it shows (where the toolbar uses a different name)
TOOL_ICON = {"text": "sk_text"}


def _crosshair(p: QPainter):
    """A crosshair with a gap at the centre, dark on a white halo so it reads on any background."""
    arms = [(HOT, 0, HOT, HOT - 2), (HOT, HOT + 2, HOT, 2 * HOT), (0, HOT, HOT - 2, HOT), (HOT + 2, HOT, 2 * HOT, HOT)]
    for color, width in ((QColor(255, 255, 255, 235), 3.0), (QColor("#1b1f27"), 1.0)):
        pen = QPen(color, width)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        for x1, y1, x2, y2 in arms:
            p.drawLine(QPointF(x1 + 0.5, y1 + 0.5), QPointF(x2 + 0.5, y2 + 0.5))


def _tile(p: QPainter, glyph):
    """The white tile the tool glyph sits on, then the glyph (a 32-unit icon drawing) scaled into it."""
    p.setPen(QPen(QColor("#1b1f27"), 1.0))
    p.setBrush(QColor(255, 255, 255, 245))
    p.drawRoundedRect(TILE, 3, 3)
    p.save()
    p.translate(TILE.x() + 0.5, TILE.y() + 0.5)
    p.scale(16 / 32, 16 / 32)
    glyph(p)
    p.restore()


def _pixmap(paint) -> QPixmap:
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    paint(p)
    p.end()
    return pm


@lru_cache(maxsize=None)
def tool_cursor(tool: str) -> QCursor:
    """The cursor shown over the sketch while `tool` is active; the system arrow for Select."""
    if tool == "select":
        return QCursor(Qt.ArrowCursor)
    name = TOOL_ICON.get(tool, tool)
    ink = QColor(icons.INK)
    icons.INK.setRgb(QColor("#3b4250").rgb())     # the light-theme ink: the tile is white in either theme
    try:
        pm = _pixmap(lambda p: (_crosshair(p), _tile(p, lambda q: icons.draw(name, q))))
    finally:
        icons.INK.setRgb(ink.rgb())
    return QCursor(pm, HOT, HOT)


def _orbit_glyph(p: QPainter):
    """Two curved arrows round a dot: the view turns."""
    pen = QPen(QColor("#1f6fd1"), 3.2)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    r = QRectF(6, 6, 20, 20)
    p.drawArc(r, 30 * 16, 120 * 16)
    p.drawArc(r, 210 * 16, 120 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1f6fd1"))
    for deg in (30, 210):
        a = math.radians(deg)
        x, y = 16 + 10 * math.cos(a), 16 - 10 * math.sin(a)
        t = a - math.pi / 2                 # the arc runs anticlockwise; the head points along it, backwards
        tx, ty = math.cos(t), -math.sin(t)
        nx, ny = -ty, tx
        p.drawPolygon(QPolygonF([QPointF(x + 6 * tx, y + 6 * ty), QPointF(x + 4 * nx, y + 4 * ny),
                                 QPointF(x - 4 * nx, y - 4 * ny)]))
    p.drawEllipse(QPointF(16, 16), 2.6, 2.6)


@lru_cache(maxsize=None)
def nav_cursor(action: str) -> QCursor:
    """The cursor while a drag turns ("orbit"), moves ("pan") or zooms ("zoom") the view."""
    if action == "pan":
        return QCursor(Qt.ClosedHandCursor)
    if action == "zoom":
        return QCursor(Qt.SizeVerCursor)
    return QCursor(_pixmap(lambda p: (_crosshair(p), _tile(p, _orbit_glyph))), HOT, HOT)
