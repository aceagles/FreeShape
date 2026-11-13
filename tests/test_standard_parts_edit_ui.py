"""I3.5: editing Standard parts in bulk (StandardPartEditDialog), the version marker and the instance menu.

Widget-level tests drive the dialog and AssemblyPanel directly; the plate tests go through the real MainWindow."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import pyfeature  # noqa: E402
from freeshape.pyfeature import registry  # noqa: E402
from freeshape.ui import assembly_ui as AU  # noqa: E402
from freeshape.ui.standard_parts_ui import StandardPartEditDialog  # noqa: E402

SCREW = "sp:fasteners/iso4762"
HEX = "sp:fasteners/iso4017"
WASHER = "sp:fasteners/iso7089"
TAPPING = "sp:fasteners/iso7049"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    problems_before = list(registry._PROBLEMS)
    yield
    registry._PROBLEMS[:] = problems_before
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def screw(asm, size="M3", length=10, type_id=SCREW, **extra):
    return asm.insert_standard_part(type_id, {"size": size, "length": length, **extra})


def dialog(asm, ids):
    _app()
    return StandardPartEditDialog(asm, ids)


def counting(asm, monkeypatch):
    calls = []
    orig = asm.set_standard_params
    monkeypatch.setattr(asm, "set_standard_params", lambda *a, **k: calls.append(a) or orig(*a, **k))
    return calls


def set_combo(combo, value):
    k = combo.findData(value)
    assert k >= 0, (value, [combo.itemData(i) for i in range(combo.count())])
    combo.setCurrentIndex(k)


def params(asm, iid):
    return asm.instance(iid)["source"]["params"]


# ---------------------------------------------------------------------------------------------- fields

def test_one_screw_shows_its_size_length_and_thread():
    asm = A.AssemblyDoc()
    a = screw(asm, "M4", 16)
    d = dialog(asm, [a])
    assert d.size.currentData() == "M4" and d.length.currentData() == 16
    assert d.thread is not None and not d.thread.isTristate() and d.thread.checkState() == Qt.Unchecked
    assert d.findChild(type(d.ok_btn), "spe_ok") is d.ok_btn
    assert d.changes() == {}
    assert 16 in [d.length.itemData(i) for i in range(d.length.count())]


def test_three_screws_of_two_lengths_blank_length_size_shown():
    asm = A.AssemblyDoc()
    ids = [screw(asm, "M3", 10), screw(asm, "M3", 10), screw(asm, "M3", 16)]
    d = dialog(asm, ids)
    assert d.size.currentData() == "M3"
    assert d.length.currentData() is None and d.length.currentText() == ""
    assert d.changes() == {}


def test_mixed_sizes_blank_size_and_length_only_changes_length(monkeypatch):
    asm = A.AssemblyDoc()
    a, b = screw(asm, "M3", 10), screw(asm, "M4", 10)
    calls = counting(asm, monkeypatch)
    d = dialog(asm, [a, b])
    assert d.size.currentData() is None and d.length.currentData() == 10
    set_combo(d.length, 12)
    res = d.apply()
    assert d.changes() == {"length": 12} and calls == [([a, b], {"length": 12})]
    assert sorted(res["changed"]) == sorted([a, b]) and res["rejected"] == {}
    assert (params(asm, a)["size"], params(asm, b)["size"]) == ("M3", "M4")
    assert params(asm, a)["length"] == params(asm, b)["length"] == 12


def test_size_change_with_blank_length_keeps_each_length_and_names_a_rejected_one():
    asm = A.AssemblyDoc()
    a, b = screw(asm, "M3", 10), screw(asm, "M3", 5)    # M4 has no length 5
    d = dialog(asm, [a, b])
    assert d.length.currentData() is None
    # a length list for the size: with the length blank, each keeps its own
    set_combo(d.size, "M4")
    assert d.length.currentData() is None
    res = d.apply()
    assert params(asm, a)["size"] == "M4" and params(asm, a)["length"] == 10
    assert res["changed"] == [a] and list(res["rejected"]) == [b], res
    assert (params(asm, b)["size"], params(asm, b)["length"]) == ("M3", 5)
    name = asm.instance(b)["name"]
    assert name in d.message and name in d.status.text()
    assert asm.instance(a)["name"] not in d.message.split("Not changed:")[1]


def test_length_list_follows_the_chosen_size():
    asm = A.AssemblyDoc()
    d = dialog(asm, [screw(asm, "M3", 10)])
    cls = pyfeature.standard_part(SCREW)()
    got = lambda: [d.length.itemData(i) for i in range(1, d.length.count())]    # noqa: E731
    assert got() == sorted(cls.lengths("M3"))
    set_combo(d.size, "M8")
    assert got() == sorted(cls.lengths("M8")) and got() != sorted(cls.lengths("M3"))


def test_screw_and_washer_offer_only_size():
    asm = A.AssemblyDoc()
    a = screw(asm, "M3", 10)
    w = asm.insert_standard_part(WASHER, {"size": "M3"})
    d = dialog(asm, [a, w])
    assert d.length is None and d.thread is None and d.size.currentData() == "M3"
    assert d.findChild(type(d.size), "spe_length") is None and d.findChild(type(d.thread or d.size), "spe_thread") is None
    set_combo(d.size, "M4")
    res = d.apply()
    assert sorted(res["changed"]) == sorted([a, w]) and params(asm, w)["size"] == "M4"


def test_thread_field_by_type():
    asm = A.AssemblyDoc()
    ids = {t: screw(asm, "M3", 10, t) for t in (SCREW, HEX)}
    ids[TAPPING] = asm.insert_standard_part(TAPPING, {"size": "ST2.9", "length": 9.5}) \
        if "ST2.9" in pyfeature.standard_part(TAPPING)().sizes() else None
    w = asm.insert_standard_part(WASHER, {"size": "M3"})
    assert dialog(asm, [ids[SCREW]]).thread is not None
    assert dialog(asm, [w]).thread is None
    if ids[TAPPING] is None:
        t = pyfeature.standard_part(TAPPING)()
        size = t.sizes()[0]
        ids[TAPPING] = asm.insert_standard_part(TAPPING, {"size": size, "length": t.lengths(size)[0]})
    d = dialog(asm, [ids[TAPPING]])
    assert d.thread is None and d.length is not None
    assert dialog(asm, [ids[SCREW], ids[TAPPING]]).thread is None


def test_mixed_thread_is_tristate_and_untouched_is_not_sent():
    asm = A.AssemblyDoc()
    a = screw(asm, "M3", 10)
    b = screw(asm, "M3", 10, model_thread=True)
    d = dialog(asm, [a, b])
    assert d.thread.isTristate() and d.thread.checkState() == Qt.PartiallyChecked
    assert d.changes() == {}
    d.thread.setCheckState(Qt.Checked)
    assert d.changes() == {"model_thread": True}


def test_ok_makes_one_call_and_one_undo_step(monkeypatch):
    asm = A.AssemblyDoc()
    ids = [screw(asm, "M3", 10) for _ in range(3)]
    calls = counting(asm, monkeypatch)
    depth = len(asm._undo)
    d = dialog(asm, ids)
    set_combo(d.length, 16)
    d.thread.setChecked(True)
    d.accept()
    assert len(calls) == 1 and len(asm._undo) == depth + 1
    assert all(params(asm, i)["length"] == 16 and params(asm, i)["model_thread"] for i in ids)
    asm.undo()
    assert all(params(asm, i)["length"] == 10 and not params(asm, i)["model_thread"] for i in ids)
    assert len(asm._undo) == depth


def test_nothing_set_makes_no_call(monkeypatch):
    asm = A.AssemblyDoc()
    a = screw(asm)
    calls = counting(asm, monkeypatch)
    d = dialog(asm, [a])
    assert d.apply() is None
    d.accept()
    assert calls == []


# ---------------------------------------------------------------------------------------------- list marker, menu

def old_doc(monkeypatch):
    """Two Standard parts saved with the Plugin at 1.0.0, then the Plugin moves to 2.0.0 (returns doc, a, b)."""
    def set_version(v):
        info = registry.plugin("fasteners")
        monkeypatch.setitem(registry._PLUGINS, "fasteners", type(info)(**{**info.__dict__, "version": v}))
    set_version("1.0.0")
    asm = A.AssemblyDoc()
    a, b = screw(asm), screw(asm, "M4", 16)
    set_version("2.0.0")
    return asm, a, b


def item(panel, iid):
    for i in range(panel.instances.count()):
        it = panel.instances.item(i)
        if it.data(AU.ROLE_ID) == iid:
            return it


class FakeCtl(AU.AssemblyController):
    """Just enough of the controller for the menu and its actions: no main window."""

    def __init__(self, doc):
        self._doc = doc
        self.msgs = []
        outer = self

        class W:
            class status:
                showMessage = staticmethod(lambda m, t=0: outer.msgs.append(m))
        self.w = W()

    doc = property(lambda self: self._doc)


def test_marker_tooltip_and_accept_plugin_update(monkeypatch):
    _app()
    asm, a, b = old_doc(monkeypatch)
    p = AU.AssemblyPanel()
    p.populate(asm)
    text = "made with fasteners 1.0.0, rebuilt with 2.0.0"
    for iid in (a, b):
        it = item(p, iid)
        assert AU.NOTICE_MARK.strip() in it.text() and text in it.toolTip() and it.data(AU.ROLE_NOTICE) == text
    ctl = FakeCtl(asm)
    from PySide6.QtWidgets import QMenu
    m = QMenu()
    acts = ctl.add_standard_actions(m, [a])
    assert [x.text() for x in acts] == ["Edit standard part…", "Accept plugin update"]
    acts[1].trigger()
    p.populate(asm)
    assert AU.NOTICE_MARK.strip() not in item(p, a).text() and item(p, a).data(AU.ROLE_NOTICE) is None
    assert AU.NOTICE_MARK.strip() in item(p, b).text()
    assert set(asm.standard_notices()) == {b}
    # an unmarked selection gets no Accept entry
    m2 = QMenu()
    assert [x.text() for x in ctl.add_standard_actions(m2, [a])] == ["Edit standard part…"]


def test_menu_entries_absent_without_a_standard_part():
    _app()
    from PySide6.QtWidgets import QMenu
    asm = A.AssemblyDoc()
    asm.resolver = lambda src: __import__("freeshape.features", fromlist=["Part"]).Part(
        "x", "p", __import__("test_assembly").box(10, 10, 10), (0.7, 0.7, 0.7), None)
    p = asm.insert({"kind": "studio", "uid": "u", "part": "p"})
    menu = QMenu()
    assert FakeCtl(asm).add_standard_actions(menu, [p]) == []
    assert FakeCtl(asm).add_standard_actions(menu, []) == []
    assert menu.actions() == []


def test_unresolved_standard_part_shows_its_error_in_the_list():
    _app()
    asm = A.AssemblyDoc()
    a = screw(asm)
    info = pyfeature.plugin("fasteners") if hasattr(pyfeature, "plugin") else registry.plugin("fasteners")
    asm.errors[a] = f'Standard part {SCREW} of Plugin "fasteners" failed: boom'
    p = AU.AssemblyPanel()
    p.populate(asm)
    it = item(p, a)
    assert asm.errors[a] in it.toolTip() and it.foreground().color().name() in ("#b26a00", "#d93025")
    assert info is not None


def test_unresolved_from_missing_type_through_panel(tmp_path):
    _app()
    import json
    asm = A.AssemblyDoc()
    a = screw(asm)
    path = str(tmp_path / "x.fasm")
    asm.save(path)
    d = json.load(open(path))
    d["instances"][0]["source"]["type"] = "sp:nosuch/thing"
    d["instances"][0]["source"]["plugin"] = "nosuch"
    json.dump(d, open(path, "w"))
    back = A.AssemblyDoc.load(path)
    p = AU.AssemblyPanel()
    p.populate(back)
    it = item(p, a)
    assert "sp:nosuch/thing" in it.toolTip() and it.foreground().color().name() == "#d93025"
    assert AU.NOTICE_MARK.strip() not in it.text()


# ---------------------------------------------------------------------------------------------- real MainWindow

def seat_screws(w, xs, length=10):
    """Insert an M3 x `length` screw at each plate hole centre x,y in `xs`, fastened to the hole's top rim."""
    asm = w.doc
    plate = asm.instances[0]["id"]
    top = max(K.edge_info(e)["center"][2] for e in K.edges(asm.parts[plate].shape) if "center" in K.edge_info(e))
    ids = []
    for x, y in xs:
        idx = next(i for i, e in enumerate(K.edges(asm.parts[plate].shape))
                   if "center" in K.edge_info(e) and np.allclose(K.edge_info(e)["center"], [x, y, top], atol=1e-6))
        s = asm.insert_standard_part(SCREW, {"size": "M3", "length": length})
        m = asm.make_mate("fastened")
        m["a"], m["b"] = asm.connector(plate, "edge", idx), asm.named_connector(s, "under_head")
        asm.add_mate(m)
        ids.append(s)
    return ids


