"""Sketching against the model: a drawing tool snaps to the midpoint and ends of a part edge, and an edge is
projected into the sketch once however often it is used or dimensioned to."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_sketch_inference import _Cam, _Ev, _sketcher  # noqa: E402

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

BOX = BRepPrimAPI_MakeBox(20.0, 10.0, 5.0).Shape()       # x 0..20, y 0..10; the sketch is on Top


def _edge(a, b):
    """Index of the box edge running a-b on z = 0."""
    for i, e in enumerate(K.edges(BOX)):
        pts = np.asarray(K.discretize_edge(e), float)
        ends = sorted([tuple(np.round(pts[0], 6)), tuple(np.round(pts[-1], 6))])
        if ends == sorted([tuple(map(float, a)), tuple(map(float, b))]):
            return i
    raise AssertionError("no such edge")


class _Viewport:
    """The headless viewport of test_sketch_inference, with one part whose bottom front edge is always picked."""
    cam = _Cam()
    part_shapes = {"P1": BOX}
    edge = _edge((0, 0, 0), (20, 0, 0))

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)

    def pick(self, x, y, kinds=None):
        return Pick("edge", ("P1", self.edge))


_VP = _Viewport()


def _px(u, v):
    return 400 + u * 10, 300 - v * 10


def _lines(s):
    return {k: e for k, e in s.sk.entities.items() if e["type"] == "line"}


def _press(s, u, v):
    x, y = _px(u, v)
    s.mouse_move(_VP, x, y, _Ev())
    s.mouse_press(_VP, x, y, _Ev())


def test_line_snaps_to_the_midpoint_of_a_part_edge():
    s = _sketcher()
    s.set_tool("line")
    x, y = _px(10.3, 0.2)                           # near the middle of the edge, not on it
    s.mouse_move(_VP, x, y, _Ev())
    assert np.allclose(s.cursor_uv, [10, 0]) and s.snap_info[0] == "edge"
    assert not _lines(s)                            # hovering brings nothing in
    s.mouse_press(_VP, x, y, _Ev())
    (eid, e), = _lines(s).items()
    assert e["construction"] and s._fixed(e["p1"]) and s._fixed(e["p2"])
    mid, = [c for c in s.sk.constraints if c["type"] == "midpoint"]
    assert mid["refs"][1] == eid and np.allclose(s.sk.P(mid["refs"][0]), [10, 0])


def test_line_snaps_to_the_end_of_a_part_edge_and_reuses_the_projected_line():
    s = _sketcher()
    s.set_tool("line")
    _press(s, 10, 0)                                # the midpoint first: the edge comes in
    _press(s, 20.2, 0.1)                            # then its end
    used = [e for e in _lines(s).values() if e["construction"]]
    assert len(used) == 1                           # one projected line, not two
    drawn, = [e for e in _lines(s).values() if not e["construction"]]
    assert drawn["p2"] in (used[0]["p1"], used[0]["p2"]) and np.allclose(s.sk.P(drawn["p2"]), [20, 0])


def test_using_an_edge_twice_gives_one_line():
    s = _sketcher()
    pk = Pick("edge", ("P1", _VP.edge))
    first = s._use_edge(_VP, pk)
    undo = len(s.undo_stack)
    assert s._use_edge(_VP, pk) == first and len(_lines(s)) == 1
    assert len(s.undo_stack) == undo                # nothing changed, nothing to undo
    assert not s.sk.entities[first]["construction"]
    # dimensioning to the edge reuses it and leaves it solid
    assert s._use_edge(_VP, pk, construction=True) == first
    assert len(_lines(s)) == 1 and not s.sk.entities[first]["construction"]


def test_use_gives_an_edge_already_in_the_sketch_the_current_mode():
    s = _sketcher()
    pk = Pick("edge", ("P1", _VP.edge))
    eid = s._use_edge(_VP, pk, construction=True)   # as a dimension brings it in
    assert s.sk.entities[eid]["construction"]
    assert s._use_edge(_VP, pk) == eid              # the Use tool, solid mode
    assert len(_lines(s)) == 1 and not s.sk.entities[eid]["construction"]


def test_using_a_circular_edge_twice_gives_one_circle():
    cyl = K.cylinder((0, 0, 0), (0, 0, 1), 4, 6)
    vp = _Viewport()
    vp.part_shapes = {"P1": cyl}
    ei = next(i for i, e in enumerate(K.edges(cyl)) if "r" in K.edge_info(e))
    s = _sketcher()
    pk = Pick("edge", ("P1", ei))
    first = s._use_edge(vp, pk)
    assert s._use_edge(vp, pk) == first
    assert sum(e["type"] == "circle" for e in s.sk.entities.values()) == 1
