"""I12.4: Calibrate in the desktop Canvas dialog: a button, two checkboxes and a two-click mode on the canvas.

Offscreen, `MainWindow(engine=True)`. The window fixture and helpers are I12.3's (tests/test_canvas_desktop.py): no modal
box is opened and the window closes without a save prompt. The 3D view cannot be captured without a display, so the mode is
checked on its state: the line and length the view would draw, the box it opens, and the dialog's params. Each test
builds its own window, so each passes alone by node id."""
import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from freeshape import canvas_calibrate as CC  # noqa: E402
from test_canvas_desktop import (  # noqa: E402,F401 -- `window` is the fixture, the rest are its helpers
    _canvas_features, _empty_studio, _insert, _png, _screen_of, _settle, _type_number, window)

DPI_HALF_MM = 50.8                    # 200 x 100 px at 50.8 dpi: half a millimetre a pixel, a 100 x 50 mm canvas


def _open_canvas(w, tmp_path, keep_aspect=True, **params):
    """A new 100 x 50 mm Canvas dialog on Top in an empty Part Studio, the view looking straight down at 0.2 mm a pixel
    (the canvas is 500 x 250 px), the preview flushed so the view holds the canvas. `params` are set in the dialog."""
    _empty_studio(w)
    path = str(tmp_path / "ruler.png")
    with open(path, "wb") as fh:
        fh.write(_png(200, 100, dpi=DPI_HALF_MM))
    _insert(w, path)
    d = w.dialog
    assert d.feature["params"]["width"] == pytest.approx(100, rel=1e-3)
    assert d.feature["params"]["height"] == pytest.approx(50, rel=1e-3)
    if not keep_aspect:
        d.widgets["keep_aspect"].setChecked(False)
    for key, value in params.items():
        if isinstance(value, bool):
            d.widgets[key].setChecked(value)
        else:
            _type_number(d, key, str(value))
    d._do_preview()
    vp = w.viewport
    vp.resize(640, 480)
    vp.set_standard_view("Top", fit=False, animate=False)
    vp.cam.scale = 0.2
    return d, vp


def _at(vp, u, v):
    """The view point of the picture point (u, v) mm from the canvas centre along its drawn x and y."""
    (c,) = vp.canvases
    world = np.asarray(c["origin"], float) + u * np.asarray(c["xdir"], float) + v * np.asarray(c["ydir"], float)
    sx, sy = _screen_of(vp, world)
    return QPoint(int(round(sx)), int(round(sy)))


def _click(vp, pt, button=Qt.LeftButton, mods=Qt.NoModifier):
    QTest.mouseClick(vp, button, mods, pt)
    QApplication.instance().processEvents()


def _hover(vp, pt, mods=Qt.NoModifier):
    """A mouse move with no button, with `mods` held (QTest.mouseMove cannot hold a key)."""
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(pt), QPointF(pt), Qt.NoButton, Qt.NoButton, mods))


def _start(d):
    d.calibrate_button.click()
    mode = d.main.viewport.calibrating
    assert mode is not None
    return mode


def _two_clicks(vp, a, b, mods=Qt.NoModifier):
    _click(vp, _at(vp, *a))
    _click(vp, _at(vp, *b), mods=mods)


def _enter(mode, text):
    ed = mode.editor
    assert ed is not None
    ed.setText(text)
    ed.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))      # to the box itself, not the focus widget
    QApplication.instance().processEvents()


def _size(d):
    p = d.feature["params"]
    return p["width"], p["height"]


# =========================================================================== the dialog

