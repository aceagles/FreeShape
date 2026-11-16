"""Sketch shape builders (sketch_ops.rect, center_rect, circle, arc3, arc_center): each adds what the desktop's tool
adds for the same clicks, and the module loads no Qt."""
import math
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402


def _px(u, v):
    """Screen position of sketch point (u, v) in the stub viewport: 10 px per mm, origin at (400, 300), y up."""
    return 400 + u * 10, 300 - v * 10


def _desktop(tool, picks, construction=False, path=()):
    """The sketch after the Sketcher's `tool` is clicked at the sketch points `picks` (moving through `path`, a
    list of points, before the last click)."""
    s = _sketcher()
    s.set_tool(tool)
    s.construction = construction
    for i, (u, v) in enumerate(picks):
        if i == len(picks) - 1:
            for q in path:
                s.mouse_move(VP, *_px(*q), _Ev())
        s.mouse_move(VP, *_px(u, v), _Ev())
        s.mouse_press(VP, *_px(u, v), _Ev())
    return s.sk


def _shape(sk):
    """Entities and constraints as saved, with the solved point positions rounded."""
    d = sk.to_dict()
    pts = {k: [round(float(c), 6) for c in v] for k, v in d["points"].items()}
    return pts, d["entities"], d["constraints"]


def _same(built, desk):
    bp, be, bc = _shape(built)
    dp, de, dc = _shape(desk)
    assert be == de
    assert bc == dc
    assert bp == dp


def test_rect_matches_the_desktop_tool():
    built = Sketch()
    OPS.rect(built, (-5.0, -3.0), (7.0, 4.0))
    _same(built, _desktop("rect", [(-5, -3), (7, 4)]))
    assert [c["type"] for c in built.constraints] == ["horizontal", "horizontal", "vertical", "vertical"]


def test_rect_construction_matches_the_desktop_tool():
    built = Sketch()
    OPS.rect(built, (1.0, 2.0), (-6.0, -4.0), True)
    _same(built, _desktop("rect", [(1, 2), (-6, -4)], construction=True))
    assert all(e.get("construction") for e in built.entities.values())


def test_center_rect_matches_the_desktop_tool():
    built = Sketch()
    OPS.center_rect(built, (2.0, 1.0), (6.0, 4.0))
    _same(built, _desktop("center_rect", [(2, 1), (6, 4)]))


def test_center_rect_has_its_helper_diagonal_and_midpoint():
    sk = Sketch()
    *lines, diag = OPS.center_rect(sk, (2.0, 1.0), (6.0, 4.0))
    assert len(lines) == 4
    e = sk.entities[diag]
    assert e["type"] == "line" and e["construction"] and e["helper"]
    mids = [c for c in sk.constraints if c["type"] == "midpoint"]
    assert len(mids) == 1 and mids[0]["refs"][1] == diag
    assert np.allclose(sk.P(mids[0]["refs"][0]), (2, 1))        # the centre
    assert sorted(sk.P(e[k]).tolist() for k in ("p1", "p2")) == [[-2.0, -2.0], [6.0, 4.0]]


def test_circle_matches_the_desktop_tool():
    built = Sketch()
    OPS.circle(built, (3.0, -2.0), 5.0)
    _same(built, _desktop("circle", [(3, -2), (8, -2)]))


def test_circle_through_the_origin_reuses_that_point():
    built = Sketch()
    sk_ = _sketcher()
    sk_.set_tool("circle")
    for u, v in [(0, 0), (4, 0)]:
        sk_.mouse_move(VP, *_px(u, v), _Ev())
        sk_.mouse_press(VP, *_px(u, v), _Ev())
    cid = OPS.circle(built, (np.zeros(2), "origin"), 4.0)
    assert built.entities[cid]["c"] == "origin" == sk_.sk.entities[next(iter(sk_.sk.entities))]["c"]
    assert not any(k != "origin" for k in built.points)


def test_arc3_counterclockwise_matches_the_desktop_tool():
    built = Sketch()
    OPS.arc3(built, (5.0, 0.0), (-5.0, 0.0), (0.0, 5.0))
    _same(built, _desktop("arc3", [(5, 0), (-5, 0), (0, 5)]))


def test_arc3_clockwise_swaps_its_ends_and_keeps_the_third_point():
    sk = Sketch()
    aid = OPS.arc3(sk, (5.0, 0.0), (-5.0, 0.0), (0.0, -5.0))      # picked clockwise (through the bottom)
    _same(sk, _desktop("arc3", [(5, 0), (-5, 0), (0, -5)]))
    arc = sk.entities[aid]
    assert np.allclose(sk.P(arc["p1"]), (-5, 0)) and np.allclose(sk.P(arc["p2"]), (5, 0))   # swapped
    assert np.allclose(sk.P(arc["c"]), (0, 0))
    mid = next(c["refs"][0] for c in sk.constraints if c["type"] == "point_on" and c["refs"][1] == aid)
    assert np.allclose(sk.P(mid), (0, -5))                                                  # the third pick
    assert mid not in (arc["p1"], arc["p2"], arc["c"])
    assert any(e["type"] == "point" and e["p"] == mid for e in sk.entities.values())       # a real point
    assert OPS._ccw_contains(sk.P(arc["c"]), sk.P(arc["p1"]), sk.P(arc["p2"]), sk.P(mid))   # ccw round the bottom


def test_arc3_in_line_is_refused():
    try:
        OPS.arc3(Sketch(), (0.0, 0.0), (5.0, 0.0), (10.0, 0.0))
    except ValueError as e:
        assert "in line" in str(e)
    else:
        raise AssertionError("expected ValueError")


def _arc_center_pair(sign):
    """(builder sketch, arc id, desktop sketch) for an arc about (2, 1) from (7, 1), ending towards (2, 1 + 8 * sign)."""
    built = Sketch()
    aid = OPS.arc_center(built, (2.0, 1.0), (7.0, 1.0), (2.0, 1.0 + 8 * sign), float(sign))
    path = [(2 + 5 * math.cos(sign * a), 1 + 5 * math.sin(sign * a)) for a in (0.3, 0.6, 0.9, 1.2)]
    return built, aid, _desktop("arc_center", [(2, 1), (7, 1), (2, 1 + 8 * sign)], path=path)


def test_arc_center_counterclockwise_matches_the_desktop_tool():
    built, aid, desk = _arc_center_pair(1)
    _same(built, desk)
    assert np.allclose(built.P(built.entities[aid]["p1"]), (7, 1))
    assert np.allclose(built.P(built.entities[aid]["p2"]), (2, 6))


def test_arc_center_clockwise_matches_the_desktop_tool():
    built, aid, desk = _arc_center_pair(-1)
    _same(built, desk)
    assert np.allclose(built.P(built.entities[aid]["p1"]), (2, -4))       # swapped
    assert np.allclose(built.P(built.entities[aid]["p2"]), (7, 1))


def test_importing_sketch_ops_loads_no_qt():
    code = ("import sys; import freeshape.sketch_ops as O; O.rect; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'OpenGL', 'OCP')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT)
    assert out.stdout.strip() == "[]"
