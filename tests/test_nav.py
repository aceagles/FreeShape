"""Navigation: mouse styles, wheel zoom, Ctrl+Shift+arrow pan, the N key, Shift+E from a sketch and dialogs that
remember the last folder."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QPoint, QPointF  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui import navstyles as NS  # noqa: E402


def test_style_table():
    L, M, R = Qt.LeftButton, Qt.MiddleButton, Qt.RightButton
    none, shift, ctrl = Qt.NoModifier, Qt.ShiftModifier, Qt.ControlModifier
    assert NS.drag_action("onshape", R, none) == "orbit"
    assert NS.drag_action("onshape", M, none) == "pan"
    assert NS.drag_action("onshape", R, ctrl) == "pan"
    assert NS.drag_action("onshape", L, none) is None              # left drag still box-selects
    assert NS.drag_action("cad", L | M, none) == "orbit"           # a chord
    assert NS.drag_action("blender", M, shift) == "pan"
    assert NS.drag_action("openinventor", L, none) == "orbit"
    assert NS.drag_action("onshape", R, ctrl | shift) is None      # combinations no rule uses
    assert NS.wheel_action("touchpad", none) == "zoom" and NS.wheel_action("touchpad", ctrl) == "zoom"
    assert NS.wheel_action("solidworks", none) == "zoom_reverse"
    assert NS.drag_action("nonsense", R, none) == "orbit"          # unknown style: Onshape
    for st in NS.STYLES.values():
        assert st["label"] and st["rules"]


def test_style_table_inside_a_sketch_and_with_no_button():
    L, R = Qt.LeftButton, Qt.RightButton
    none, shift, ctrl, alt = Qt.NoModifier, Qt.ShiftModifier, Qt.ControlModifier, Qt.AltModifier
    for name in ("gesture", "openinventor", "openscad"):           # styles that orbit with a plain left drag
        assert NS.drag_action(name, L, none, editing=True) is None  # the sketch has the left button
        assert NS.drag_action(name, L, alt, editing=True) == "orbit"
    assert NS.drag_action("touchpad", L, alt, editing=True) is None
    assert NS.drag_action("onshape", R, none, editing=True) == "orbit"   # other buttons are unchanged
    assert NS.hover_action("touchpad", shift) == "pan" and NS.hover_action("touchpad", alt) == "orbit"
    assert NS.hover_action("touchpad", ctrl) == "zoom" and NS.hover_action("touchpad", ctrl | shift) is None
    assert NS.hover_action("touchpad", none) is None and NS.hover_action("onshape", shift) is None
    for name in NS.STYLES:
        h = NS.help_line(name)
        assert h.startswith("Orbit: ") and "Pan: " in h and "Zoom: " in h and "-drag" in h
    assert "Alt+move" in NS.help_line("touchpad") and "Ctrl+move" in NS.help_line("touchpad")


def _window():
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    os.environ["FREESHAPE_NO_PLUGINS"] = "1"          # your own installed plugins stay out of the tests
    from freeshape.ui.mainwindow import MainWindow
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(40, 0), s.add_point(40, 30), s.add_point(0, 30)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=10, op="new")
    doc.add_feature(e)
    doc.hidden_features |= {"Top", "Front", "Right"}
    w = MainWindow()
    w.resize(1200, 800)
    w._add_tab(doc)
    app.processEvents()
    vp = w.viewport
    vp.set_standard_view("Isometric", animate=False)
    vp.fit(animate=False)
    return app, w, vp


def test_n_key_rules():
    app, w, vp = _window()
    asked = []
    real = vp.look_along
    vp.look_along = lambda normal=None, anchor=None, animate=True, axes=(): asked.append(normal)
    s = vp.cam.to_screen(np.array([[20.0, 15.0, 10.0]]))[0]
    vp._cursor_xy = (float(s[0]), float(s[1]))                 # the mouse over the top face
    w._normal_key()
    assert np.allclose(np.abs(asked[-1]), [0, 0, 1])
    vp._cursor_xy = (2.0, 2.0)                                 # over empty space, nothing selected
    vp.clear_selection()
    w._normal_key()
    assert asked[-1] is None                                   # -> look from the other side
    vp.look_along = real
    vp.look_along(np.array([0, 0, 1.0]), animate=False)
    assert np.allclose(vp.cam.R[2], [0, 0, 1], atol=1e-9)      # from the nearer side
    vp.look_along(np.array([0, 0, 1.0]), animate=False)
    assert np.allclose(vp.cam.R[2], [0, 0, -1], atol=1e-9)     # already square to it: the other side
    vp.set_standard_view("Right", animate=False)
    vp.look_along(None, animate=False)
    assert np.allclose(vp.cam.R[2], [-1, 0, 0], atol=1e-9)     # Right becomes Left


def test_wheel_is_capped_and_ctrl_shift_arrow_pans():
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    app, w, vp = _window()

    def wheel(dy):
        vp._zoom_left = 0.0
        ev = QWheelEvent(QPointF(300, 300), QPointF(300, 300), QPoint(0, 0), QPoint(0, dy), Qt.NoButton,
                         Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(vp, ev)
        return vp._zoom_left
    one = wheel(120)
    assert abs(wheel(600) - one) < 1e-12                      # a burst counts as one notch at most
    assert abs(wheel(15) - one / 8) < 1e-12                    # small steps scale with the amount
    vp.zoom_speed = 2.0
    assert abs(wheel(120) - 2 * one) < 1e-12
    vp._zoom_timer.stop()
    t0 = vp.cam.target.copy()
    QTest.keyClick(vp, Qt.Key_Right, Qt.ControlModifier | Qt.ShiftModifier)
    assert not np.allclose(t0, vp.cam.target)


def test_shift_e_from_a_sketch_opens_extrude_with_its_area():
    from freeshape.ui.viewport import Pick
    app, w, vp = _window()
    vp.set_selection([Pick("plane", ("std", "Front"))])
    w.start_sketch()
    sk = w.sketcher
    p = [sk.sk.add_point(x, y) for x, y in [(0, 0), (10, 0), (10, 10), (0, 10)]]
    for a, b in zip(p, p[1:] + p[:1]):
        sk.sk.add_line(a, b)
    sk.changed(True)
    w._feature_from_sketch("extrude")
    assert w.mode == "dialog" and w.dialog.ftype == "extrude"
    assert len(w.dialog.fields["profiles"].items) == 1
    w.cancel_dialog()


def test_dialogs_start_in_the_last_folder(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui import filedlg
    before = filedlg._settings().value(filedlg._KEY, "")
    try:
        os.makedirs(tmp_path / "a", exist_ok=True)
        filedlg.remember(str(tmp_path / "a" / "drawing.dxf"))
        assert filedlg.start_path("part.step") == os.path.join(str(tmp_path / "a"), "part.step")
        full = str(tmp_path / "elsewhere.fshape")
        assert filedlg.start_path(full) == full                # a full path (Save as an existing file) wins
    finally:
        filedlg._settings().setValue(filedlg._KEY, before)      # leave the user's setting as it was


def test_n_squares_the_view_to_the_face_edges():
    app, w, vp = _window()
    vp.set_standard_view("Isometric", animate=False)
    vp.look_along(np.array([0, 0, 1.0]), animate=False, axes=[np.array([0.866, 0.5, 0])])
    R = vp.cam.R
    on_screen = np.array([0.866, 0.5, 0]) @ R[0], np.array([0.866, 0.5, 0]) @ R[1]
    assert min(abs(on_screen[0]), abs(on_screen[1])) < 1e-3          # the edge runs level or upright
    vp.set_standard_view("Isometric", animate=False)
    vp.look_along(np.array([0, 0, 1.0]), animate=False)             # no edges given: lines up with X / Y
    assert min(abs(vp.cam.R[0] @ [1, 0, 0]), abs(vp.cam.R[1] @ [1, 0, 0])) < 1e-9


# ---- carried over from the fork's mouse styles, on upstream's style table: a bare Viewport, no window
import pytest  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402


@pytest.fixture
def vp():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.viewport import Viewport
    v = Viewport()
    v.cam.w, v.cam.h, v.cam.scale = 800, 600, 0.1
    v.orbit_mode = "centre"
    return v


def _wheel(vp, angle=0, pixel=0, mods=Qt.NoModifier, ax=0, px=0):
    e = QWheelEvent(QPointF(400, 300), QPointF(400, 300), QPoint(px, pixel), QPoint(ax, angle),
                    Qt.NoButton, mods, Qt.NoScrollPhase, False)
    vp.wheelEvent(e)


def _settle(vp):
    while vp._zoom_timer.isActive():
        vp._step_zoom()


def _key(vp, key, mods):
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods))


def _move(vp, x, y, mods):
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y), Qt.NoButton, Qt.NoButton, mods))


def test_plain_arrow_still_rotates(vp):
    t0 = vp.cam.target.copy()
    _key(vp, Qt.Key_Right, Qt.NoModifier)
    assert vp._anim is not None
    assert np.allclose(vp.cam.target, t0)


def test_ctrl_shift_arrow_does_not_pan_inside_a_sketch(vp):
    class Sk:
        def key_press(self, *a):
            return False

    t0 = vp.cam.target.copy()
    vp.sketcher = Sk()
    _key(vp, Qt.Key_Right, Qt.ControlModifier | Qt.ShiftModifier)
    assert np.allclose(vp.cam.target, t0)
    vp.sketcher = None
    _key(vp, Qt.Key_Right, Qt.ControlModifier | Qt.ShiftModifier)
    assert not np.allclose(vp.cam.target, t0)


def test_a_wheel_that_only_sends_pixels_still_zooms(vp):
    _wheel(vp, angle=0, pixel=60)
    _settle(vp)
    assert vp.cam.scale < 0.1


def test_touchpad_shift_pans(vp):
    vp.nav_style = "touchpad"
    _move(vp, 100, 100, Qt.ShiftModifier)
    t0 = vp.cam.target.copy()
    _move(vp, 130, 110, Qt.ShiftModifier)
    assert not np.allclose(vp.cam.target, t0)
    assert np.allclose(vp.cam.target - t0, -vp.cam.R[0] * 30 * 0.1 + vp.cam.R[1] * 10 * 0.1)


def test_touchpad_alt_orbits(vp):
    vp.nav_style = "touchpad"
    _move(vp, 100, 100, Qt.AltModifier)
    R0 = vp.cam.R.copy()
    _move(vp, 140, 100, Qt.AltModifier)
    assert not np.allclose(vp.cam.R, R0)


def test_touchpad_ctrl_zooms_and_ctrl_shift_does_nothing(vp):
    vp.nav_style = "touchpad"
    _move(vp, 400, 300, Qt.ControlModifier)
    _move(vp, 400, 320, Qt.ControlModifier)
    assert vp.cam.scale != 0.1
    s0, t0 = vp.cam.scale, vp.cam.target.copy()
    mods = Qt.ControlModifier | Qt.ShiftModifier
    _move(vp, 400, 300, mods)
    _move(vp, 400, 340, mods)
    assert vp.cam.scale == s0 and np.allclose(vp.cam.target, t0)


def test_onshape_style_ignores_modifier_moves(vp):
    t0, R0, s0 = vp.cam.target.copy(), vp.cam.R.copy(), vp.cam.scale
    for m in (Qt.ShiftModifier, Qt.AltModifier, Qt.ControlModifier | Qt.ShiftModifier):
        _move(vp, 100, 100, m)
        _move(vp, 150, 130, m)
    assert np.allclose(vp.cam.target, t0) and np.allclose(vp.cam.R, R0) and vp.cam.scale == s0


def test_touchpad_scroll_zooms_and_so_does_ctrl_scroll(vp):
    vp.nav_style = "touchpad"
    R0 = vp.cam.R.copy()
    _wheel(vp, angle=120, pixel=20)                 # two-finger scroll
    _settle(vp)
    s1 = vp.cam.scale
    assert s1 < 0.1 and np.allclose(vp.cam.R, R0)
    _wheel(vp, angle=120, mods=Qt.ControlModifier)  # pinch
    _settle(vp)
    assert vp.cam.scale < s1


_B = {"L": Qt.LeftButton, "M": Qt.MiddleButton, "R": Qt.RightButton}
_M = {"S": Qt.ShiftModifier, "C": Qt.ControlModifier, "A": Qt.AltModifier}


def _drag(vp, buttons, mods, x0=300, y0=300, x1=340, y1=320):
    """Press `buttons` (in order) with `mods`, move, release. Returns (orbited, panned, zoomed).
    Each drag starts with the camera aimed at the orbit pivot (the origin of this empty scene): an orbit about
    a pivot the camera is not aimed at also moves the target, which would read as a pan after an earlier pan."""
    vp.cam.target = np.zeros(3)
    t0, R0, s0 = vp.cam.target.copy(), vp.cam.R.copy(), vp.cam.scale
    m = Qt.NoModifier
    for c in mods:
        m |= _M[c]
    held = Qt.NoButton
    for c in buttons:
        held |= _B[c]
        vp.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(x0, y0), QPointF(x0, y0), _B[c], held, m))
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x0 + 10, y0 + 5), QPointF(x0 + 10, y0 + 5),
                                  Qt.NoButton, held, m))
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x1, y1), QPointF(x1, y1), Qt.NoButton, held, m))
    for c in reversed(buttons):
        held &= ~_B[c]
        vp.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(x1, y1), QPointF(x1, y1),
                                         _B[c], held, m))
    zoomed = vp.cam.scale != s0
    return (not np.allclose(vp.cam.R, R0), not zoomed and not np.allclose(vp.cam.target, t0), zoomed)


ORBIT, PAN, ZOOM = (True, False, False), (False, True, False), (False, False, True)
NONE = (False, False, False)


def test_drags_follow_the_style_table(vp):
    vp.nav_style = "onshape"
    assert _drag(vp, "R", "") == ORBIT and _drag(vp, "M", "") == PAN and _drag(vp, "R", "C") == PAN
    vp.nav_style = "cad"
    assert _drag(vp, "LM", "") == ORBIT and _drag(vp, "M", "") == PAN
    vp.nav_style = "openscad"
    assert _drag(vp, "L", "") == ORBIT and _drag(vp, "M", "") == ZOOM


@pytest.mark.parametrize("style", ["cad", "blender", "solidworks", "revit"])
def test_left_drag_selects_not_navigates(vp, style):
    vp.nav_style = style
    assert _drag(vp, "L", "") == NONE


def test_left_orbit_styles_keep_left_for_sketch(vp):
    class Sk:
        dragged = 0
        sk = type("S", (), {"constraints": [], "entities": {}})()
        plane = None

        def mouse_press(self, *a):
            return True

        def mouse_drag(self, *a):
            Sk.dragged += 1

        def mouse_release(self, *a):
            pass

    vp.sketcher = Sk()
    for style in ("gesture", "openinventor", "openscad"):
        vp.nav_style = style
        assert _drag(vp, "L", "") == NONE
        assert _drag(vp, "L", "A") == ORBIT       # Alt+left still orbits while a sketch is active
    assert Sk.dragged


def test_left_orbit_style_drag_part_wins(vp):
    class DH:
        moved = 0

        def press(self, *a):
            return True

        def move(self, *a):
            DH.moved += 1

        def release(self, *a):
            pass

    vp.drag_handler = DH()
    vp.nav_style = "openinventor"
    assert _drag(vp, "L", "") == NONE
    assert DH.moved
