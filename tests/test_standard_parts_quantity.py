"""I3.3: the quantity box of the Standard parts pane. Quantity N with no holes picked inserts N free
instances in one undo step; with holes picked the hole count wins and the field is disabled."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui_helpers  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.standard_insert import hole_targets  # noqa: E402
from freeshape.ui.standard_parts_ui import StandardPartsPane  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

SCREW = "sp:fasteners/iso4762"


def _app():
    return QApplication.instance() or QApplication([])


def _six(doc=None):
    _app()
    doc = doc or A.AssemblyDoc()
    pane = StandardPartsPane(doc)
    pane.select_type(SCREW)
    pane.set_free_size("M4")
    pane.set_free_length(12)
    pane.quantity.setValue(6)
    return doc, pane


def test_quantity_six_is_one_undo_step():
    doc, pane = _six()
    assert pane.quantity.objectName() == "sp_quantity" and pane.quantity.isEnabled()
    pane.insert_btn.click()
    assert len(doc.instances) == 6
    assert len({json_src(i) for i in doc.instances}) == 1
    assert "Inserted 6" in pane.status.text()
    doc.undo()
    assert doc.instances == []


def json_src(inst):
    import json
    return json.dumps(inst["source"], sort_keys=True)


def test_quantity_six_bounding_boxes_do_not_overlap():
    doc, pane = _six()
    pane.insert_btn.click()
    boxes = [K.bbox(doc.world_shape(i["id"])) for i in doc.instances]
    assert len(boxes) == 6
    for a in range(6):
        for b in range(a + 1, 6):
            (alo, ahi), (blo, bhi) = boxes[a], boxes[b]
            assert any(ahi[k] <= blo[k] + 1e-9 or bhi[k] <= alo[k] + 1e-9 for k in range(3)), (a, b)


def test_bom_has_one_row_of_six():
    doc, pane = _six()
    pane.insert_btn.click()
    rows = doc.bom()
    assert len(rows) == 1 and rows[0]["qty"] == 6


def test_quantity_one_still_inserts_one():
    _app()
    doc = A.AssemblyDoc()
    pane = StandardPartsPane(doc)
    pane.select_type(SCREW)
    res = pane.insert()
    assert len(res["instances"]) == 1 and len(doc.instances) == 1


@pytest.fixture
def win(tmp_path):
    w = ui_helpers.plate_assembly_window(tmp_path)
    yield w
    ui_helpers.close_window(w)


def test_two_holes_picked_disable_quantity_and_mate(win):
    w = win
    _app().processEvents()
    if w.asm.insert_panel is None:
        w.asm.toggle_insert()
    pane = w.asm.insert_panel.standard
    w.asm.insert_panel.tabs.setCurrentIndex(1)
    iid = w.doc.instances[0]["id"]
    faces = K.faces(w.doc.parts[iid].shape)
    top = next(i for i, f in enumerate(faces) if "n" in K.face_info(f) and K.face_info(f)["n"][2] > 0.99
               and abs(K.face_info(f)["c"][2] - ui_helpers.PLATE_T) < 1e-6)
    tg = hole_targets(w.doc, [{"inst": iid, "kind": "face", "index": top}])
    m4 = [t for t in tg if abs(float(t["diameter"]) - ui_helpers.M4_HOLE) < 1e-6][:2]
    assert len(m4) == 2
    n0, m0 = len(w.doc.instances), len(w.doc.mates)
    w.viewport.set_selection([Pick("edge", (iid, t["edge"])) for t in m4])
    pane.select_type(SCREW)
    assert not pane.free and not pane.quantity.isEnabled()
    pane.quantity.setValue(6)                       # a disabled field's value must not decide the count
    pane.insert_btn.click()
    assert len(w.doc.instances) == n0 + 2 and len(w.doc.mates) == m0 + 2
