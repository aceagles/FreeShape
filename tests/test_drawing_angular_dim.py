"""Angular dimension between two straight edges of a drawing view (type "ang")."""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon  # noqa: E402
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism  # noqa: E402
from OCP.gp import gp_Pnt, gp_Vec  # noqa: E402

from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf  # noqa: E402

ezdxf = pytest.importorskip("ezdxf")

H = 10.0


def wedge(angle):
    """A plate whose corner at (60, 0) is `angle` degrees: edges to (0, 0) and to V + 40 * (-cos, sin)."""
    a = math.radians(angle)
    p = (60 - 40 * math.cos(a), 40 * math.sin(a))
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in ((0, 0), (60, 0), p, (0, p[1])):
        poly.Add(gp_Pnt(x, y, 0))
    poly.Close()
    return BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(poly.Wire()).Face(), gp_Vec(0, 0, H)).Shape(), p


def setup(angle=30.0):
    shape = [wedge(angle)[0]]
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: shape[0]
    g.refresh(notify=False)
    top = g.add_view(*DIRECTIONS["Top"], (150, 150), checkpoint=False)
    g.refresh(notify=False)
    return g, top, shape


def vert(g, top, x, y):
    for kind, P, _xy, ex in g.snap_points(top):
        if kind == "vertex" and abs(P[0] - x) < 1e-6 and abs(P[1] - y) < 1e-6 and abs(P[2] - H) < 1e-6:
            return {"kind": "vertex", "p": [float(c) for c in P], "i": ex["i"], "n": ex["n"]}
    raise AssertionError("no vertex")


def ang_spec(g, top, angle, pos):
    p = wedge(angle)[1]
    V, O, T = vert(g, top, 60, 0), vert(g, top, 0, 0), vert(g, top, *p)
    return {"type": "ang", "view": top["id"], "a": O, "b": V, "c": V, "d": T, "pos": pos}


def inside_pos(g, top):
    """A paper position inside the corner (between the two edges, near the vertex)."""
    uv = g.project(top, np.array([60 - 20.0, 3.0, H]))
    return [float(c) for c in g.to_paper(top, uv)]


def test_value_30_and_opposite_quadrant_150():
    g, top, _ = setup(30)
    pos = inside_pos(g, top)
    a = g.add_annot(ang_spec(g, top, 30, pos))
    kind, _data, val = g.dim_geometry(a)
    assert kind == "ang" and abs(val - 30) < 1e-6
    assert g.dim_text(a, val) == "30°"
    V = g.to_paper(top, g.project(top, np.array([60.0, 0, H])))
    # the quadrant across the vertex (vertical angle) measures the same angle
    a["pos"] = [float(c) for c in (2 * V - np.asarray(pos))]
    assert abs(g.dim_geometry(a)[2] - 30) < 1e-6
    # the quadrants that share one edge with it measure the supplement: across the bottom edge, and across the
    # slanted edge
    below = g.to_paper(top, g.project(top, np.array([40.0, -3.0, H])))
    a["pos"] = [float(c) for c in below]
    assert abs(g.dim_geometry(a)[2] - 150) < 1e-6
    beyond = g.to_paper(top, g.project(top, np.array([60 - 20 * math.cos(math.radians(30)) - 3,
                                                      10 + 3, H])))
    a["pos"] = [float(c) for c in beyond]
    assert abs(g.dim_geometry(a)[2] - 150) < 1e-6


def test_follows_the_model_and_is_not_lost():
    g, top, shape = setup(30)
    a = g.add_annot(ang_spec(g, top, 30, inside_pos(g, top)))
    assert abs(g.dim_geometry(a)[2] - 30) < 1e-6
    shape[0] = wedge(40)[0]
    g.refresh(notify=False)
    assert abs(g.dim_geometry(a)[2] - 40) < 1e-6
    assert not any(isinstance(a.get(k), dict) and a[k].get("lost") for k in ("a", "b", "c", "d"))
    items = []
    g._dim_items(items, a)
    assert {"arc", "tri", "text"} <= {it[0] for it in items} and all(it[1] == "DIM" for it in items)
    assert [it for it in items if it[0] == "text"][0][4] == "40°"


def test_dxf_has_one_angular_dimension_of_30_degrees(tmp_path):
    g, top, _ = setup(30)
    g.sheet["scale"] = 0.5
    g.add_annot(ang_spec(g, top, 30, inside_pos(g, top)))
    for full in (False, True):
        path = tmp_path / f"a{full}.dxf"
        export_dxf(g, str(path), full_size=full)
        dims = [e for e in ezdxf.readfile(str(path)).modelspace() if e.dxftype() == "DIMENSION"]
        assert len(dims) == 1 and dims[0].dxf.dimtype & 7 == 2
        assert abs(dims[0].get_measurement() - 30) < 1e-6
        assert "dimlfac" not in dims[0].override().dimstyle_attribs


def test_dxf_falls_back_to_lines_when_the_real_dimension_fails(tmp_path, monkeypatch):
    import freeshape.drawing as DW
    g, top, _ = setup(30)
    g.add_annot(ang_spec(g, top, 30, inside_pos(g, top)))
    monkeypatch.setattr(DW, "_ezdxf_dim", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    path = tmp_path / "f.dxf"
    export_dxf(g, str(path))
    msp = ezdxf.readfile(str(path)).modelspace()
    assert not [e for e in msp if e.dxftype() == "DIMENSION"]
    assert any(e.dxftype() == "TEXT" and e.dxf.text == "30°" for e in msp)


def test_save_and_reload_round_trip(tmp_path):
    g, top, _ = setup(30)
    a = g.add_annot(ang_spec(g, top, 30, inside_pos(g, top)))
    g.save(str(tmp_path / "a.fsdraw"))
    g2 = DrawingDoc.load(str(tmp_path / "a.fsdraw"), resolver=g.resolver)
    assert [x["type"] for x in g2.annots] == ["ang"] and g2.annots[0]["c"] == a["c"]


def test_dimension_tool_creates_it_without_the_refusal():
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.drawing_ui import DrawingController
    QApplication.instance() or QApplication([])
    g, top, _ = setup(30)
    p = wedge(30)[1]
    V, O, T = vert(g, top, 60, 0), vert(g, top, 0, 0), vert(g, top, *p)
    xy = lambda x, y: g.to_paper(top, g.project(top, np.array([x, y, H])))
    snaps = [{"type": "line", "view": top["id"], "a": xy(0, 0), "b": xy(60, 0), "p3": [O, V]},
             {"type": "line", "view": top["id"], "a": xy(60, 0), "b": xy(*p), "p3": [V, T]}]
    it = iter(snaps)
    ctl = DrawingController(SimpleNamespace(doc=g, dwg_tool_changed=lambda t: None))
    ctl.tool = "dim"
    ctl._find_snap = lambda x, y: next(it, None)
    pos = inside_pos(g, top)
    ctl._dim_click(0, 0, pos)
    ctl._dim_click(0, 0, pos)
    assert not ctl.hint.startswith("⚠") and "supported yet" not in ctl.hint
    ctl._dim_click(0, 0, pos)                       # the placement click
    assert [x["type"] for x in g.annots] == ["ang"]
    assert abs(g.dim_geometry(g.annots[0])[2] - 30) < 1e-6
