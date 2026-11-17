"""I12.2: freeshape/canvas_calibrate.py, the Calibrate of FreeCAD's image plane as a function of numbers. No server and
no kernel: the numerics are plain floats (the test runs on both OCP versions all the same)."""
import math

import pytest

from freeshape import canvas_calibrate as CC
from freeshape.canvas_calibrate import calibrate

CANVAS = {"width": 100.0, "height": 50.0, "x": 3.0, "y": -4.0, "rotation": 0.0, "flip_h": False, "flip_v": False,
          "opacity": 50.0, "blob": "k"}


def near(a, b):
    assert a == pytest.approx(b, abs=1e-9)


def test_a_horizontal_line_of_40_measured_as_50_scales_the_canvas_and_leaves_the_rest():
    got = calibrate(CANVAS, (-20, 7), (20, 7), 50)
    near(got["width"], 125)
    near(got["height"], 62.5)
    assert (got["x"], got["y"], got["rotation"]) == (3.0, -4.0, 0.0)
    assert got["opacity"] == 50.0 and got["blob"] == "k"                # the other params come through
    assert CANVAS["width"] == 100.0                                      # the argument is not changed


def test_centre_puts_the_line_midpoint_on_the_plane_origin():
    got = calibrate(CANVAS, (10, 5), (30, 5), 80, centre=True)         # 20 mm drawn, 80 real: x4; midpoint (20, 5)
    near(got["width"], 400)
    near(got["height"], 200)
    near(got["x"], -80)                                                  # the midpoint is 80, 20 from the centre now
    near(got["y"], -20)


def test_snap_turns_a_line_at_40_degrees_to_45():
    p1 = (10 * math.cos(math.radians(40)), 10 * math.sin(math.radians(40)))
    got = calibrate(CANVAS, (0, 0), p1, 10, snap=True)
    near(got["rotation"], 5)                                             # the canvas turns 5 degrees counter-clockwise
    near(got["width"], 100)
    assert (got["x"], got["y"]) == (3.0, -4.0)
    # a line at 100 degrees goes to 90, a negative one to the nearest multiple the other way
    near(calibrate(CANVAS, (0, 0), (math.cos(math.radians(100)), math.sin(math.radians(100))), 1,
                   snap=True)["rotation"], -10)
    near(calibrate(CANVAS, (0, 0), (math.cos(math.radians(-170)), math.sin(math.radians(-170))), 1,
                   snap=True)["rotation"], -10 + 0)                      # -170 is 10 from -180: turn -10


def test_with_rotation_30_the_centre_option_still_lands_the_midpoint_on_the_origin():
    canvas = dict(CANVAS, rotation=30.0)
    p0, p1, distance = (-5, 2), (15, 12), 60
    got = calibrate(canvas, p0, p1, distance, centre=True)
    f = distance / math.hypot(20, 10)
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    mx, my = f * 5, f * 7                                                # the midpoint (5, 7) after the scaling
    # canvas centre + mx * xdir + my * ydir is the origin, xdir = (c, s), ydir = (-s, c)
    near(got["x"] + mx * c - my * s, 0)
    near(got["y"] + mx * s + my * c, 0)
    assert got["rotation"] == 30.0


@pytest.mark.parametrize("flip_h,flip_v", [(True, False), (False, True), (True, True)])
def test_the_flips_are_in_the_frame_of_the_line(flip_h, flip_v):
    canvas = dict(CANVAS, rotation=20.0, flip_h=flip_h, flip_v=flip_v)
    got = calibrate(canvas, (1, 2), (9, 4), 30, centre=True)
    f = 30 / math.hypot(8, 2)
    c, s = math.cos(math.radians(20)), math.sin(math.radians(20))
    xd, yd = (c, s), (-s, c)
    if flip_h:
        xd = (-c, -s)
    if flip_v:
        yd = (s, -c)
    near(got["x"] + f * 5 * xd[0] + f * 3 * yd[0], 0)
    near(got["y"] + f * 5 * xd[1] + f * 3 * yd[1], 0)
    # snap in a flipped frame: the line as drawn, turned into the plane, ends on a multiple of 45 degrees
    got = calibrate(canvas, (0, 0), (10, 3), 10, snap=True)
    c, s = math.cos(math.radians(got["rotation"])), math.sin(math.radians(got["rotation"]))
    xd, yd = (c, s), (-s, c)
    if flip_h:
        xd = (-c, -s)
    if flip_v:
        yd = (s, -c)
    angle = math.degrees(math.atan2(10 * xd[1] + 3 * yd[1], 10 * xd[0] + 3 * yd[0]))
    near(angle / 45, round(angle / 45))


def test_snap_then_centre_use_the_new_rotation():
    p0, p1 = (0, 0), (10 * math.cos(math.radians(40)), 10 * math.sin(math.radians(40)))
    got = calibrate(CANVAS, p0, p1, 10, centre=True, snap=True)
    c, s = math.cos(math.radians(got["rotation"])), math.sin(math.radians(got["rotation"]))
    mx, my = p1[0] / 2, p1[1] / 2
    near(got["x"] + mx * c - my * s, 0)
    near(got["y"] + mx * s + my * c, 0)
    near(math.degrees(math.atan2(p1[0] * s + p1[1] * c, p1[0] * c - p1[1] * s)), 45)    # the line is at 45 in the plane


def test_equal_points_or_a_line_under_a_micron_are_refused():
    with pytest.raises(CC.CalibrateError, match="same point"):
        calibrate(CANVAS, (4, 4), (4, 4), 10)
    with pytest.raises(CC.CalibrateError):
        calibrate(CANVAS, (0, 0), (5e-7, 0), 10)
    got = calibrate(CANVAS, (0, 0), (2e-6, 0), 1e-6)                   # just over the limit is a line
    near(got["width"], 50)


@pytest.mark.parametrize("bad", [dict(distance=0), dict(distance=-1), dict(distance=float("nan")),
                                 dict(distance=True), dict(distance="5"), dict(p1=(1,)), dict(p1=(1, "a")),
                                 dict(p1=(1, float("inf"))), dict(p0=None)])
def test_bad_input_is_refused_with_a_message(bad):
    args = dict(p0=(0, 0), p1=(1, 0), distance=5)
    args.update(bad)
    with pytest.raises(CC.CalibrateError) as e:
        calibrate(CANVAS, **args)
    assert str(e.value)


def test_a_canvas_without_a_size_cannot_be_calibrated():
    with pytest.raises(CC.CalibrateError, match="width"):
        calibrate(dict(CANVAS, width=None), (0, 0), (1, 0), 2)
    with pytest.raises(CC.CalibrateError, match="height"):
        calibrate(dict(CANVAS, height="#h"), (0, 0), (1, 0), 2)