@pytest.fixture
def window(tmp_path):
    import ui_helpers
    w = ui_helpers.plate_assembly_window(tmp_path)
    yield w
    ui_helpers.close_window(w)


def test_eight_screws_through_the_main_window(window):
    w, asm = window, window.doc
    from ui_helpers import plate_holes
    xy = [(x, y) for x, y, d in plate_holes() if abs(d - 3.4) < 1e-9]
    ids = seat_screws(w, xy)
    assert len(ids) == 8 and asm.errors == {}
    w.asm.panel.populate(asm)
    w.asm.panel.select_instances(set(ids))
    assert sorted(w.asm.selected_instance_ids()) == sorted(ids)
    depth = len(asm._undo)
    dlg = w.asm.standard_dialog(ids)
    assert dlg.findChild(type(dlg.size), "spe_size") is dlg.size and dlg.length.objectName() == "spe_length"
    assert dlg.thread.objectName() == "spe_thread" and dlg.ok_btn.objectName() == "spe_ok"
    assert dlg.size.currentData() == "M3" and dlg.length.currentData() == 10
    set_combo(dlg.length, 16)
    dlg.ok_btn.click()
    assert sorted(dlg.result["changed"]) == sorted(ids) and dlg.result["rejected"] == {}
    assert all(params(asm, i)["length"] == 16 for i in ids)
    assert asm.errors == {} and not any(m["id"] in asm.errors for m in asm.mates)
    assert len(asm._undo) == depth + 1
    assert "Changed 8" in w.status.currentMessage()
    asm.undo()
    assert all(params(asm, i)["length"] == 10 for i in ids) and asm.errors == {}


def test_viewport_selection_maps_to_the_edit(window):
    from freeshape.ui.viewport import Pick
    w, asm = window, window.doc
    ids = seat_screws(w, [(15, 15), (40, 15)])
    w.viewport.selection = [Pick("part", (ids[0],)), Pick("part", (ids[1],))]
    std = [i for i in w.asm.selected_instance_ids() if asm.instance(i)["source"]["kind"] == "standard"]
    assert std == ids
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    acts = w.asm.add_standard_actions(menu, w.asm.selected_instance_ids())
    assert [a.text() for a in acts] == ["Edit standard part…"]


def test_plate_alone_has_no_standard_menu_entry(window):
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    assert window.asm.add_standard_actions(menu, [window.doc.instances[0]["id"]]) == []
