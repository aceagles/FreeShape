"""I3.2: Thicken dialog, toolbar and command (offscreen; nothing modal runs). Fixtures from tests/test_thicken.py."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from conftest import TOP, add, block_doc, face_refs, normal_is, only_part  # noqa: E402
from test_thicken import arc_doc, flat_doc, solids_of, surfaces  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui import dialogs  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    app.processEvents()
    yield w
    w.deleteLater()


def run(win, name):
    next(c for c in win._palette_commands() if c.name == name).action()


def start(win):
    run(win, "Thicken")
    assert win.mode == "dialog" and win.dialog.ftype == "thicken"
    return win.dialog


def top_face(doc):
    return face_refs(only_part(doc), normal_is([0, 0, 1]), expect=1)[0]


def test_thicken_command_stores_picks_and_builds_the_first_fixture(win):
    doc = flat_doc()                                   # I3.1's first fixture: a 20 x 10 surface, 3 thick = 600
    win._add_tab(doc)
    (sp,) = surfaces(doc)
    d = start(win)
    d.fields["faces"].toggle(("part", sp.id))
    d._set("thickness1", 3.0)
    p = d.feature["params"]
    assert p["parts"] == [sp.id] and p["faces"] == [] and p["thickness1"] == 3.0 and p["op"] == "new"
    win.commit_dialog()
    f = [f for f in doc.features if f["type"] == "thicken"][-1]
    assert doc.errors.get(f["id"]) is None
    (s,) = solids_of(doc)
    assert K.volume(s.shape) == pytest.approx(600.0, rel=1e-6)
    assert not surfaces(doc)                           # the surface was used up


def test_thicken_edit_rebuilds_with_the_new_value(win):
    doc = flat_doc()
    win._add_tab(doc)
    (sp,) = surfaces(doc)
    f = add(doc, "thicken", parts=[sp.id], thickness1=3.0)
    win.start_feature("thicken", edit_fid=f["id"])
    assert win.dialog.fields["faces"].items == [("part", sp.id)]
    win.dialog._set("thickness1", 5.0)
    win.commit_dialog()
    assert doc.errors.get(f["id"]) is None
    assert K.volume(solids_of(doc)[0].shape) == pytest.approx(1000.0, rel=1e-6)


def test_picking_a_solid_face_sets_add_and_a_surface_sets_new(win):
    doc = block_doc()
    win._add_tab(doc)
    d = start(win)
    d.fields["faces"].toggle(("face", top_face(doc)))
    assert d.feature["params"]["op"] == "add"
    d.fields["faces"].toggle(("face", top_face(doc)))        # picked off again
    d._set("op", "new")
    d.fields["faces"].toggle(("face", top_face(doc)))
    assert d.feature["params"]["op"] == "add"

def test_picking_a_surface_part_sets_new(win):
    doc = flat_doc()
    win._add_tab(doc)
    (sp,) = surfaces(doc)
    d = start(win)
    d._set("op", "add")
    d.fields["faces"].toggle(("part", sp.id))
    assert d.feature["params"]["op"] == "new"


def test_a_user_choice_of_operation_survives_a_thickness_edit(win):
    doc = block_doc()
    win._add_tab(doc)
    d = start(win)
    d.fields["faces"].toggle(("face", top_face(doc)))
    d._set("op", "new")
    d._set("thickness1", 4.0)
    assert d.feature["params"]["op"] == "new"


def test_mid_plane_keeps_the_three_values(win):
    doc = flat_doc()
    win._add_tab(doc)
    d = start(win)
    d._set("thickness1", 6.0)
    d._set("thickness2", 1.5)
    d._set("mid_plane", True)
    assert d.widgets["thickness2"].isHidden() or not d.widgets["thickness2"].isVisibleTo(d)
    d._set("mid_plane", False)
    p = d.feature["params"]
    assert (p["thickness1"], p["thickness2"]) == (6.0, 1.5)
    assert d.widgets["thickness2"].isVisibleTo(d)


def test_thicken_found_by_search_in_part_studio_not_in_assembly(win):
    assert "Thicken" in {c.name for c in win._palette_commands()}
    for q in ("thicken", "surface to solid", "offset solid"):
        assert "Thicken" in [c.name for c in search_commands(win._palette_commands(), q)][:3], q
    assert search_commands(win._palette_commands(), "thicken")[0].name == "Thicken"
    from test_assembly import make
    win._add_tab(make({}))
    assert "Thicken" not in {c.name for c in win._palette_commands()}


def test_thicken_has_a_toolbar_button_with_an_icon(win):
    from PySide6.QtWidgets import QToolButton
    btns = [b for b in win.findChildren(QToolButton) if b.property("icon_name") == "thicken"]
    assert len(btns) == 1 and not btns[0].icon().isNull()


# ---------------------------------------------------------------------------- the engine wire

@pytest.fixture(scope="module")
def engine_win():
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    w.settings = type("S", (dict,), {"value": lambda s, k, d=None: s.get(k, d),
                                     "setValue": lambda s, k, v: s.__setitem__(k, v)})()
    assert w.studio_engine.settle(180)
    yield w
    w.studio_engine.shutdown()
    from freeshape.engine import process as P
    P.SPARES.clear()


def test_thicken_made_in_the_engine_window_builds_a_solid(engine_win):
    from test_extrude_surface import curve_refs, first_line, polyline
    from conftest import sketch_feature
    w = engine_win
    se = w.studio_engine
    doc = w.doc
    sid = sketch_feature(doc, polyline, TOP)
    assert se.settle(180)
    add(doc, "extrude", creation="surface", curves=curve_refs(sid, first_line(doc, sid)), depth=10, op="new")
    assert se.settle(180)
    (sp,) = list(doc.state.parts.values())
    assert K.is_surface(sp.shape)
    w.start_feature("thicken")
    dlg = w.dialog
    dlg.handle_pick(Pick("part", (sp.id,)))              # a surface part picked in the view
    assert dlg.feature["params"]["parts"] == [sp.id] and dlg.feature["params"]["op"] == "new"
    dlg._set("thickness1", 2.0)
    dlg._do_preview()                                    # the preview builds in the worker without an error
    assert se.settle(180)
    assert dlg.err.isHidden(), dlg.err.text()
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert len(parts) == 1 and not K.is_surface(parts[0].shape)
    assert K.is_valid(parts[0].shape) and K.volume(parts[0].shape) > 400
