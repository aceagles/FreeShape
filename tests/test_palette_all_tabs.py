"""Search tools (the command palette) in every tab kind and while sketching (I3.12). Offscreen; nothing modal runs."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from freeshape.drawing import DrawingDoc  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from freeshape.variable_studio import VariableStudioDoc  # noqa: E402
from test_assembly import box, make, src  # noqa: E402

FEATURES = {"Extrude", "Fillet", "Revolve", "Sweep", "Loft", "Chamfer", "Shell", "Hole", "Boolean", "Sketch",
            "Delete part"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def w(app):
    from freeshape.ui.mainwindow import MainWindow
    win = MainWindow()
    app.processEvents()
    return win


def names(win):
    return [c.name for c in win._palette_commands()]


def run(win, name):
    cmd = next(c for c in win._palette_commands() if c.name == name)
    cmd.action()
    QApplication.processEvents()
    return cmd


def assembly(win, with_parts=True):
    lib = {"block": box(10, 10, 10), "plate": box(40, 40, 5)}
    a = make(lib)
    ids = []
    if with_parts:
        ids = [a.insert(src("block"), T=np.array([[1, 0, 0, 20 * k], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float))
               for k in range(3)]
    win._add_tab(a)
    return a, ids


def drawing(win):
    g = DrawingDoc()
    g.name = "Drawing T"
    win._add_tab(g)
    return g


def test_part_studio_list_keeps_its_entries(w):
    got = names(w)
    assert got[:3] == ["Sketch", "Extrude", "Revolve"]
    assert {"Extrude", "Fillet", "Delete part", "Section view", "Export"} <= set(got)
    assert "Revolute" not in got and "Polygon" not in got


def test_assembly_lists_its_commands_and_no_feature(w):
    assembly(w)
    got = names(w)
    for n in ("Insert", "Revolute", "Pin slot", "Parallel", "Linear relation", "Group", "Animate", "Fix", "Suppress",
              "Unsuppress", "Hide", "Isolate", "Show all", "Interference", "Bill of materials", "Mass properties",
              "Section view", "Front view", "Isometric view", "Export"):
        assert n in got, n
    assert not FEATURES & set(got)
    assert [c.name for c in search_commands(w._palette_commands(), "joint")][:1] == ["Fastened"]


def test_assembly_revolute_starts_the_mate_tool_in_that_type(w):
    a, ids = assembly(w)
    run(w, "Revolute")
    assert w.mode == "dialog" and w.asm.mate_dialog.mate["type"] == "revolute"
    w.asm.mate_dialog.reject()
    assert w.mode == "idle"


def test_assembly_bill_of_materials_opens_the_table(w, monkeypatch):
    from freeshape.ui import assembly_ui
    opened = []
    monkeypatch.setattr(assembly_ui.BomDialog, "exec", lambda self: opened.append(self.csv()) or 0)
    assembly(w)
    run(w, "Bill of materials")
    assert len(opened) == 1 and "block" in opened[0]


def test_assembly_mass_properties_uses_the_whole_assembly(w, monkeypatch):
    called = []
    monkeypatch.setattr(w, "_mass_props_assembly", lambda doc: called.append(doc))
    a, _ = assembly(w)
    run(w, "Mass properties")
    assert called == [a]


def test_assembly_suppress_hide_isolate_act_on_the_selection(w):
    a, ids = assembly(w)
    run(w, "Suppress")                       # nothing selected: a status message, no error
    assert "Select a part" in w.status.currentMessage() and not any(i.get("suppressed") for i in a.instances)
    w.asm.panel.select_instances({ids[0]})
    run(w, "Suppress")
    assert [bool(i.get("suppressed")) for i in a.instances] == [True, False, False]
    run(w, "Unsuppress")
    assert not any(i.get("suppressed") for i in a.instances)
    run(w, "Hide")
    assert [bool(i.get("hidden")) for i in a.instances] == [True, False, False]
    a.undo()
    assert not any(i.get("hidden") for i in a.instances)
    run(w, "Isolate")
    assert [bool(i.get("hidden")) for i in a.instances] == [False, True, True]
    run(w, "Show all")
    assert not any(i.get("hidden") for i in a.instances)


def test_assembly_fix_toggles_the_selected_part(w):
    a, ids = assembly(w)
    w.asm.panel.select_instances({ids[1]})
    run(w, "Fix")
    assert a.instance(ids[1]).get("fixed") is True


def test_assembly_animate_needs_a_mate_and_says_so(w):
    assembly(w)
    run(w, "Animate")
    assert "mate" in w.status.currentMessage() and w.mode == "idle"


def test_drawing_lists_its_commands_and_no_feature(w):
    drawing(w)
    got = names(w)
    for n in ("Add view", "Projected view", "Isometric view", "Dimension", "Note", "Sheet settings", "Export DXF",
              "Export PDF", "Export SVG", "Export PNG"):
        assert n in got, n
    assert not FEATURES & set(got) and "Revolute" not in got


def test_drawing_commands_call_the_toolbar_functions(w, monkeypatch):
    g = drawing(w)
    seen = []
    monkeypatch.setattr(w.dwg, "export", lambda f=None: seen.append(("export", f)))
    monkeypatch.setattr(w.dwg, "sheet_setup", lambda: seen.append("sheet"))
    run(w, "Export DXF")
    run(w, "Export PNG")
    run(w, "Sheet settings")
    assert seen == [("export", "dxf"), ("export", "png"), "sheet"]
    run(w, "Dimension")
    assert w.dwg.tool == "dim"
    run(w, "Note")
    assert w.dwg.tool == "note"
    assert g is w.doc


def test_isometric_view_presets_the_direction(w, monkeypatch):
    from freeshape.ui import drawing_ui
    drawing(w)
    picked = []

    def fake_exec(self):
        picked.append(self.dir.currentData())
        return 0
    monkeypatch.setattr(drawing_ui.ViewDialog, "exec", fake_exec)
    run(w, "Isometric view")
    run(w, "Add view")
    assert picked == ["Isometric", "Front"]


def test_variable_studio_lists_only_view_independent_commands(w):
    w._add_tab(VariableStudioDoc())
    got = names(w)
    assert "New Part Studio" in got and "Save" in got
    assert not FEATURES & set(got) and "Zoom to fit" not in got and "Revolute" not in got


def test_sketch_lists_sketch_tools_and_no_feature_and_runs_them(w):
    w.viewport.set_selection([Pick("plane", ("std", "Front"))])
    w.start_sketch()
    assert w.mode == "sketch" and w.sketcher is not None
    got = names(w)
    for n in ("Line", "Corner rectangle", "Circle", "Polygon", "Trim", "Extend", "Offset", "Mirror", "Use",
              "Construction", "Dimension", "Coincident", "Normal", "Slot", "Sketch fillet", "Finish sketch",
              "Front view", "Zoom to fit"):
        assert n in got, n
    assert not FEATURES & set(got) - {"Sketch"} and "Extrude" not in got and "Revolute" not in got
    w.open_command_search()
    assert not w._palette.isHidden() and w._palette.list.count() > 10
    for label, tool in (("Polygon", "polygon"), ("Extend", "extend"), ("Normal", "normal")):
        run(w, label)
        assert w.sketcher.tool == tool
    run(w, "Finish sketch")
    assert w.mode == "idle"


def test_palette_opens_in_every_tab_kind_and_after_a_feature_dialog_is_refused(w):
    sc = [s for s in w.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+Space")]
    assert sc
    for make_tab in (lambda: None, lambda: assembly(w), lambda: drawing(w), lambda: w._add_tab(VariableStudioDoc())):
        make_tab()
        if getattr(w, "_palette", None) is not None:
            w._palette.close_palette()
        sc[0].activated.emit()
        assert not w._palette.isHidden()
        assert w._palette.parentWidget() is w.view_stack.currentWidget()
        assert w._palette.list.count() > 0
    w._palette.hide()
    w.tabs.setCurrentIndex(0)
    w.start_feature("extrude")
    assert w.mode == "dialog"
    sc[0].activated.emit()
    assert w._palette.isHidden() and "open tool" in w.status.currentMessage()
    w.cancel_dialog()