def test_the_canvas_dialog_has_a_calibrate_button_and_two_checkboxes_off_by_default(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    assert d.calibrate_button.text() == "Calibrate"
    assert d.calibrate_centre.text() == "Centre on midpoint" and not d.calibrate_centre.isChecked()
    assert d.calibrate_snap.text() == "Snap rotation to line" and not d.calibrate_snap.isChecked()
    assert vp.calibrating is None
    w.cancel_dialog()
    w.start_feature("extrude")                        # no other dialog has it
    assert not hasattr(w.dialog, "calibrate_button")
    w.cancel_dialog()


def test_the_button_starts_the_mode_with_a_crosshair_and_a_cancelled_dialog_ends_it(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    mode = _start(d)
    assert vp.cursor().shape() == Qt.CrossCursor and mode.p0 is None and mode.line_screen() is None
    w.cancel_dialog()
    assert vp.calibrating is None and vp.cursor().shape() == Qt.ArrowCursor


# =========================================================================== the gestures

def test_two_clicks_40_mm_apart_then_50_and_enter_scale_to_125_by_62_5_and_one_undo_restores(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    doc = w.doc
    mode = _start(d)
    _click(vp, _at(vp, -20, 0))
    assert mode.p0 == pytest.approx((-20, 0), abs=0.2) and mode.editor is None
    _hover(vp, _at(vp, 5, 0))                         # the line and its live length follow the mouse
    (x0, y0), (x1, y1) = mode.line_screen()
    assert (x0, y0) == pytest.approx((_at(vp, -20, 0).x(), _at(vp, -20, 0).y()), abs=0.6)
    assert (x1, y1) == pytest.approx((_at(vp, 5, 0).x(), _at(vp, 5, 0).y()), abs=0.6)
    assert mode.length() == pytest.approx(25, abs=0.3)
    _click(vp, _at(vp, 20, 0))
    assert mode.fixed and mode.length() == pytest.approx(40, abs=0.3)
    ed = mode.editor                                   # the sketcher's box, at the line's middle, with the length
    assert ed is not None and ed.width() == 110 and float(ed.text()) == pytest.approx(40, abs=0.3)
    mid = _at(vp, 0, 0)
    assert abs(ed.geometry().center().x() - mid.x()) <= 2 and abs(ed.geometry().center().y() - mid.y()) <= 14
    _enter(mode, "50")
    assert vp.calibrating is None and vp.cursor().shape() == Qt.ArrowCursor and mode.editor is None
    assert _size(d) == pytest.approx((125, 62.5), rel=1e-6)
    assert float(d.widgets["width"].text()) == pytest.approx(125) and float(d.widgets["height"].text()) == \
        pytest.approx(62.5)
    assert (d.feature["params"]["x"], d.feature["params"]["y"], d.feature["params"]["rotation"]) == (0, 0, 0)
    d.accept()
    _settle(w)
    (feat,) = _canvas_features(doc)
    assert (feat["params"]["width"], feat["params"]["height"]) == pytest.approx((125, 62.5), rel=1e-6)
    assert w.viewport.canvases[0]["width"] == pytest.approx(125)
    w.undo()                                           # the whole canvas is one step: it is gone, as before it was made
    _settle(w)
    assert _canvas_features(doc) == [] and w.viewport.canvases == []


def test_one_undo_after_calibrating_an_existing_canvas_restores_100_by_50(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    d.accept()
    _settle(w)
    (feat,) = _canvas_features(w.doc)
    w.start_feature("canvas", feat["id"])
    d = w.dialog
    d._do_preview()
    mode = _start(d)
    _two_clicks(w.viewport, (-20, 0), (20, 0))
    _enter(mode, "50")
    assert _size(d) == pytest.approx((125, 62.5), rel=1e-6)
    d.accept()
    _settle(w)
    assert _size_of(w.doc, feat["id"]) == pytest.approx((125, 62.5), rel=1e-6)
    w.undo()
    _settle(w)
    assert _size_of(w.doc, feat["id"]) == pytest.approx((100, 50), rel=1e-3)
    assert w.viewport.canvases[0]["width"] == pytest.approx(100, rel=1e-3)


def _size_of(doc, fid):
    p = doc.feature(fid)["params"]
    return p["width"], p["height"]


def test_a_click_off_the_canvas_does_nothing_and_a_click_on_it_is_not_selected(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    picked = []
    vp.canvasPicked.connect(picked.append)
    mode = _start(d)
    _click(vp, _at(vp, 70, 0))                         # right of the picture (it ends at 50)
    _click(vp, _at(vp, 0, 40))                         # above it (it ends at 25)
    assert mode.p0 is None and mode.editor is None and vp.calibrating is mode
    _click(vp, _at(vp, -20, 0))
    _click(vp, _at(vp, 80, 10))                        # a second click off the canvas: still waiting for it
    assert mode.p0 is not None and not mode.fixed and mode.editor is None
    assert picked == [] and vp.selection == []
    assert _size(d) == pytest.approx((100, 50), rel=1e-3)
    w.cancel_dialog()


def test_esc_leaves_the_size_unchanged_in_both_stages_and_the_dialog_stays_open(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    mode = _start(d)
    _click(vp, _at(vp, -20, 0))
    w._escape()                                        # after the first click
    assert vp.calibrating is None and w.dialog is d and _size(d) == pytest.approx((100, 50), rel=1e-3)
    mode = _start(d)
    _two_clicks(vp, (-20, 0), (20, 0))
    assert mode.editor is not None
    mode.editor.setText("50")
    w._escape()                                        # with the box open and a value typed
    assert vp.calibrating is None and mode.editor is None and w.dialog is d
    assert _size(d) == pytest.approx((100, 50), rel=1e-3)
    mode = _start(d)
    _click(vp, _at(vp, -20, 0))
    # the view's own Esc, handed to the view itself: QTest.keyClick goes to the application's focus widget, which is
    # another window when an earlier test left one shown
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert vp.calibrating is None and w.dialog is d
    w.cancel_dialog()


def test_a_right_click_cancels_the_mode_and_opens_no_menu(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    asked = []
    vp.contextRequested.connect(lambda *a: asked.append(a))
    mode = _start(d)
    _click(vp, _at(vp, -20, 0))
    _click(vp, _at(vp, 20, 0))
    assert mode.editor is not None
    _click(vp, _at(vp, 30, 10), button=Qt.RightButton)
    assert vp.calibrating is None and mode.editor is None and asked == [] and w.dialog is d
    assert _size(d) == pytest.approx((100, 50), rel=1e-3)
    mode = _start(d)
    _click(vp, _at(vp, 10, 10), button=Qt.RightButton)                  # before any click as well
    assert vp.calibrating is None and asked == []
    w.cancel_dialog()


def test_ctrl_snaps_a_7_degree_line_to_5_degrees_live_and_on_the_second_click(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    mode = _start(d)
    _click(vp, _at(vp, -30, -10))
    p0 = mode.p0
    far = _at(vp, -30 + 40 * math.cos(math.radians(7)), -10 + 40 * math.sin(math.radians(7)))
    _hover(vp, far)
    free = math.degrees(math.atan2(mode.p1[1] - p0[1], mode.p1[0] - p0[0]))
    assert free == pytest.approx(7, abs=0.4)
    _hover(vp, far, Qt.ControlModifier)
    n = math.hypot(mode.p1[0] - p0[0], mode.p1[1] - p0[1])
    assert math.degrees(math.atan2(mode.p1[1] - p0[1], mode.p1[0] - p0[0])) == pytest.approx(5, abs=1e-6)
    assert mode.length() == pytest.approx(n) and n == pytest.approx(40, abs=0.4)       # the length is kept
    _click(vp, far, mods=Qt.ControlModifier)
    assert mode.fixed and math.degrees(math.atan2(mode.p1[1] - p0[1], mode.p1[0] - p0[0])) == pytest.approx(5, abs=1e-6)
    (x0, y0), (x1, y1) = mode.line_screen()           # the drawn line is the snapped one
    assert math.degrees(math.atan2(-(y1 - y0), x1 - x0)) == pytest.approx(5, abs=1e-6)
    w.cancel_dialog()


def test_a_second_click_on_the_first_point_makes_no_line(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    mode = _start(d)
    _click(vp, _at(vp, 10, 10))
    _click(vp, _at(vp, 10, 10))
    assert not mode.fixed and mode.editor is None and mode.p0 is not None
    _click(vp, _at(vp, 30, 10))
    assert mode.fixed
    w.cancel_dialog()


def test_a_length_that_is_not_above_zero_keeps_the_box_and_changes_nothing(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    mode = _start(d)
    _two_clicks(vp, (-20, 0), (20, 0))
    for bad in ("0", "-5", "abc", ""):
        _enter(mode, bad)
        assert vp.calibrating is mode and mode.editor is not None and _size(d) == pytest.approx((100, 50), rel=1e-3)
        assert "above 0" in w.status.currentMessage()
    _enter(mode, "100 / 4")                            # a good one still works, as an expression too
    assert vp.calibrating is None and _size(d)[0] == pytest.approx(100 * 25 / 40, rel=1e-3)
    w.cancel_dialog()


def test_the_other_picking_is_off_but_the_middle_button_still_navigates(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path)
    mode = _start(d)
    assert mode.press(10, 10, Qt.MiddleButton, Qt.NoModifier) is False         # left for the view's pan and orbit
    assert mode.press(10, 10, Qt.LeftButton, Qt.NoModifier) is True
    w.cancel_dialog()


# =========================================================================== the mapping

def _drawn_axes(rotation, flip_h, flip_v):
    """The drawn x and y of a canvas on Top in the plane's x, y, from the definition (canvas_calibrate)."""
    xd, yd = CC._axes(rotation, flip_h, flip_v)
    return np.array([xd[0], xd[1], 0.0]), np.array([yd[0], yd[1], 0.0])


@pytest.mark.parametrize("flip_h,flip_v", [(False, False), (True, False), (True, True)])
def test_a_click_maps_to_the_pictures_own_frame_on_a_rotated_and_flipped_canvas(window, tmp_path, flip_h, flip_v):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path, keep_aspect=False, rotation=30, flip_h=flip_h, flip_v=flip_v, x=7, y=-4)
    (c,) = vp.canvases
    xd, yd = _drawn_axes(30, flip_h, flip_v)
    assert np.asarray(c["xdir"]) == pytest.approx(xd, abs=1e-9) and np.asarray(c["ydir"]) == pytest.approx(yd, abs=1e-9)
    mode = _start(d)
    want0, want1 = (-18.0, 6.0), (22.0, 14.0)
    centre = np.array([7.0, -4.0, 0.0])
    for want in (want0, want1):                       # where the picture's point is in the world, from the definition
        world = centre + want[0] * xd + want[1] * yd
        got = mode.to_image(*_screen_of(vp, world))
        assert got[:2] == pytest.approx(want, abs=1e-6) and got[2]
    _two_clicks(vp, want0, want1)
    assert mode.p0 == pytest.approx(want0, abs=0.2) and mode.p1 == pytest.approx(want1, abs=0.2)
    # the corner of the picture the pixel (0, 0) of the image is: the top-left, whatever is flipped, is inside
    assert mode.to_image(*_screen_of(vp, centre + 49.9 * xd))[2] and not mode.to_image(
        *_screen_of(vp, centre + 50.5 * xd))[2]
    w.cancel_dialog()


def test_what_the_dialog_gets_is_what_canvas_calibrate_gives_on_a_rotated_flipped_canvas(window, tmp_path):
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path, keep_aspect=False, rotation=30, flip_h=True, x=7, y=-4)
    d.calibrate_centre.setChecked(True)
    d.calibrate_snap.setChecked(True)
    before = dict(d.feature["params"])
    mode = _start(d)
    _two_clicks(vp, (-18, 6), (22, 14))
    p0, p1 = mode.p0, mode.p1
    _enter(mode, "37.5")
    want = CC.calibrate(before, p0, p1, 37.5, True, True)
    got = d.feature["params"]
    for key in ("width", "height", "x", "y", "rotation"):
        assert got[key] == want[key], key
    assert got["rotation"] != before["rotation"] and got["x"] != before["x"]
    assert float(d.widgets["rotation"].text()) == pytest.approx(got["rotation"], abs=1e-3)
    w.cancel_dialog()


def test_the_desktop_and_the_served_dialog_agree_for_the_same_line_distance_and_checkboxes(window, tmp_path):
    from test_canvas_feature import png as grey_png
    from test_serve_canvas import Server, field, value
    w, _shown = window
    d, vp = _open_canvas(w, tmp_path, keep_aspect=False, rotation=30, flip_h=True, x=7, y=-4)
    d.calibrate_centre.setChecked(True)
    d.calibrate_snap.setChecked(True)
    mode = _start(d)
    _two_clicks(vp, (-18, 6), (22, 14))
    p0, p1 = list(mode.p0), list(mode.p1)
    _enter(mode, "37.5")
    desktop = {k: d.feature["params"][k] for k in ("width", "height", "x", "y", "rotation")}
    w.cancel_dialog()
    srv = Server(tmp_path / "served")
    try:
        up = srv.upload(_png(200, 100, dpi=DPI_HALF_MM))
        got = srv.new(up)
        srv.update(got["dialog"], {"keep_aspect": False, "rotation": 30, "flip_h": True, "x": 7, "y": -4})
        done = srv.calibrate(got["dialog"], {"p0": p0, "p1": p1, "distance": 37.5, "centre": True, "snap": True})
        served = {k: value(done, k) for k in desktop}
    finally:
        srv.close()
    assert desktop == pytest.approx(served, rel=1e-9, abs=1e-9)
