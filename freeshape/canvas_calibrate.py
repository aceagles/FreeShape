"""Calibrate a Canvas from a line drawn on its image (I12.2). Qt-free, numbers in and out.

FreeCAD's image plane has a Calibrate command: the person draws a line on the picture between two points whose real
distance they know, and the picture is scaled until the line has that length. The desktop's calibrate (I12.4) and
the browser's (I12.6) both call `calibrate`, so neither holds any of the arithmetic.

The frame. `p0` and `p1` are points in the canvas's own frame: millimetres from the image centre, along the
image's drawn x (left to right) and drawn y (bottom to top). That is the frame of a Canvas feature's `xdir` and
`ydir` (features.run_canvas): Rotation and the two flips are already applied to it, so a client that draws on the
picture as it shows gets these numbers without knowing about either. `params` is the feature's params; only
`width`, `height`, `x`, `y`, `rotation`, `flip_h` and `flip_v` are read, and only the first five are written.
"""
from __future__ import annotations

import math

MIN_LENGTH = 1e-6           # mm: a line shorter than this has no direction and no scale
SNAP_STEP = 45.0            # degrees: `snap` turns the line onto a multiple of this


class CalibrateError(ValueError):
    """The calibration cannot be made; the message says why."""


def _number(params, key, default=None):
    v = params.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise CalibrateError(f"The canvas {key} is not a number yet: set it before calibrating")
    return float(v)


def _point(p, name):
    try:
        x, y = p
    except (TypeError, ValueError):
        raise CalibrateError(f"{name} must be two numbers [x, y]") from None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (x, y)):
        raise CalibrateError(f"{name} must be two finite numbers [x, y]")
    return float(x), float(y)


def _axes(rotation, flip_h, flip_v):
    """The drawn x and y directions in the plane's own x, y, as run_canvas makes them: the plane turned `rotation`
    degrees counter-clockwise, then each reversed by its flip."""
    c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    xd, yd = (c, s), (-s, c)
    if flip_h:
        xd = (-xd[0], -xd[1])
    if flip_v:
        yd = (-yd[0], -yd[1])
    return xd, yd


def _clean(v):
    return float(round(v, 12)) + 0.0          # -0.0 and 1e-17 noise out, as the feature's own outputs do


def calibrate(params, p0, p1, distance, centre=False, snap=False):
    """`params` (a Canvas feature's params) scaled so the line p0 -> p1 measures `distance` mm; a new dict, the
    argument is not changed.

    - Width and Height are multiplied by distance / |p1 - p0| about the image centre (X and Y stay);
    - `snap` turns Rotation, about the image centre, by the least angle that puts the line on a multiple of 45 degrees
      in the plane (the line's own direction as drawn, with the flips, is what is measured);
    - `centre` then sets X and Y so the line's midpoint lands on the plane's origin, using the new size and rotation.

    CalibrateError for a point that is not two finite numbers, a line shorter than MIN_LENGTH (1e-6 mm), a distance
    that is not above 0, or a Width, Height, X, Y or Rotation that is not a number (a canvas whose size still
    follows its image has none to scale)."""
    a, b = _point(p0, "p0"), _point(p1, "p1")
    if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) \
            or distance <= 0:
        raise CalibrateError("The distance must be a number above 0")
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < MIN_LENGTH:
        raise CalibrateError("The two points are the same point: draw a line with some length")
    width, height = _number(params, "width"), _number(params, "height")
    x, y = _number(params, "x", 0.0), _number(params, "y", 0.0)
    rotation = _number(params, "rotation", 0.0)
    flip_h, flip_v = bool(params.get("flip_h", False)), bool(params.get("flip_v", False))
    factor = float(distance) / length
    if not (math.isfinite(width * factor) and math.isfinite(height * factor)):
        raise CalibrateError("The scale is too large")
    if snap:
        xd, yd = _axes(rotation, flip_h, flip_v)
        angle = math.degrees(math.atan2(dx * xd[1] + dy * yd[1], dx * xd[0] + dy * yd[0]))   # the line in the plane
        rotation += round(angle / SNAP_STEP) * SNAP_STEP - angle
    if centre:
        xd, yd = _axes(rotation, flip_h, flip_v)
        mx, my = factor * (a[0] + b[0]) / 2, factor * (a[1] + b[1]) / 2          # the midpoint from the centre, scaled
        x = -(mx * xd[0] + my * yd[0])
        y = -(mx * xd[1] + my * yd[1])
    out = dict(params)
    out.update(width=_clean(width * factor), height=_clean(height * factor))
    if centre:
        out.update(x=_clean(x), y=_clean(y))
    if snap:
        out.update(rotation=_clean(rotation))
    return out
