"""I1.9: Rib dialog, toolbar and command (offscreen; nothing modal runs). Fixture from tests/test_rib.py."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from conftest import add, only_part, rect_lines, region_refs, sketch_feature, TOP  # noqa: E402
from test_rib import bracket, line, rib, rib_sketch, volume  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape.ui import dialogs  # noqa: E402
from freeshape.ui.palette import search_commands  # noqa: E402


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


def open_bracket(win):
    doc, plane, _ = bracket()
    win._add_tab(doc)
    return doc, rib_sketch(doc, plane, line((40, 10), (10, 40)))


def run(win, name):
    next(c for c in win._palette_commands() if c.name == name).action()


def expected():
    doc, plane, _ = bracket()
    before = volume(only_part(doc).shape)
    return before, before + 0.5 * 30 * 30 * 6


def test_rib_command_stores_picks_and_builds_the_fixture_rib(win):
    doc, ref = open_bracket(win)
    pid = next(iter(doc.state.parts))
    _, want = expected()
    run(win, "Rib")
    assert win.mode == "dialog" and win.dialog.ftype == "rib"
    d = win.dialog
    d.fields["path"].toggle(("path", ref))
    d.fields["parts"].toggle(("part", pid))
    d._set("thickness", 6.0)
    p = d.feature["params"]
    assert p["path"] == [ref] and p["parts"] == [pid] and p["thickness"] == 6.0
    win.commit_dialog()
    f = [f for f in doc.features if f["type"] == "rib"][-1]
    assert doc.errors.get(f["id"]) is None
    assert volume(only_part(doc).shape) == pytest.approx(want, abs=1e-3)


def test_rib_thickness_edit_rebuilds(win):
    doc, ref = open_bracket(win)
    f, err = rib(doc, ref, thickness=6.0)
    assert err is None
    win.start_feature("rib", edit_fid=f["id"])
    win.dialog._set("thickness", 4.0)
    win.commit_dialog()
    before, _ = expected()
    assert doc.errors.get(f["id"]) is None
    assert volume(only_part(doc).shape) == pytest.approx(before + 0.5 * 30 * 30 * 4, abs=1e-3)


def test_rib_found_by_search_in_part_studio_not_in_assembly(win):
    got = {c.name for c in win._palette_commands()}
    assert "Rib" in got
    for q in ("rib", "gusset", "web", "stiffener"):
        assert search_commands(win._palette_commands(), q)[0].name == "Rib", q
    from test_assembly import make
    win._add_tab(make({}))
    assert "Rib" not in {c.name for c in win._palette_commands()}


def test_rib_single_part_is_filled_without_a_pick(win):
    doc, ref = open_bracket(win)
    pid = next(iter(doc.state.parts))
    run(win, "Rib")
    assert win.dialog.fields["parts"].items == [("part", pid)]
    assert win.dialog.feature["params"]["parts"] == [pid]


def test_rib_part_defaults_to_the_one_part_the_plane_cuts(win):
    doc, plane, _ = bracket()
    far = sketch_feature(doc, lambda s: rect_lines(s, 200, 200, 10, 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, far), depth=10, op="new")
    assert len(doc.state.parts) == 2
    ref = rib_sketch(doc, plane, line((40, 10), (10, 40)))
    win._add_tab(doc)
    run(win, "Rib")
    assert win.dialog.fields["parts"].items == []
    win.dialog.fields["path"].toggle(("path", ref))
    cut = win.dialog.fields["parts"].items
    assert len(cut) == 1 and win.dialog.feature["params"]["parts"] == [cut[0][1]]
    assert cut[0][1] == next(iter(doc.state.parts))        # the bracket, not the block far away


def test_rib_schema_round_trips():
    p = F.default_params("rib")
    fields = {"path": [("path", {"kind": "sketch_chain", "sketch": "s", "entity": "e"})], "parts": [("part", "p1")]}
    dialogs.fields_to_params("rib", fields, p)
    assert p["path"] == [fields["path"][0][1]] and p["parts"] == ["p1"]
    assert dialogs.params_to_fields("rib", p) == fields
    assert win_tool_icon_exists()


def win_tool_icon_exists():
    from freeshape.ui.icons import icon
    return not icon("rib").isNull()
