"""RelationDialog, start_relation and the relation toolbar entries (I2.4)."""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from test_golden_mates import _asm, _face, _src, _up  # noqa: E402
from test_relation_types import _mate, _plus_x  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Status:
    def __init__(self):
        self.msgs = []

    def showMessage(self, text, ms=0):
        self.msgs.append(text)


def _ctl(a):
    from freeshape.ui.assembly_ui import AssemblyController, AssemblyPanel
    from freeshape.ui.viewport import Viewport
    ctl = AssemblyController.__new__(AssemblyController)
    ctl.w = SimpleNamespace(viewport=Viewport(), doc=a, status=_Status(), mode="idle", _adapt_tree=lambda d: None)
    ctl.mate_dialog = ctl.relation_dialog = ctl.insert_panel = None
    ctl.panel = AssemblyPanel()
    ctl.update_triads = lambda: None
    ctl.show_scene = lambda: None
    return ctl


def _setup(*types):
    """A fixed plate with one mated block per type; returns (doc, [mates])."""
    a = _asm()
    ip = a.insert(_src("plate"))
    picks = [_up, _plus_x]
    return a, [_mate(a, t, ip, picks[i % 2]) for i, t in enumerate(types)]


def _open(ctl, rtype, *mids):
    ctl.start_relation(rtype)
    d = ctl.relation_dialog
    for mid in mids:
        d.pick_mate(mid)
    return d


def _positions(a):
    return {i["id"]: [list(r) for r in i["T"]] for i in a.instances}


def test_gear_dialog_adds_relation(qapp):
    a, (m1, m2) = _setup("revolute", "revolute")
    ctl = _ctl(a)
    d = _open(ctl, "gear", m1["id"], m2["id"])
    d.ratio.setText("0.5")
    before = _positions(a)
    d.accept()
    assert ctl.relation_dialog is None and ctl.w.mode == "idle"
    assert len(a.relations) == 1
    r = a.relations[0]
    assert r["type"] == "gear" and r["ratio"] == 0.5 and not r["reverse"]
    assert (r["a"], r["b"]) == ({"mate": m1["id"], "dof": "rz"}, {"mate": m2["id"], "dof": "rz"})
    assert _positions(a) == before


def test_cylindrical_gear_has_no_chooser_planar_linear_has(qapp):
    a, (cyl, pl, sl) = _setup("cylindrical", "planar", "slider")
    ctl = _ctl(a)
    d = _open(ctl, "gear", cyl["id"])
    assert not d.choosers["a"][0].isVisibleTo(d)
    assert d.dof_choice["a"] == "rz"
    d.reject()
    d = _open(ctl, "linear", pl["id"], sl["id"])
    row, combo = d.choosers["a"]
    assert row.isVisibleTo(d)
    assert [combo.itemData(i) for i in range(combo.count())] == ["tx", "ty"]
    assert not d.choosers["b"][0].isVisibleTo(d)
    combo.setCurrentIndex(1)
    assert d.dof_choice["a"] == "ty"
    d.accept()
    assert a.relations[0]["a"] == {"mate": pl["id"], "dof": "ty"}


def test_edit_changes_ratio_in_place_and_moves_nothing(qapp):
    a, (m1, m2) = _setup("revolute", "revolute")
    ctl = _ctl(a)
    _open(ctl, "gear", m1["id"], m2["id"]).accept()
    rid = a.relations[0]["id"]
    before = _positions(a)
    ctl.edit_relation(rid)
    d = ctl.relation_dialog
    assert not d.is_new and d.ratio.text() == "1"
    d.ratio.setText("2")
    d.reverse.setChecked(True)
    d.accept()
    assert [r["id"] for r in a.relations] == [rid]
    assert a.relations[0]["ratio"] == 2.0 and a.relations[0]["reverse"] is True
    assert _positions(a) == before


def test_rack_and_pinion_picked_in_the_wrong_order_is_swapped(qapp):
    a, (rev, sl) = _setup("revolute", "slider")
    ctl = _ctl(a)
    d = _open(ctl, "rack_pinion", sl["id"], rev["id"])
    assert d.ratio_unit.text() == "mm/rev"
    assert (d.sel["a"], d.sel["b"]) == (rev["id"], sl["id"])
    d.ratio.setText("40")
    d.accept()
    r = a.relations[0]
    assert r["a"]["mate"] == rev["id"] and r["b"]["mate"] == sl["id"] and r["ratio"] == 40.0


def test_screw_has_one_mate_field(qapp):
    a, (cyl,) = _setup("cylindrical")
    ctl = _ctl(a)
    d = _open(ctl, "screw", cyl["id"])
    assert not d.fields["b"].isVisibleTo(d) and d.fields["a"].isVisibleTo(d)
    d.ratio.setText("2")
    d.accept()
    r = a.relations[0]
    assert r["a"] == {"mate": cyl["id"], "dof": "rz"} and r["b"] == {"mate": cyl["id"], "dof": "tz"}


def test_type_selector_follows_table_and_unit_label(qapp):
    from freeshape import assembly as A
    a, (m1, m2) = _setup("revolute", "slider")
    d = _open(_ctl(a), "gear")
    assert set(d.type_buttons) == set(A.RELATION_TYPES)
    for t, spec in A.RELATION_TYPES.items():
        d.set_type(t)
        assert d.ratio_unit.text() == spec["ratio_unit"]
        assert d.type_buttons[t].isChecked()


def test_errors_show_in_the_dialog_and_keep_it_open(qapp):
    a, (m1, m2) = _setup("revolute", "slider")
    ctl = _ctl(a)
    d = _open(ctl, "gear", m1["id"])
    d.accept()                                     # second side empty
    assert d.err.text() and not a.relations and ctl.relation_dialog is d
    d.pick_mate(m2["id"])                          # a slider has no rotation
    assert "rotation" in d.err.text() and d.sel["b"] is None
    d.pick_mate(m1["id"])                          # same freedom twice: the model refuses
    d.accept()
    assert "different" in d.err.text() and not a.relations
    d.ratio.setText("abc")
    d.accept()
    assert "number" in d.err.text()
    d.reject()
    assert ctl.relation_dialog is None and ctl.w.mode == "idle" and not a.relations


def test_clicking_a_mate_in_the_list_fills_the_field(qapp):
    a, (m1, m2) = _setup("revolute", "revolute")
    ctl = _ctl(a)
    ctl.panel.mateClicked.connect(ctl._mate_clicked)
    d = _open(ctl, "gear")
    ctl.panel.mateClicked.emit(m2["id"])
    ctl.panel.mateClicked.emit(m1["id"])
    assert (d.sel["a"], d.sel["b"]) == (m2["id"], m1["id"])


def test_toolbar_has_a_button_per_relation_type(qapp):
    from freeshape import assembly as A
    from freeshape.ui.icons import icon
    for t in A.RELATION_TYPES:
        assert not icon("r_" + t).isNull()
