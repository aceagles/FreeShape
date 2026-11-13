"""DXF dimensions measure the model through the view's own scale, not only the sheet's."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf  # noqa: E402

ezdxf = pytest.importorskip("ezdxf")


def block():
    box = BRepPrimAPI_MakeBox(20, 20, 10).Shape()
    return K.cut(box, K.cylinder((10, 10, -1), (0, 0, 1), 3.0, 12))


def make(sheet_scale, view_scale):
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    shape = block()
    g.resolver = lambda src: shape
    g.sheet["scale"] = sheet_scale
    g.refresh(notify=False)
    top = g.add_view(*DIRECTIONS["Top"], (150, 150), checkpoint=False)
    if view_scale is not None:
        top["scale"] = view_scale
    g.refresh(notify=False)
    sp = [x for x in g.snap_points(top) if x[0] == "vertex"]
    E = lambda t: {"kind": "vertex", "p": [float(c) for c in t[1]], "i": t[3]["i"], "n": t[3]["n"]}
    lo = min(sp, key=lambda t: (t[1][1], t[1][0]))
    hi = max([t for t in sp if abs(t[1][1] - lo[1][1]) < 1e-6], key=lambda t: t[1][0])
    g.add_annot({"type": "lin", "view": top["id"], "a": E(lo), "b": E(hi), "orient": "h", "pos": [150, 120]})
    c, _ax, r, _uv, ci = g.circles_in_view(top)[0]
    g.add_annot({"type": "dia", "view": top["id"], "pos": [170, 170],
                 "a": {"kind": "centre", "p": [float(x) for x in c], "r": r, "i": ci,
                       "n": len(g._snapdata()[1])}})
    return g


def read_dims(g, path, full):
    export_dxf(g, str(path), full_size=full)
    dims = [e for e in ezdxf.readfile(str(path)).modelspace() if e.dxftype() == "DIMENSION"]
    return {d.dxf.dimtype & 7: float(d.get_measurement()) * d.override().get("dimlfac", 1.0) for d in dims}, dims


@pytest.mark.parametrize("sheet,view,full", [(1.0, 2.0, False), (0.5, 2.0, True), (0.5, 2.0, False)])
def test_dimensions_read_model_size_with_view_scale(tmp_path, sheet, view, full):
    g = make(sheet, view)
    vals, dims = read_dims(g, tmp_path / "a.dxf", full)
    assert len(dims) == 2
    lin = [d for d in dims if d.dxf.dimtype & 7 in (0, 1)]
    dia = [d for d in dims if d.dxf.dimtype & 7 == 3]
    meas = lambda d: float(d.get_measurement()) * d.override().get("dimlfac", 1.0)
    assert abs(meas(lin[0]) - 20) < 1e-6
    assert abs(meas(dia[0]) - 6) < 1e-6


def test_views_on_the_sheet_scale_keep_dimlfac(tmp_path):
    for view in (None, 0.5):
        g = make(0.5, view)
        export_dxf(g, str(tmp_path / "s.dxf"), full_size=False)
        for d in ezdxf.readfile(str(tmp_path / "s.dxf")).modelspace():
            if d.dxftype() == "DIMENSION":
                assert abs(d.override().get("dimlfac") - 2.0) < 1e-12
        export_dxf(g, str(tmp_path / "f.dxf"), full_size=True)
        for d in ezdxf.readfile(str(tmp_path / "f.dxf")).modelspace():
            if d.dxftype() == "DIMENSION":
                assert abs(d.override().get("dimlfac") - 1.0) < 1e-12
