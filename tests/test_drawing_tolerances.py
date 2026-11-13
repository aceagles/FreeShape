"""Dimension tolerances: an optional `tol` entry on a drawing dimension (sym, dev, lim)."""
import json
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf  # noqa: E402

ezdxf = pytest.importorskip("ezdxf")


def make(sheet_scale=1.0, view_scale=None):
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    shape = K.cut(BRepPrimAPI_MakeBox(20, 20, 10).Shape(), K.cylinder((10, 10, -1), (0, 0, 1), 3.0, 12))
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
    lin = g.add_annot({"type": "lin", "view": top["id"], "a": E(lo), "b": E(hi), "orient": "h", "pos": [150, 120]})
    return g, lin


def test_dim_text_and_stacked_parts():
    g, a = make()
    assert g.dim_text(a, 20.0) == "20"
    assert g.dim_parts(a, 20.0) == ("20", None, None)
    a["tol"] = {"type": "sym", "up": 0.1}
    assert g.dim_text(a, 20.0) == "20 ±0.1"
    assert g.dim_parts(a, 20.0)[1:] == (None, None)
    a["tol"] = {"type": "dev", "up": 0.2, "lo": -0.1}
    assert g.dim_parts(a, 20.0) == ("20", "+0.2", "-0.1")
    a["tol"] = {"type": "lim", "up": 0.2, "lo": -0.1}
    assert g.dim_parts(a, 20.0) == ("", "20.2", "19.9")
    a["tol"] = {"type": "lim", "up": 0.2, "lo": -0.1, "dp": 2}
    assert g.dim_parts(a, 20.0) == ("", "20.20", "19.90")
    a["tol"] = {"type": "none"}
    assert g.dim_text(a, 20.0) == "20"


def test_typed_text_ignores_tol():
    g, a = make()
    a["text"] = "TYP"
    for t in ("sym", "dev", "lim"):
        a["tol"] = {"type": t, "up": 0.2, "lo": -0.1}
        assert g.dim_parts(a, 20.0) == ("TYP", None, None)
    items = []
    g._dim_items(items, a)
    assert [it[4] for it in items if it[0] == "text"] == ["TYP"]


def texts(g, a):
    items = []
    g._dim_items(items, a)
    return [it for it in items if it[0] == "text"]


def test_single_line_draws_one_text_item_as_before():
    g, a = make()
    before = texts(g, a)
    assert len(before) == 1
    a["tol"] = {"type": "sym", "up": 0.1}
    t = texts(g, a)
    assert len(t) == 1 and t[0][4] == "20 ±0.1" and t[0][5] == before[0][5]
    assert t[0][3] == before[0][3]
    a["tol"] = {"type": "none"}
    assert texts(g, a) == before


def test_stacked_draws_two_or_three_text_items():
    g, a = make()
    a["tol"] = {"type": "dev", "up": 0.2, "lo": -0.1}
    t = texts(g, a)
    assert [x[4] for x in t] == ["20", "-0.1", "+0.2"]
    assert t[1][5] < t[0][5] and t[2][5] < t[0][5]          # deviations in smaller text
    assert t[2][3][1] > t[1][3][1]                            # upper above lower
    a["tol"] = {"type": "lim", "up": 0.2, "lo": -0.1}
    t = texts(g, a)
    assert [x[4] for x in t] == ["19.9", "20.2"] and t[1][3][1] > t[0][3][1]


def dxf_dim(g, tmp_path, full=False, kinds=(0, 1)):
    path = tmp_path / "t.dxf"
    export_dxf(g, str(path), full_size=full)
    return [e for e in ezdxf.readfile(str(path)).modelspace()
            if e.dxftype() == "DIMENSION" and e.dxf.dimtype & 7 in kinds][0]


