"""I4.2: the parts list has a Material column, the row lookup lives in links.LinkResolver (no window needed), and a
drawing source with `exploded` shows the Assembly's exploded view with balloons anchored on the moved parts."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import assembly as A, kernel as K  # noqa: E402
from freeshape import pyfeature  # noqa: E402,F401  (loads the bundled plugins)
from freeshape.drawing import DIRECTIONS, DrawingDoc  # noqa: E402
from freeshape.features import Part  # noqa: E402
from freeshape.links import LinkResolver  # noqa: E402


def T(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


def _assembly(standard=False):
    shapes = {"plate": BRepPrimAPI_MakeBox(60, 40, 8).Shape(), "block": BRepPrimAPI_MakeBox(20, 20, 20).Shape()}
    a = A.AssemblyDoc()
    a.uid = "asm-1"
    a.resolver = lambda src: Part("x", src["part"].title(), shapes[src["part"]], (0.6, 0.7, 0.8), None)
    p = a.insert({"kind": "studio", "uid": "u", "part": "plate"}, T(0, 0, 0), checkpoint=False)
    b = a.insert({"kind": "studio", "uid": "u", "part": "block"}, T(20, 10, 8), checkpoint=False)
    a.instance(p)["material"] = "Steel"
    a.instance(b)["material"] = "Aluminium"
    if standard:
        a.insert_standard_part("sp:fasteners/iso4762", {"size": "M3", "length": 16}, T(30, 20, 28))
    assert not a.errors
    return a, p, b


def _resolver(asm):
    return LinkResolver(find_open_assembly=lambda uid, path: asm if uid == asm.uid else None)


def _drawing(res, src):
    g = DrawingDoc()
    g.source = src
    g.resolver = res.resolve_drawing_source
    g.bom_resolver = res.resolve_drawing_bom
    g.sheet.update(size="A3", scale=1.0)
    g.refresh(notify=False)
    return g


SRC = {"kind": "assembly", "uid": "asm-1", "name": "Asm"}


def test_rows_equal_assembly_bom_without_a_window():
    a, _p, _b = _assembly()
    rows = _resolver(a).resolve_drawing_bom(dict(SRC))
    want = a.bom()
    assert [(r["name"], r["qty"], r["material"], r["mass"]) for r in rows] == \
           [(r["name"], r["qty"], r["material"], r["mass"]) for r in want]
    assert [r["material"] for r in rows] == ["Steel", "Aluminium"] and all(r["shapes"] for r in rows)


def test_table_shows_item_name_qty_material_mass_and_survives_reload(tmp_path):
    a, _p, _b = _assembly(standard=True)
    g = _drawing(_resolver(a), dict(SRC))
    g.add_bom_table()
    texts = [it[4] for it in g.items() if it[0] == "text"]
    for head in ("ITEM", "PART", "QTY", "MATERIAL", "MASS (g)"):
        assert head in texts
    assert "Steel" in texts and "Aluminium" in texts
    std = [r for r in a.bom() if r["source"] == "Standard part"][0]
    assert std["name"] in texts                                         # the standard part's designation is a row
    assert [r.get("material") for r in g.bom] == [r["material"] for r in a.bom()]
    g.save(str(tmp_path / "p.fdwg"))
    g2 = DrawingDoc.load(str(tmp_path / "p.fdwg"))
    assert g2.bom == g.bom and any(x["type"] == "bom" for x in g2.annots)
    assert "MATERIAL" in [it[4] for it in g2.items() if it[0] == "text"]


def test_no_material_means_no_column():
    a, _p, _b = _assembly()
    g = _drawing(_resolver(a), dict(SRC))
    for r in g.bom:
        r.pop("material", None)
    g.add_bom_table()
    assert "MATERIAL" not in [it[4] for it in g.items() if it[0] == "text"]


def test_exploded_source_moves_parts_and_balloons():
    a, p, b = _assembly()
    a.add_explode_step([b], (0, 0, 1), 40, checkpoint=False)
    res = _resolver(a)
    flat = _drawing(res, dict(SRC))
    exp = _drawing(res, dict(SRC, exploded=True))
    lo0, hi0 = K.bbox(flat.shape)
    lo1, hi1 = K.bbox(exp.shape)
    assert hi1[2] - lo1[2] > hi0[2] - lo0[2] + 30                       # wider than assembled
    row = [r for r in exp.bom if r["name"] == "Block"][0]
    blo, _bhi = K.bbox(exp._bom_shapes[exp.bom.index(row)][0])
    assert abs(blo[2] - 48) < 1e-6                                      # 8 + 40: the block sits at its exploded place
    front = exp.add_view(*DIRECTIONS["Front"], (110, 180), checkpoint=False)
    assert exp.auto_balloons(front) == 2
    ball = [x for x in exp.annots if x["type"] == "balloon" and x["item"] == row["item"]][0]
    assert ball["anchor"][2] >= 48 - 1e-6                               # anchored on the moved block


def test_unexploded_source_ignores_explode_steps():
    a, _p, b = _assembly()
    a.add_explode_step([b], (0, 0, 1), 40, checkpoint=False)
    g = _drawing(_resolver(a), dict(SRC))
    _lo, hi = K.bbox(g.shape)
    assert hi[2] < 30
