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
from freeshape import links as L  # noqa: E402
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


# ---------------------------------------------------------------------------- through the window, in a Document
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from conftest import add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape.ui import drawing_ui as DU  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    w = MW.MainWindow()
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    app.processEvents()
    yield w
    for d in w.docs.values():                       # a dirty tab would ask to save on close
        d.dirty = False
    w.close()
    app.processEvents()
    w.deleteLater()
    app.processEvents()


def _document_with_parts(w, tmp_path):
    """A Document: a Part Studio with a plate and a block, and an Assembly using both with materials."""
    fd = w.create_document(str(tmp_path / "Doc"))
    assert fd is not None
    tab = fd.tabs[0]
    for x0, w_, h in ((0, 60, 8), (100, 20, 20)):
        sid = sketch_feature(tab.doc, lambda s, x0=x0, w_=w_: rect_lines(s, x0, 0, w_, 40))
        add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=h, op="new")
    assert not tab.doc.errors and len(tab.doc.state.parts) == 2
    w.new_assembly()
    asm = w.doc
    ids = list(tab.doc.state.parts)
    for n, pid in enumerate(ids):
        iid = asm.insert(L.studio_source(tab, pid), name=f"P{n}")
        asm.instance(iid)["material"] = ("Steel", "Aluminium")[n]
    asm.refresh(solve=False, notify=False)
    assert not asm.errors and len(asm.bom()) == 2
    return fd, tab, asm


def _pick_first_source(monkeypatch, kind):
    """Drive the real New drawing dialog: choose the first source of `kind` and accept."""
    def run(dlg):
        stack = [dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            stack += [it.child(j) for j in range(it.childCount())]
            idx = it.data(0, DU.ROLE_ID)
            if idx is not None and dlg._sources[int(idx)].get("kind") == kind:
                dlg.tree.setCurrentItem(it)
                return QDialog.Accepted
        raise AssertionError(f"no {kind} source in the picker")
    monkeypatch.setattr(DU.NewDrawingDialog, "exec", run)


def _rows(g):
    return [(r["name"], r["qty"], r.get("material")) for r in g.bom]


def test_assembly_drawing_in_a_document_has_the_parts_list(window, monkeypatch, tmp_path):
    fd, _tab, asm = _document_with_parts(window, tmp_path)
    window.tabs.setCurrentIndex(window._tab_index(asm))
    _pick_first_source(monkeypatch, "assembly")
    window.new_drawing()
    g = window.doc
    assert g.kind == "drawing" and g.source["kind"] == "assembly"
    assert g.bom_resolver == fd.resolver.resolve_drawing_bom
    want = [(r["name"], r["qty"], r["material"]) for r in asm.bom()]
    assert len(want) == 2 and _rows(g) == want
    g.add_bom_table()
    assert any(a["type"] == "bom" for a in g.annots)
    window.save()
    assert window._close_document()
    window.open_document(str(tmp_path / "Doc"))
    g2 = next(d for d in window.docs.values() if d.kind == "drawing")
    assert g2.bom_resolver == window.folder.resolver.resolve_drawing_bom
    g2._update_bom()
    assert _rows(g2) == want and any(a["type"] == "bom" for a in g2.annots)


def test_part_studio_drawing_in_a_document_has_the_parts_list(window, monkeypatch, tmp_path):
    fd, tab, _asm = _document_with_parts(window, tmp_path)
    window.tabs.setCurrentIndex(window._tab_index(tab.doc))
    _pick_first_source(monkeypatch, "studio")
    window.new_drawing()
    g = window.doc
    assert g.kind == "drawing" and g.source["kind"] == "studio"
    assert g.source.get("part") == "*"                  # "All parts" is listed first for a studio of two parts
    assert [r["name"] for r in g.bom] == [p.name for p in tab.doc.state.parts.values()]
    assert all(r["qty"] == 1 for r in g.bom)
    assert g.bom_resolver == fd.resolver.resolve_drawing_bom
    g.add_bom_table()


def test_loose_drawing_with_an_unresolvable_source_has_an_empty_parts_list(window):
    g = DrawingDoc()
    g.source = {"kind": "assembly", "tab": "T9", "name": "Gone"}
    window._add_tab(g)
    assert g.bom == [] and g.bom_resolver == window.resolver.resolve_drawing_bom
