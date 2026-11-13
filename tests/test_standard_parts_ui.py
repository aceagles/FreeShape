"""I3.4: the Standard parts tab of the Insert panel (StandardPartsPane).

Widget-level tests use a plain AssemblyDoc and no viewport; the plate tests go through the real MainWindow."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

import ui_helpers  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.standard_parts_ui import StandardPartsPane  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

SCREW = "sp:fasteners/iso4762"
WASHER = "sp:fasteners/iso7089"
TAPPING = "sp:fasteners/iso7049"
HEX = "sp:fasteners/iso4017"


def _app():
    return QApplication.instance() or QApplication([])


def top_face(doc, iid):
    for i, f in enumerate(K.faces(doc.parts[iid].shape)):
        info = K.face_info(f)
        if "n" in info and info["n"][2] > 0.99 and abs(info["c"][2] - ui_helpers.PLATE_T) < 1e-6:
            return i
    raise AssertionError("no top face")


# ------------------------------------------------------------------ the pane on its own
def test_tree_groups_by_category():
    _app()
    pane = StandardPartsPane(A.AssemblyDoc())
    items = pane.type_items()
    assert list(items) == ["Screws", "Nuts", "Washers"]
    assert SCREW in items["Screws"] and WASHER in items["Washers"]
    assert pane.tree.objectName() == "sp_tree"


def test_free_instance_without_selection():
    _app()
    doc = A.AssemblyDoc()
    pane = StandardPartsPane(doc)
    pane.select_type(SCREW)
    assert pane.free and not pane.size.isHidden() and not pane.length.isHidden() and pane.table.isHidden()
    pane.set_free_size("M5")
    pane.set_free_length(20)
    res = pane.insert()
    assert len(res["instances"]) == 1 and not doc.mates
    p = doc.instance(res["instances"][0])["source"]["params"]
    assert p["size"] == "M5" and p["length"] == 20
    assert "Inserted 1" in pane.status.text()


def test_washer_has_no_length_control():
    _app()
    doc = A.AssemblyDoc()
    pane = StandardPartsPane(doc)
    pane.select_type(WASHER)
    assert pane.length.isHidden()
    res = pane.insert()
    assert len(res["instances"]) == 1


def test_tapping_screw_params():
    _app()
    pane = StandardPartsPane(A.AssemblyDoc())
    pane.select_type(TAPPING)
    assert "point" in pane.param_keys() and "standard" not in pane.param_keys()
    assert not pane.params_box.isHidden()
    c_sizes = [pane.size.itemData(i) for i in range(pane.size.count())]
    pane.set_param("point", "F")
    assert pane.base_params()["point"] == "F"
    assert pane.size.count() > 0 and c_sizes
    pane.select_type(HEX)
    assert pane.param_keys() == ["model_thread"]
    pane.select_type(WASHER)
    assert pane.param_keys() == [] and pane.params_box.isHidden()


def test_plan_only_on_changes(monkeypatch):
    _app()
    import freeshape.standard_insert as SI
    n = []
    orig = SI.plan_insert
    monkeypatch.setattr(SI, "plan_insert", lambda *a, **k: n.append(1) or orig(*a, **k))
    doc = A.AssemblyDoc()
    iid = doc.insert_standard_part(WASHER, {"size": "M3"})
    pane = StandardPartsPane(doc)
    pane.set_picks([])
    pane.select_type(SCREW)
    assert n == []                              # nothing selected: no plan to make
    pane.set_picks([{"inst": iid, "kind": "face", "index": 0}])
    assert len(n) == 1
    pane.show()
    pane.repaint()
    assert len(n) == 1


# ------------------------------------------------------------------ through the main window
@pytest.fixture
def win(tmp_path):
    w = ui_helpers.plate_assembly_window(tmp_path)
    yield w
    ui_helpers.close_window(w)


def open_pane(w):
    app = _app()
    app.processEvents()
    if w.asm.insert_panel is None:
        w.asm.toggle_insert()
    app.processEvents()
    panel = w.asm.insert_panel
    assert panel is not None
    panel.tabs.setCurrentIndex(1)
    return panel.standard


def select_top(w):
    iid = w.doc.instances[0]["id"]
    w.viewport.set_selection([Pick("face", (iid, top_face(w.doc, iid)))])
    return iid


def test_plate_rows_insert_and_undo(win):
    w = win
    n0, m0 = len(w.doc.instances), len(w.doc.mates)
    select_top(w)
    pane = open_pane(w)
    assert pane.insert_btn.objectName() == "sp_insert"
    pane.select_type(SCREW)
    assert [(s, n) for s, n, _l in pane.rows()] == [("M3", 8), ("M4", 4)]
    assert all(v is not None for _s, _n, v in pane.rows()) and pane.has_length_column()
    assert not pane.skipped.isHidden() and "1.0" in pane.skipped.text() and "1 hole" in pane.skipped.text()
    m4 = pane.table.cellWidget(1, 2)
    assert isinstance(m4, QComboBox)
    chosen = [m4.itemData(i) for i in range(m4.count())][-1]
    pane.set_row_length("M4", chosen)
    pane.insert_btn.click()
    assert len(w.doc.instances) == n0 + 12 and len(w.doc.mates) == m0 + 12
    assert pane.status.text() == "Inserted 12 Socket head cap screw ISO 4762 in 12 holes"
    assert w.viewport.selection == []
    assert w.asm.insert_panel is panel_of(pane)
    lens = {}
    for inst in w.doc.instances[n0:]:
        p = inst["source"]["params"]
        lens.setdefault(p["size"], set()).add(p["length"])
    assert lens["M4"] == {chosen}
    w.doc.undo()
    assert len(w.doc.instances) == n0 and len(w.doc.mates) == m0


def panel_of(pane):
    return pane.parent().parent().parent().parent()


def test_free_insert_in_window(win):
    w = win
    n0 = len(w.doc.instances)
    pane = open_pane(w)
    pane.select_type(SCREW)
    assert pane.free and pane.table.isHidden() and not pane.size.isHidden()
    pane.set_free_size("M4")
    pane.set_free_length(12)
    pane.insert_btn.click()
    assert len(w.doc.instances) == n0 + 1
    assert w.doc.instances[-1]["source"]["params"]["length"] == 12


def test_selection_before_panel_opens_is_honoured(win):
    w = win
    if w.asm.insert_panel is not None:
        w.asm.close_insert()
    select_top(w)
    pane = open_pane(w)
    pane.select_type(SCREW)
    assert [(s, n) for s, n, _l in pane.rows()] == [("M3", 8), ("M4", 4)]


def test_selection_change_rebuilds(win):
    w = win
    pane = open_pane(w)
    pane.select_type(SCREW)
    assert pane.free
    iid = select_top(w)
    assert [s for s, _n, _l in pane.rows()] == ["M3", "M4"]
    w.viewport.clear_selection()
    assert pane.free and pane.rows() == []
    # one cylindrical-free edge pick that is not a hole: no groups, free mode again
    w.viewport.set_selection([Pick("face", (iid, top_face(w.doc, iid)))])
    assert not pane.free


def test_washer_in_window_has_no_length(win):
    w = win
    select_top(w)
    pane = open_pane(w)
    pane.select_type(WASHER)
    assert not pane.has_length_column() and pane.table.columnCount() == 2
    assert [(s, n) for s, n, _l in pane.rows()] == [("M3", 8), ("M4", 4)]


def test_tapping_point_choice_in_window(win):
    pane = open_pane(win)
    pane.select_type(TAPPING)
    assert not pane.params_box.isHidden()
    assert pane.params_box.findChild(QComboBox, "sp_point") is not None
    assert pane.params_box.findChild(QComboBox, "sp_standard") is None
