"""The Calibrate Canvas mode of the 3D view (I12.4): two clicks on one canvas, then a length.

FreeCAD's image plane has Calibrate: the person draws a line on the picture between two points whose real distance
they know and types that distance. Here the first click sets one end; while the mouse moves a line and its length
follow it (Ctrl turns the line to the nearest 5 degrees); the second click opens the inline box (the sketcher's 110 px
dimension editor) at the line's middle, filled with the measured length; Enter hands the line and the typed length to
`on_done(p0, p1, distance)`; Esc or a right click ends the mode (`on_cancel`).

The points are in the frame `freeshape.canvas_calibrate.calibrate` takes: millimetres from the image centre along the
image's drawn x and y (the canvas's `xdir` and `ydir`, flips and rotation already applied). A click maps to them by
casting the view ray onto the canvas plane (`origin`, `normal`) and projecting the hit on `xdir` and `ydir`.

The viewport calls `press`, `move`, `cancel` and `draw` while it holds this mode (`Viewport.calibrating`); nothing
else in the view picks, hovers or selects. Middle-button navigation is left alone.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPen

from . import theme
from .numparse import fmt, parse_number

SNAP_DEG = 5.0              # Ctrl turns the line to a multiple of this, in the image's own frame
MIN_PIXELS = 3.0            # a second click this close to the first is the same click twice: it does nothing


class CalibrateMode:
    def __init__(self, vp, fid, on_done, on_cancel, variables=None, say=None):
        self.vp, self.fid = vp, fid
        self.on_done, self.on_cancel = on_done, on_cancel
        self.variables = variables
        self.say = say or (lambda text: None)
        self.p0 = None                   # (u, v) mm from the image centre, the first click
        self.p1 = None                   # the second click, or the (snapped) point under the mouse while moving
        self.fixed = False               # the second click is made: the line stays and the box is open
        self.editor = None
        self._returned = False           # Enter was pressed and refused: the editingFinished that follows is not a focus loss

    # ------------------------------------------------------------------ the frame
    def canvas(self):
        return next((c for c in self.vp.canvases if c["id"] == self.fid), None)

    def to_image(self, x, y):
        """(u, v, inside) of the view point (x, y) on this canvas: mm from its centre along the drawn x and y, and
        whether it is within the picture; None when the ray misses the canvas plane."""
        c = self.canvas()
        if c is None:
            return None
        o, d = self.vp.cam.ray(x, y)
        n = np.asarray(c["normal"], float)
        den = float(d @ n)
        if abs(den) < 1e-9:
            return None
        origin = np.asarray(c["origin"], float)
        t = float((origin - o) @ n) / den
        if t <= 0:
            return None
        rel = o + t * d - origin
        u, v = float(rel @ np.asarray(c["xdir"], float)), float(rel @ np.asarray(c["ydir"], float))
        return u, v, abs(u) <= float(c["width"]) / 2 and abs(v) <= float(c["height"]) / 2

    def to_screen(self, uv):
        c = self.canvas()
        w = np.asarray(c["origin"], float) + uv[0] * np.asarray(c["xdir"], float) + uv[1] * np.asarray(c["ydir"], float)
        s = self.vp.cam.to_screen(w)
        return float(s[0]), float(s[1])

    def _snapped(self, uv, ctrl):
        """`uv` with the line p0 -> uv turned to a multiple of 5 degrees (same length) when Ctrl is held."""
        if not ctrl or self.p0 is None:
            return uv
        du, dv = uv[0] - self.p0[0], uv[1] - self.p0[1]
        length = math.hypot(du, dv)
        if length == 0:
            return uv
        a = round(math.degrees(math.atan2(dv, du)) / SNAP_DEG) * SNAP_DEG
        return self.p0[0] + length * math.cos(math.radians(a)), self.p0[1] + length * math.sin(math.radians(a))

    # ------------------------------------------------------------------ what the view draws
    def length(self):
        """The line's length in mm (what the live label shows and the box starts with); None before the first click."""
        if self.p0 is None or self.p1 is None:
            return None
        return math.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1])

    def line_screen(self):
        """((x0, y0), (x1, y1)) in view pixels of the line the view draws; None before the first click."""
        if self.p0 is None or self.p1 is None or self.canvas() is None:
            return None
        return self.to_screen(self.p0), self.to_screen(self.p1)

    def draw(self, p):
        ln = self.line_screen()
        if ln is None:
            return
        (x0, y0), (x1, y1) = ln
        pen = QPen(QColor(theme.ACCENT), 2)
        p.setPen(pen)
        p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.setBrush(QColor(theme.ACCENT))
        for x, y in ln:
            p.drawEllipse(QPointF(x, y), 3.5, 3.5)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if self.editor is not None:
            self.editor.move(int(mx - 55), int(my - 12))         # follows the line when the view is zoomed or turned
        else:
            f = QFont(p.font())
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(theme.ACCENT_DARK))
            p.drawText(QPointF(mx + 8, my - 8), fmt(self.length()) + " mm")

    # ------------------------------------------------------------------ events
    def press(self, x, y, button, mods):
        """True when the mode took the press (left sets a point, right ends the mode); the middle button navigates."""
        if button == Qt.RightButton:
            self.cancel()
            return True
        if button != Qt.LeftButton:
            return False
        if self.fixed:
            return True                                     # the box is open: Enter or Esc, not another point
        hit = self.to_image(x, y)
        if hit is None or not hit[2]:
            return True                                     # a click that misses the canvas does nothing
        uv = hit[:2]
        if self.p0 is None:
            self.p0 = self.p1 = uv
            self.say("Click the other end of the known distance (Ctrl turns the line to 5 degree steps)")
            return True
        uv = self._snapped(uv, bool(mods & Qt.ControlModifier))
        s0, s1 = self.to_screen(self.p0), self.to_screen(uv)
        if math.hypot(s1[0] - s0[0], s1[1] - s0[1]) < MIN_PIXELS:
            return True                                     # no line yet: the second end must be somewhere else
        self.p1, self.fixed = uv, True
        self._open_editor()
        return True

    def move(self, x, y, mods):
        if self.p0 is None or self.fixed:
            return
        hit = self.to_image(x, y)
        if hit is not None:
            self.p1 = self._snapped(hit[:2], bool(mods & Qt.ControlModifier))
            self.vp.update()

    def _open_editor(self):
        from .sketcher import Sketcher
        (x0, y0), (x1, y1) = self.line_screen()
        # the sketcher's dimension box: _make_editor reads nothing of the sketcher but its arguments
        ed = Sketcher._make_editor(None, self.vp, (x0 + x1) / 2, (y0 + y1) / 2, fmt(self.length()))
        ed.setToolTip("The real length of this line, in mm. Enter applies, Esc cancels.")
        ed.returnPressed.connect(self._commit)
        ed.editingFinished.connect(self._dismiss)
        self.editor = ed
        ed.show()
        ed.setFocus()
        ed.selectAll()
        self.say("Type the real length of the line and press Enter")
        self.vp.update()

    def _commit(self):
        ed = self.editor
        if ed is None:
            return
        v = parse_number(ed.text().strip(), variables=self.variables)
        if v is None or not v > 0:
            self.say("The length must be a number above 0")      # the box stays: correct it, or Esc
            self._returned = True
            return
        p0, p1 = self.p0, self.p1
        self._finish()
        self.on_done(p0, p1, float(v))

    def _dismiss(self):
        if self._returned:
            self._returned = False
            return
        if self.editor is not None:                           # focus left the box without Enter: no calibration
            self.cancel()

    def _close_editor(self):
        ed, self.editor = self.editor, None
        if ed is not None:
            ed.blockSignals(True)
            ed.hide()
            ed.deleteLater()

    def _finish(self):
        self._close_editor()
        if self.vp.calibrating is self:
            self.vp.calibrating = None
        self.vp.setCursor(Qt.ArrowCursor)
        self.vp.update()

    def cancel(self):
        self._finish()
        self.on_cancel()
