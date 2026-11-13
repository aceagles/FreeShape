"""Sketching against the model: a drawing tool snaps to the corners and midpoint of a part edge, in the sketch
plane or not; an edge is projected into the sketch once however often it is snapped to, used or dimensioned to."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_sketch_inference import _Ev, _sketcher  # noqa: E402

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402

BOX = BRepPrimAPI_MakeBox(20.0, 10.0, 5.0).Shape()       # x 0..20, y 0..10; the sketch is on Top (z = 0)
RAISED = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 5), 20.0, 10.0, 5.0).Shape()   # the same, clear of the sketch plane


def _edge(shape, a, b):
    """Index of the edge of `shape` running a-b."""
    for i, e in enumerate(K.edges(shape)):
        pts = np.asarray(K.discretize_edge(e), float)
        ends = sorted([tuple(np.round(pts[0], 6)), tuple(np.round(pts[-1], 6))])
        if ends == sorted([tuple(map(float, a)), tuple(map(float, b))]):
            return i
    raise AssertionError("no such edge")


class _Cam:
    scale = 1.0

    def to_screen(self, w):                      # 10 px per mm, origin at (400, 300), y up; one point or many
        w = np.asarray(w, float)
        if w.ndim == 2:
            return np.column_stack([w[:, 0] * 10 + 400, 300 - w[:, 1] * 10, np.zeros(len(w))])
        return (w[0] * 10 + 400, 300 - w[1] * 10, 0)


class _Viewport:
    """A headless viewport with one part whose front edge (bottom of BOX, or of RAISED) is always picked."""
    cam = _Cam()

    def __init__(self, shape=BOX, z=0):
        self.part_shapes = {"P1": shape}
        self.edge = _edge(shape, (0, 0, z), (20, 0, z))

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)

    def pick(self, x, y, kinds=None):
        return Pick("edge", ("P1", self.edge))


_VP = _Viewport()


def _px(u, v):
    return 400 + u * 10, 300 - v * 10


def _lines(s):
    return {k: e for k, e in s.sk.entities.items() if e["type"] == "line"}


def _press(s, u, v, vp=_VP):
    x, y = _px(u, v)
    s.mouse_move(vp, x, y, _Ev())
    s.mouse_press(vp, x, y, _Ev())


def test_line_snaps_to_the_midpoint_of_a_part_edge():
    s = _sketcher()
    s.set_tool("line")
    before = set(s.sk.points)
    x, y = _px(10.3, 0.2)                           # near the middle of the edge, not on it
    s.mouse_move(_VP, x, y, _Ev())
    assert np.allclose(s.cursor_uv, [10, 0]) and s.snap_info[0] == "mpt"
    assert not _lines(s) and set(s.sk.points) == before        # hovering brings nothing in
    s.mouse_press(_VP, x, y, _Ev())
    pid, = set(s.sk.points) - before                # a point fixed on the edge's midpoint: no line comes in
    assert np.allclose(s.sk.P(pid), [10, 0]) and s._fixed(pid) and not _lines(s)


def test_line_from_the_midpoint_to_the_end_of_a_part_edge():
    s = _sketcher()
    s.set_tool("line")
    _press(s, 10, 0)                                # the midpoint first
    _press(s, 20.2, 0.1)                            # then the edge's end
    (eid, drawn), = _lines(s).items()               # the drawn line only: nothing was projected
    assert not drawn.get("construction")
    assert np.allclose(s.sk.P(drawn["p1"]), [10, 0]) and np.allclose(s.sk.P(drawn["p2"]), [20, 0])
    assert s._fixed(drawn["p1"]) and s._fixed(drawn["p2"])


def test_snapping_along_an_edge_twice_projects_it_once():
    s = _sketcher()
    x, y = _px(4.0, 0.05)                           # on the edge, away from its corners and middle
    got, snap = s.snap(_VP, x, y)
    assert snap is not None and snap[0] == "medge" and np.allclose(got, [4, 0])
    p1 = s._point_at(got, snap)
    (eid, e), = _lines(s).items()                   # the edge came in as a fixed, hidden construction line
    assert e["construction"] and e.get("helper") and s._fixed(e["p1"]) and s._fixed(e["p2"])
    # the same edge again through the model path: the line already there is used, not a second one on top
    p2 = s._model_point(np.array([15.0, 0.0]), ("medge", ("P1", _VP.edge, "on")))
    assert len(_lines(s)) == 1 and p1 != p2
    on = {tuple(c["refs"]) for c in s.sk.constraints if c["type"] == "point_on"}
    assert on == {(p1, eid), (p2, eid)}
    # the Use tool on that edge shows the helper line as real geometry instead of adding another
    assert s._use_edge(_VP, Pick("edge", ("P1", _VP.edge))) == eid
    assert len(_lines(s)) == 1 and not s.sk.entities[eid].get("helper") and not s.sk.entities[eid]["construction"]


def test_line_snaps_to_an_edge_that_is_not_in_the_sketch_plane():
    vp = _Viewport(RAISED, z=5)                     # the picked edge runs 5 mm above the plane
    s = _sketcher()
    s.set_tool("line")
    x, y = _px(10.3, 0.2)
    s.mouse_move(vp, x, y, _Ev())
    assert np.allclose(s.cursor_uv, [10, 0]) and s.snap_info[0] == "mpt"     # the middle of what it looks like
    s.mouse_press(vp, x, y, _Ev())
    x, y = _px(20.2, 0.1)
    s.mouse_move(vp, x, y, _Ev())
    assert np.allclose(s.cursor_uv, [20, 0]) and s.snap_info[0] == "mpt"     # and its end
    s.mouse_press(vp, x, y, _Ev())
    (eid, drawn), = _lines(s).items()
    assert np.allclose(s.sk.P(drawn["p1"]), [10, 0]) and np.allclose(s.sk.P(drawn["p2"]), [20, 0])
    assert s._fixed(drawn["p1"]) and s._fixed(drawn["p2"])
    s.set_tool("dimension")                         # the dimension tool takes such an edge whole, not a point of it
    assert s._model_point_hit(vp, *_px(10.3, 0.2)) is None


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
    # and so does a snap, which takes no undo step of its own and does not hide the line as a helper
    assert s._use_edge(_VP, pk, construction=True, undo=False) == first
    assert len(s.undo_stack) == undo and not s.sk.entities[first].get("helper")


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
