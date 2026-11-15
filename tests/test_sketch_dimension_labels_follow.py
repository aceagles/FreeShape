"""A sketch dimension's label keeps its place relative to the geometry it measures: it goes along when that
geometry is dragged, moved by a solve, undone, or rebuilt, and it stays where the user drags it."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import sketch_ops as OPS  # noqa: E402
from freeshape.sketch import ORIGIN, Sketch  # noqa: E402
from test_sketch_inference import VP, _Ev, _sketcher  # noqa: E402

PX = 10                                           # the stub viewport: 10 px per mm, y up


def _labels(sk):
    """Paint the sketch overlay; {dimension id: centre of its label on screen}."""
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    img = QImage(800, 600, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        sk.draw_overlay(VP, p)
    finally:
        p.end()
    return {cid: np.array([r.center().x(), r.center().y()]) for cid, r in sk._dim_rects.items()}


def _free_rect(s, x=5.0, y=5.0, w=23.0, h=10.0):
    """A rectangle that is free to move as a whole: level and plumb sides, no tie to the origin."""
    P = [s.add_point(x, y), s.add_point(x + w, y), s.add_point(x + w, y + h), s.add_point(x, y + h)]
    L = [s.add_line(P[i], P[(i + 1) % 4]) for i in range(4)]
    for l, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, l)
    assert s.solve()
    return P, L


def _dimensioned_rect():
    """The reported case: a small rectangle with its width (23) under it and its height (10) to its right."""
    sk = _sketcher()
    P, L = _free_rect(sk.sk)
    width = sk._create_dimension(L[0], None, np.array([16.5, 2.0]))
    height = sk._create_dimension(L[1], None, np.array([32.0, 10.0]))
    assert width["value"] == 23 and height["value"] == 10
    return sk, P, L, width, height


def _drag(sk, frm, to):
    sk.mouse_press(VP, frm[0], frm[1], _Ev())
    sk.mouse_drag(VP, (frm[0] + to[0]) / 2, (frm[1] + to[1]) / 2, _Ev())
    sk.mouse_drag(VP, to[0], to[1], _Ev())
    sk.mouse_release(VP, to[0], to[1], _Ev(), True)


def test_labels_go_with_a_dragged_rectangle():
    sk, P, L, width, height = _dimensioned_rect()
    before = _labels(sk)
    corner = sk.sk.P(P[0]).copy()
    _drag(sk, (450, 200), (650, 120))             # the middle of the left side, 20 mm right and 8 mm up
    moved = sk.sk.P(P[0]) - corner
    assert np.allclose(moved, [20, 8], atol=1e-6), moved
    after = _labels(sk)
    for c in (width, height):
        assert np.allclose(after[c["id"]] - before[c["id"]], [20 * PX, -8 * PX], atol=1e-3), c["type"]


def _shapes():
    """One sketch per dimension type: (name, sketch, the dimension, where its label is placed)."""
    out = []

    def one(name, build, pos):
        s = Sketch()
        c = build(s)
        s.place_dim(c, pos)
        out.append(pytest.param(s, c, id=name))

    def line(s, a, b):
        return s.add_line(s.add_point(*a), s.add_point(*b))

    one("length", lambda s: s.add_constraint("distance", line(s, (0, 0), (10, 5)), value=125 ** 0.5), (3, 6))
    one("distance", lambda s: s.add_constraint("distance", s.add_point(1, 1), s.add_point(9, 7), value=10.0), (2, 8))
    one("hdistance", lambda s: s.add_constraint("hdistance", s.add_point(1, 1), s.add_point(9, 7), value=8.0), (5, 12))
    one("vdistance", lambda s: s.add_constraint("vdistance", s.add_point(1, 1), s.add_point(9, 7), value=6.0), (14, 4))
    one("point_to_line", lambda s: s.add_constraint("distance", s.add_point(4, 8), line(s, (0, 0), (10, 0)),
                                                    value=8.0), (7, 4))
    one("diameter", lambda s: s.add_constraint("diameter", s.add_circle(s.add_point(5, 5), 4), value=8.0), (12, 9))
    one("radius", lambda s: s.add_constraint("radius", s.add_arc(s.add_point(5, 5), s.add_point(9, 5),
                                                                s.add_point(5, 9)), value=4.0), (10, 10))
    one("angle", lambda s: s.add_constraint("angle", line(s, (0, 0), (10, 0)), line(s, (0, 0), (8, 6)),
                                            value=36.87), (9, 3))

    s = Sketch()
    _P, L = _free_rect(s, 0, 0, 20, 10)
    OPS.offset(s, L[0], 3)
    (c,) = [k for k in s.constraints if k["type"] == "offset"]
    s.place_dim(c, (12, -1.5))
    out.append(pytest.param(s, c, id="offset"))
    return out


@pytest.mark.parametrize("s,c", _shapes())
def test_every_dimension_type_follows_its_geometry(s, c):
    sk = _sketcher(s)
    c = sk._constraint(c["id"])
    placed = sk.sk.dim_pos(c).copy()
    before = _labels(sk)[c["id"]]
    for pid in sk.sk.points:                      # the whole shape, wherever it ends up: 7 right, 4 down
        if pid != ORIGIN:
            sk.sk.points[pid] = (sk.sk.P(pid) + [7, -4]).tolist()
    assert np.allclose(sk.sk.dim_pos(c) - placed, [7, -4], atol=1e-9)
    assert np.allclose(_labels(sk)[c["id"]] - before, [7 * PX, 4 * PX], atol=1e-3)


def _circle_held_from_the_origin():
    sk = _sketcher()
    s = sk.sk
    centre = s.add_point(20, 0)
    ci = s.add_circle(centre, 4)
    s.add_constraint("horizontal", ORIGIN, centre)
    s.add_constraint("hdistance", ORIGIN, centre, value=20.0)
    assert s.solve()
    dia = sk._create_dimension(ci, None, np.array([26.0, 6.0]))
    return sk, centre, dia


def test_label_follows_a_solve_caused_by_another_dimension_and_undo():
    sk, centre, dia = _circle_held_from_the_origin()
    before = _labels(sk)[dia["id"]]
    assert np.allclose(before, [400 + 26 * PX, 300 - 6 * PX])
    sk.push_undo()
    (hd,) = [c for c in sk.sk.constraints if c["type"] == "hdistance"]
    hd["value"] = 50.0                            # as typing 50 into the other dimension does
    sk.changed()
    assert np.allclose(sk.sk.P(centre), [50, 0], atol=1e-6)
    assert np.allclose(_labels(sk)[dia["id"]] - before, [30 * PX, 0], atol=1e-3)
    sk.undo()
    assert np.allclose(_labels(sk)[dia["id"]], before, atol=1e-3)
    sk.redo()
    assert np.allclose(_labels(sk)[dia["id"]] - before, [30 * PX, 0], atol=1e-3)


def test_label_follows_a_rebuild_from_outside_the_sketcher():
    sk, _centre, dia = _circle_held_from_the_origin()
    saved = sk.sk.to_dict()
    for c in saved["constraints"]:
        if c["type"] == "hdistance":
            c["value"] = 35.0                     # a variable changed while the sketch was closed
    again = _sketcher(Sketch.from_dict(saved))    # opening it solves: the circle is 15 further out
    assert np.allclose(_labels(again)[dia["id"]], [400 + (26 + 15) * PX, 300 - 6 * PX], atol=1e-3)


def test_a_sketch_saved_with_only_pos_draws_where_it_did_and_then_follows():
    sk, centre, dia = _circle_held_from_the_origin()
    old = sk.sk.to_dict()
    for c in old["constraints"]:
        c.pop("pos_anchor", None)                 # how every sketch was saved before the anchor existed
    assert any(c.get("pos") for c in old["constraints"])
    loaded = Sketch.from_dict(old)
    assert loaded.to_dict() == old                # loading alone adds nothing to the file
    (c,) = [c for c in loaded.constraints if c["type"] == "diameter"]
    assert loaded.dim_pos(c).tolist() == c["pos"] == [26.0, 6.0]
    sk2 = _sketcher(Sketch.from_dict(copy.deepcopy(old)))
    assert np.allclose(_labels(sk2)[dia["id"]], [400 + 26 * PX, 300 - 6 * PX], atol=1e-9)     # a diameter's label is at pos
    (hd,) = [c for c in sk2.sk.constraints if c["type"] == "hdistance"]
    hd["value"] = 50.0
    sk2.changed()
    assert np.allclose(_labels(sk2)[dia["id"]], [400 + 56 * PX, 300 - 6 * PX], atol=1e-3)


def test_a_dragged_label_stays_where_it_was_put_and_still_follows():
    sk, _centre, dia = _circle_held_from_the_origin()
    at = _labels(sk)[dia["id"]]
    _drag(sk, at, (700, 100))                     # 30 mm right of the origin, 20 mm up
    assert np.allclose(_labels(sk)[dia["id"]], [700, 100], atol=1e-6)
    again = _sketcher(Sketch.from_dict(sk.sk.to_dict()))          # saved and opened again
    assert np.allclose(_labels(again)[dia["id"]], [700, 100], atol=1e-6)
    (hd,) = [c for c in again.sk.constraints if c["type"] == "hdistance"]
    hd["value"] = 10.0
    again.changed()
    assert np.allclose(_labels(again)[dia["id"]], [600, 100], atol=1e-3)
