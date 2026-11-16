"""Sketch Mirror as in Onshape: pick the mirror line first, then each entity clicked is mirrored straight away,
the tool stays on until Esc or Enter, and the status line says what to pick next.
Stub viewport: 10 px per mm, origin at (400, 300), y up. The mirror line is the y axis, x = 0."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.sketcher import Sketcher  # noqa: E402


class _Cam:
    scale = 1.0

    def to_screen(self, w):
        return (w[0] * 10 + 400, 300 - w[1] * 10, 0)


class _ViewportStub:
    cam = _Cam()
    status_text = ""

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)

    def update(self):
        pass


class _Ev:
    def __init__(self, key=None):
        self._key = key

    def key(self):
        return self._key

    def modifiers(self):
        return Qt.NoModifier


def _scene():
    """Mirror line on x = 0; an L of two joined lines, a circle, an arc and a point entity, all at x > 0."""
    sk = Sketch()
    ids = {"axis": sk.add_line(sk.add_point(0, -20), sk.add_point(0, 20), True)}
    corner = sk.add_point(10, 0)
    ids["l1"] = sk.add_line(sk.add_point(4, 0), corner)
    ids["l2"] = sk.add_line(corner, sk.add_point(10, 8))
    ids["circle"] = sk.add_circle(sk.add_point(6, 14), 2.0)
    ids["arc"] = sk.add_arc(sk.add_point(16, -10), sk.add_point(19, -10), sk.add_point(16, -7))
    ids["point"] = sk.add_point_entity(sk.add_point(14, 4))
    return sk, ids


def _sketcher(sk):
    s = Sketcher(K.std_plane("Top"), sk, "f1", lambda *a: None)
    s._offer_dims = lambda *a, **k: None
    return s


def _click(s, vp, u, v):
    x, y = u * 10 + 400, 300 - v * 10
    s.mouse_move(vp, x, y, _Ev())
    s.mouse_press(vp, x, y, _Ev())


AT = {"axis": (0, 5), "l1": (7, 0), "l2": (10, 4), "circle": (8, 14),
      "arc": (16 + 3 * np.cos(np.pi / 4), -10 + 3 * np.sin(np.pi / 4)), "point": (14, 4)}


def _sym(sk, axis):
    return {frozenset(c["refs"][:2]) for c in sk.constraints if c["type"] == "symmetric" and c["refs"][2] == axis}


def test_line_first_then_several_entities_each_mirrored_and_the_tool_stays_on():
    sk, ids = _scene()
    s, vp = _sketcher(sk), _ViewportStub()
    s.set_tool("sk_mirror")
    n0 = len(sk.entities)
    _click(s, vp, *AT["axis"])
    assert s.mirror_axis == ids["axis"] and len(s.sk.entities) == n0, "the first pick is the line, nothing mirrored"
    for k, name in enumerate(("l1", "l2", "circle", "arc", "point"), 1):
        _click(s, vp, *AT[name])
        assert len(s.sk.entities) == n0 + k, f"{name} is mirrored as soon as it is clicked"
        assert s.tool == "sk_mirror", "the tool stays on for the next pick"
    sk = s.sk
    circ = [e for e in sk.entities.values() if e["type"] == "circle"]
    assert sorted(round(float(sk.P(e["c"])[0]), 6) for e in circ) == [-6.0, 6.0]
    pts = [e for e in sk.entities.values() if e["type"] == "point"]
    assert any(np.allclose(sk.P(e["p"]), (-14, 4)) for e in pts)
    arcs = [e for e in sk.entities.values() if e["type"] == "arc"]
    assert any(np.allclose(sk.P(e["c"]), (-16, -10)) for e in arcs)
    assert len(_sym(sk, ids["axis"])) >= 8


def test_lines_mirrored_one_at_a_time_stay_joined():
    sk, ids = _scene()
    s, vp = _sketcher(sk), _ViewportStub()
    s.set_tool("sk_mirror")
    for name in ("axis", "l1", "l2"):
        _click(s, vp, *AT[name])
    copies = [e for k, e in s.sk.entities.items() if e["type"] == "line" and k not in ids.values()]
    assert len(copies) == 2
    shared = set(s.sk.entity_points(next(k for k, e in s.sk.entities.items() if e is copies[0]))) & \
        set(s.sk.entity_points(next(k for k, e in s.sk.entities.items() if e is copies[1])))
    assert len(shared) == 1 and np.allclose(s.sk.P(shared.pop()), (-10, 0))


def test_ops_mirror_reuses_an_earlier_mirror_image_of_a_point():
    sk, ids = _scene()
    OPS.mirror(sk, [ids["l1"]], ids["axis"])
    n = len(sk.points)
    OPS.mirror(sk, [ids["l2"]], ids["axis"])
    assert len(sk.points) == n + 1, "only the far end of the second line is new; the corner's image is reused"


def test_status_line_says_what_to_pick_next():
    sk, _ids = _scene()
    s, vp = _sketcher(sk), _ViewportStub()
    s.set_tool("sk_mirror")
    assert s.hint() == "Select the mirror line."
    _click(s, vp, *AT["axis"])
    assert vp.status_text == s.hint() == "Select entities to mirror. Esc or Enter to finish."
    _click(s, vp, *AT["circle"])
    assert vp.status_text == "Select entities to mirror. Esc or Enter to finish."


def test_a_first_pick_that_is_not_a_line_is_refused():
    sk, _ids = _scene()
    s, vp = _sketcher(sk), _ViewportStub()
    s.set_tool("sk_mirror")
    n0 = len(sk.entities)
    _click(s, vp, *AT["circle"])
    assert s.mirror_axis is None and len(s.sk.entities) == n0
    assert vp.status_text.startswith("⚠")


def test_clicking_a_mirrored_entity_or_its_copy_again_does_nothing():
    sk, _ids = _scene()
    s, vp = _sketcher(sk), _ViewportStub()
    s.set_tool("sk_mirror")
    _click(s, vp, *AT["axis"])
    _click(s, vp, *AT["circle"])
    n = len(s.sk.entities)
    _click(s, vp, *AT["circle"])
    _click(s, vp, -AT["circle"][0], AT["circle"][1])          # the copy
    assert len(s.sk.entities) == n


def test_enter_and_esc_end_the_tool():
    for key in (Qt.Key_Return, Qt.Key_Escape):
        sk, _ids = _scene()
        s, vp = _sketcher(sk), _ViewportStub()
        s.set_tool("sk_mirror")
        _click(s, vp, *AT["axis"])
        assert s.key_press(vp, _Ev(key))
        assert s.tool == "select"


def test_geometry_selected_before_the_tool_is_mirrored_when_the_line_is_picked():
    sk, ids = _scene()
    s, vp = _sketcher(sk), _ViewportStub()
    s.selection = [ids["l1"], ids["circle"]]
    s.set_tool("sk_mirror")
    n0 = len(sk.entities)
    _click(s, vp, *AT["axis"])
    assert len(s.sk.entities) == n0 + 2 and s.tool == "sk_mirror"
