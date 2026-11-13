"""Extend a line onto a spline, and text drawn as glyph outlines in the five callers that used polyline()."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _dist_to_spline(sk, sid, q):
    S = sk.polyline(sid)
    A, D = S[:-1], S[1:] - S[:-1]
    s = np.clip(((q - A) * D).sum(1) / np.maximum((D * D).sum(1), 1e-18), 0, 1)
    return float(np.min(np.linalg.norm(q - (A + s[:, None] * D), axis=1)))


def _scene():
    sk = Sketch()
    cps = [sk.add_point(*q) for q in [(10, -6), (12, -2), (9, 3), (11, 7)]]
    sp = sk.add_spline(cps)
    a, b = sk.add_point(0, 0), sk.add_point(4, 0.5)
    return sk, sp, cps, sk.add_line(a, b), a, b


def test_extend_ends_on_spline_and_stays_on_it():
    sk, sp, cps, ln, a, b = _scene()
    res = OPS.extend(sk, ln, "p2")
    assert res["target"] == sp
    assert _dist_to_spline(sk, sp, sk.P(b)) < 1e-6
    assert np.allclose(sk.P(a), [0, 0])
    sk.points[cps[1]] = [13.5, -1.0]
    sk.points[cps[2]] = [8.0, 4.0]
    assert sk.solve()
    assert _dist_to_spline(sk, sp, sk.P(b)) < 1e-6


def test_extend_shared_end_and_length_held_refuse():
    sk, sp, cps, ln, a, b = _scene()
    sk.add_line(b, sk.add_point(4, 5))
    before = sk.to_dict()
    with pytest.raises(ValueError, match="shared"):
        OPS.extend(sk, ln, "p2")
    assert sk.to_dict() == before

    sk, sp, cps, ln, a, b = _scene()
    sk.add_constraint("distance", a, b, value=float(np.linalg.norm(sk.P(b) - sk.P(a))))
    sk.add_constraint("fix", a)
    sk.solve()
    before = sk.to_dict()
    with pytest.raises(ValueError):
        OPS.extend(sk, ln, "p2")
    assert sk.to_dict() == before


# --- text drawn as glyph outlines in the five callers that used polyline() -------------------------------
# "Hi" at 10 mm: bounding box x 0..9.36, y 0..7.6; H ends at x 6.54 and the i starts at x 8.46, so a rectangle
# over x 7..8, y 7..9 crosses the old bounding-box outline but touches no glyph.

def _text_sketch():
    sk = Sketch()
    return sk, sk.add_text(sk.add_point(0, 0), "Hi", size=10)


class _SpyPolyline:
    """Records which entities a caller asks outlines for, per method."""

    def __init__(self, monkeypatch):
        self.one, self.many = [], []
        real1, realn = Sketch.polyline, Sketch.polylines

        def one(sk, eid, n=48):
            self.one.append(sk.entities[eid]["type"])
            return real1(sk, eid, n)

        def many(sk, eid, n=48):
            self.many.append(sk.entities[eid]["type"])
            return realn(sk, eid, n)
        monkeypatch.setattr(Sketch, "polyline", one)
        monkeypatch.setattr(Sketch, "polylines", many)


def test_sketcher_box_pick_uses_glyphs():
    from test_sketch_inference import VP, _sketcher
    sk, tid = _text_sketch()
    s = _sketcher(sk)

    def pick(u0, v0, u1, v1, crossing):     # camera: 10 px per mm, origin (400, 300), y up
        a, b = (400 + u0 * 10, 300 - v0 * 10), (400 + u1 * 10, 300 - v1 * 10)
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        box = (hi_x, lo_y, lo_x, hi_y) if crossing else (lo_x, lo_y, hi_x, hi_y)
        s.selection = []
        s._box_pick(VP, box, False)
        return tid in s.selection

    assert pick(-1, -1, 11, 9, False)           # window round the whole text picks it
    assert pick(0.5, 0.5, 1.5, 1.5, True)       # crossing the H picks it
    assert not pick(7, 7, 8, 9, True)           # crossing only the old box outline, in the gap: no glyph touched


def test_viewport_box_select_uses_glyphs():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.document import Document
    from freeshape.ui.viewport import Viewport
    from freeshape.ui.camera import basis
    sk, tid = _text_sketch()
    doc = Document()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = sk.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    tid = next(k for k, e in sr.sketch.entities.items() if e["type"] == "text")
    vp = Viewport()
    vp.sketches = [(f["id"], sr, [])]
    vp.user_filter = {"sketch_line"}
    vp.select_filter = None
    vp.cam.set_state((np.array([5.0, 4, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))

    def pick(u0, v0, u1, v1, crossing):
        S = vp.cam.to_screen(np.array([sr.plane.to_world(u0, v0), sr.plane.to_world(u1, v1)], float))[:, :2]
        rect = (S[:, 0].min(), S[:, 1].min(), S[:, 0].max(), S[:, 1].max())
        return any(p.key[1] == tid for p in vp._items_in_rect(rect, crossing))

    assert pick(-1, -1, 11, 9, False)
    assert pick(0.5, 0.5, 1.5, 1.5, True)
    assert not pick(7, 7, 8, 9, True)


def test_bounds_and_pattern_side_ask_for_polylines(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.document import Document
    from freeshape.ui.viewport import Viewport
    from test_sketch_inference import _sketcher
    sk, tid = _text_sketch()
    doc = Document()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = sk.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    vp = Viewport()
    vp.sketches = [(f["id"], sr, [])]
    spy = _SpyPolyline(monkeypatch)
    vp.model_bounds()                                    # the sketch bounds caller
    assert "text" in spy.many and "text" not in spy.one
    spy.one.clear(), spy.many.clear()
    vp.sketcher = _sketcher(sk)
    vp.model_bounds()                                    # the sketch being edited
    assert "text" in spy.many and "text" not in spy.one
