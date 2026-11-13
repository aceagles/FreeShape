"""Centre lines on cylinders seen side-on: drawn with the centre marks, on the same layer."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf  # noqa: E402

OVERSHOOT = 2.0                                    # paper mm past each end, as for the centre-mark crosses


def plate():
    box = BRepPrimAPI_MakeBox(60.0, 40.0, 10.0).Shape()
    return K.cut(box, K.cylinder((15, 20, -1), (0, 0, 1), 4.0, 12.0))


def counterbore():
    box = BRepPrimAPI_MakeBox(60.0, 40.0, 10.0).Shape()
    s = K.cut(box, K.cylinder((15, 20, -1), (0, 0, 1), 4.0, 12.0))
    return K.cut(s, K.cylinder((15, 20, 5), (0, 0, 1), 7.0, 6.0))


def drawing_of(shape, view, **extra):
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: shape
    g.refresh(notify=False)
    v = g.add_view(*DIRECTIONS[view], (150, 150), checkpoint=False)
    v.update(extra)
    g._items = None
    g._by_owner = None
    return g, v


def centre_lines(g, v):
    return [it for it in g.items() if it[0] == "line" and it[1] == "CENTER" and it[2] == v["id"]]


def test_through_hole_front_has_mark_top_has_line():
    # "Front" looks along y: the hole axis (z) lies in the view plane
    g, v = drawing_of(plate(), "Front")
    lines = centre_lines(g, v)
    assert len(lines) == 1
    (_k, _l, _o, a, b) = lines[0]
    assert abs(a[0] - b[0]) < 1e-9                                   # vertical on paper (axis is z)
    s = g.view_scale(v)
    assert abs(abs(b[1] - a[1]) - (10 * s + 2 * OVERSHOOT)) < 1e-3  # plate faces plus overshoot each end
    # the other view along the axis: a cross, two lines, no axial line
    g2, v2 = drawing_of(plate(), "Top")
    ls = centre_lines(g2, v2)
    assert len(ls) == 2
    assert {abs(l[3][0] - l[4][0]) < 1e-9 or abs(l[3][1] - l[4][1]) < 1e-9 for l in ls} == {True}
    assert all(abs(np.hypot(l[4][0] - l[3][0], l[4][1] - l[3][1]) - (8 * g2.view_scale(v2) + 4.0)) < 1e-6 for l in ls)


def test_coaxial_holes_give_one_line():
    g, v = drawing_of(counterbore(), "Front")
    lines = centre_lines(g, v)
    assert len(lines) == 1
    a, b = lines[0][3], lines[0][4]
    assert abs(abs(b[1] - a[1]) - (10 * g.view_scale(v) + 2 * OVERSHOOT)) < 1e-3


def test_centre_marks_off_means_no_centre_line():
    g, v = drawing_of(plate(), "Front", centre_marks=False)
    assert centre_lines(g, v) == []


def test_dxf_line_is_on_the_centre_layer(tmp_path):
    import ezdxf
    g, v = drawing_of(plate(), "Front")
    p = str(tmp_path / "c.dxf")
    export_dxf(g, p)
    msp = ezdxf.readfile(p).modelspace()
    layers = {e.dxf.layer for e in msp.query("LINE")}
    assert "CENTER" in layers
    n = [e for e in msp.query("LINE") if e.dxf.layer == "CENTER" and abs(e.dxf.start.x - e.dxf.end.x) < 1e-6]
    assert len(n) >= 1