@pytest.mark.parametrize("sheet,view,full", [(1.0, None, False), (0.5, 2.0, False), (0.5, 2.0, True)])
def test_dxf_tolerance_values_are_not_scaled(tmp_path, sheet, view, full):
    g, a = make(sheet, view)
    a["tol"] = {"type": "sym", "up": 0.1, "dp": 2}
    ov = dxf_dim(g, tmp_path, full).override()
    assert ov.get("dimtol") == 1 and not ov.get("dimlim")
    assert ov.get("dimtp") == pytest.approx(0.1) and ov.get("dimtm") == pytest.approx(0.1)
    assert ov.get("dimtdec") == 2
    a["tol"] = {"type": "dev", "up": 0.2, "lo": -0.1}
    ov = dxf_dim(g, tmp_path, full).override()
    assert ov.get("dimtol") == 1 and ov.get("dimtp") == pytest.approx(0.2) and ov.get("dimtm") == pytest.approx(0.1)
    a["tol"] = {"type": "lim", "up": 0.2, "lo": -0.1}
    ov = dxf_dim(g, tmp_path, full).override()
    assert ov.get("dimlim") == 1 and not ov.get("dimtol")
    assert ov.get("dimtp") == pytest.approx(0.2) and ov.get("dimtm") == pytest.approx(0.1)


def test_dxf_without_tol_or_with_typed_text_has_no_tolerance(tmp_path):
    g, a = make()
    ov = dxf_dim(g, tmp_path).override()
    assert not ov.get("dimtol") and not ov.get("dimlim") and not ov.get("dimtp")
    a["tol"] = {"type": "sym", "up": 0.1}
    a["text"] = "TYP"
    ov = dxf_dim(g, tmp_path).override()
    assert not ov.get("dimtol") and not ov.get("dimtp")


def test_angular_tolerance_in_degrees(tmp_path):
    import test_drawing_angular_dim as T
    g, top, _ = T.setup(30)
    g.sheet["scale"] = 0.5
    a = g.add_annot(T.ang_spec(g, top, 30, T.inside_pos(g, top)))
    a["tol"] = {"type": "sym", "up": 0.5}
    assert g.dim_text(a, 30.0) == "30° ±0.5"
    ov = dxf_dim(g, tmp_path, kinds=(2,)).override()
    assert ov.get("dimtol") == 1 and ov.get("dimtp") == pytest.approx(0.5) and "dimlfac" not in ov.dimstyle_attribs
    a["tol"] = {"type": "lim", "up": 0.5, "lo": -0.25}
    assert g.dim_parts(a, 30.0) == ("", "30.5°", "29.75°")


def test_saved_tolerance_reloads_and_old_file_loads_unchanged(tmp_path):
    g, a = make()
    a["tol"] = {"type": "dev", "up": 0.2, "lo": -0.1, "dp": 1}
    p = str(tmp_path / "d.fsdrw")
    g.save(p)
    h = DrawingDoc.load(p, resolver=g.resolver)
    assert h.annots[0]["tol"] == {"type": "dev", "up": 0.2, "lo": -0.1, "dp": 1}
    # a file without the key
    a.pop("tol")
    g.save(p)
    assert "tol" not in json.load(open(p))["annots"][0]
    h = DrawingDoc.load(p, resolver=g.resolver)
    assert "tol" not in h.annots[0]
    assert h.dim_text(h.annots[0], 20.0) == "20"


def test_dialog_round_trips_each_type():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.drawing_ui import DimDialog
    g, a = make()
    cases = [None, {"type": "sym", "up": 0.1}, {"type": "dev", "up": 0.2, "lo": -0.1, "dp": 2},
             {"type": "lim", "up": 0.25, "lo": -0.05}]
    for tol in cases:
        b = dict(a)
        b.pop("tol", None)
        if tol:
            b["tol"] = tol
        d = DimDialog(None, g, b)
        assert d.tol_value() == tol
    d = DimDialog(None, g, a)
    d.tol_type.setCurrentIndex(d.tol_type.findData("dev"))
    d.tol_up.setValue(0.3)
    d.tol_lo.setValue(-0.2)
    assert d.tol_value() == {"type": "dev", "up": 0.3, "lo": -0.2}
    assert d.tol_lo.isEnabled()
    d.tol_type.setCurrentIndex(d.tol_type.findData("sym"))
    assert not d.tol_lo.isEnabled() and d.tol_value() == {"type": "sym", "up": 0.3}
