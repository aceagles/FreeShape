"""I8.3: the Fillet dialog's Type choice, its Vertices list with a number per row, and the Variable fillet command.
Offscreen main window; nothing modal runs (the dialog is a panel, picks are delivered with handle_pick)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QLineEdit  # noqa: E402

from conftest import add, edge_refs, only_part  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_fillet_variable import end_offsets, front_top_edge  # noqa: E402
from test_naming_blends import block, named_faces  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def open_window(app, tmp_path, monkeypatch, variable=None):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, _ = block()
    if variable:
        add(doc, "variable", name="r", expr=variable)
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    assert w.doc is doc
    return w


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    w = open_window(app, tmp_path, monkeypatch)
    yield w
    w.deleteLater()


@pytest.fixture
def win_r(app, tmp_path, monkeypatch):
    w = open_window(app, tmp_path, monkeypatch, variable="3")
    yield w
    w.deleteLater()


def edge_pick(part):
    i = next(i for i, e in enumerate(K.edges(part.shape)) if front_top_edge(K.edge_info(e)))
    return Pick("edge", (part.id, i))


def vertex_pick(part, pos):
    pts = K.vertex_points(part.shape)
    return Pick("vertex", (part.id, next(i for i, q in enumerate(pts) if np.linalg.norm(np.asarray(q) - pos) < 1e-6)))


def start(w, variable=True):
    w.start_feature("fillet")
    d = w.dialog
    assert w.mode == "dialog" and d.ftype == "fillet"
    d.handle_pick(edge_pick(only_part(w.doc)))
    if variable:
        d._set("fillet_type", "variable")
    return d


def box_of(d, i):
    f = d.fields["vertices"]
    return f.list.itemWidget(f.list.item(i)).findChild(QLineEdit)


def type_in(d, i, text):
    ed = box_of(d, i)
    ed.setText(text)
    ed.textEdited.emit(text)


def pick_ends(d, part):
    d.activate(d.fields["vertices"])
    d.handle_pick(vertex_pick(part, (0, 0, 10)))
    d.handle_pick(vertex_pick(part, (40, 0, 10)))


def fillets(w):
    return [f for f in w.doc.features if f["type"] == "fillet"]


def test_type_choice_shows_and_hides_the_vertex_field(win):
    d = start(win, variable=False)
    f = d.fields["vertices"]
    assert not f.isVisibleTo(d)
    d._set("fillet_type", "variable")
    assert f.isVisibleTo(d)
    d._set("fillet_type", "constant")
    assert not f.isVisibleTo(d)


def test_vertex_picks_add_rows_and_a_number_sets_the_radius(win):
    part = only_part(win.doc)
    d = start(win)
    pick_ends(d, part)
    f = d.fields["vertices"]
    assert len(f.items) == 2
    assert [box_of(d, i).text() for i in (0, 1)] == ["2", "2"]          # the default radius
    assert [r["radius"] for r in d.feature["params"]["vertex_radii"]] == [2.0, 2.0]
    type_in(d, 1, "6")
    rows = d.feature["params"]["vertex_radii"]
    assert rows[1]["radius"] == 6.0 and rows[0]["radius"] == 2.0
    assert rows[1]["vertex"]["kind"] == "vertex" and np.allclose(rows[1]["vertex"]["c"], [40, 0, 10])
    win.commit_dialog()
    (fe,) = fillets(win)
    assert win.doc.errors.get(fe["id"]) is None
    off = end_offsets(win.doc)
    assert off[0] == pytest.approx([2.0, 2.0], abs=1e-3)
    assert off[40] == pytest.approx([6.0, 6.0], abs=1e-3)


def test_an_empty_row_follows_the_default_radius(win):
    part = only_part(win.doc)
    d = start(win)
    pick_ends(d, part)
    d._set("radius", 3.0)
    assert [r["radius"] for r in d.feature["params"]["vertex_radii"]] == [3.0, 3.0]


def test_expression_in_a_row_is_stored_and_evaluated(win_r):
    w = win_r
    part = only_part(w.doc)
    d = start(w)
    pick_ends(d, part)
    type_in(d, 1, "#r * 2")
    assert d.feature["params"]["vertex_radii"][1]["radius"] == "#r * 2"
    w.commit_dialog()
    (fe,) = fillets(w)
    assert w.doc.errors.get(fe["id"]) is None
    assert end_offsets(w.doc)[40] == pytest.approx([6.0, 6.0], abs=1e-3)       # #r = 3


def test_a_vertex_off_the_selected_edges_is_refused(win):
    part = only_part(win.doc)
    d = start(win)
    d.activate(d.fields["vertices"])
    d.handle_pick(vertex_pick(part, (0, 20, 0)))
    assert d.fields["vertices"].items == []
    assert "not at the end of a selected edge" in win.status.currentMessage()
    d.handle_pick(vertex_pick(part, (0, 0, 10)))
    assert len(d.fields["vertices"].items) == 1


def test_picking_a_listed_vertex_selects_its_row(win):
    part = only_part(win.doc)
    d = start(win)
    pick_ends(d, part)
    f = d.fields["vertices"]
    d.handle_pick(vertex_pick(part, (0, 0, 10)))
    assert len(f.items) == 2 and f.list.currentRow() == 0
    d.handle_pick(vertex_pick(part, (40, 0, 10)))
    assert len(f.items) == 2 and f.list.currentRow() == 1


def test_hovering_a_row_highlights_its_vertex(win):
    part = only_part(win.doc)
    d = start(win)
    pick_ends(d, part)
    n = len(d.overlays())
    d.fields["vertices"].hovered.emit(1)
    assert len(d.overlays()) == n + 1
    d.fields["vertices"].hovered.emit(-1)
    assert len(d.overlays()) == n


def test_reopen_shows_the_rows_and_removing_one_returns_it_to_the_default(win):
    part = only_part(win.doc)
    d = start(win)
    pick_ends(d, part)
    type_in(d, 1, "6")
    win.commit_dialog()
    (fe,) = fillets(win)
    win.start_feature("fillet", edit_fid=fe["id"])
    d = win.dialog
    f = d.fields["vertices"]
    assert d.feature["params"]["fillet_type"] == "variable" and f.isVisibleTo(d)
    assert len(f.items) == 2
    assert [box_of(d, i).text() for i in (0, 1)] == ["2", "6"]
    f._remove_item(f.list.item(1))
    assert len(d.feature["params"]["vertex_radii"]) == 1
    win.commit_dialog()
    assert win.doc.errors.get(fe["id"]) is None
    assert end_offsets(win.doc)[40] == pytest.approx([2.0, 2.0], abs=1e-3)     # back to the default radius


def test_a_constant_fillet_through_the_dialog_saves_and_builds_as_before(win):
    start(win, variable=False)
    win.commit_dialog()
    (fe,) = fillets(win)
    assert "vertex_radii" not in fe["params"] and "fillet_type" not in fe["params"]
    ref, _ = block()
    add(ref, "fillet", edges=edge_refs(only_part(ref), front_top_edge, expect=1), radius=2.0)
    assert sorted(named_faces(win.doc)) == sorted(named_faces(ref))
    assert K.volume(only_part(win.doc).shape) == pytest.approx(K.volume(only_part(ref).shape), rel=1e-9)


def test_switching_back_to_constant_drops_the_vertex_radii(win):
    part = only_part(win.doc)
    d = start(win)
    pick_ends(d, part)
    assert d.feature["params"]["vertex_radii"]
    d._set("fillet_type", "constant")
    assert "vertex_radii" not in d.feature["params"] and "fillet_type" not in d.feature["params"]
    d._set("fillet_type", "variable")
    assert len(d.feature["params"]["vertex_radii"]) == 2             # the rows were kept


def test_command_search_finds_variable_fillet_and_opens_the_dialog(win):
    from freeshape.ui.palette import search_commands
    cmds = win._palette_commands()
    assert "Variable fillet" in [c.name for c in cmds]
    for q in ("variable fillet", "variable radius"):
        hits = [c.name for c in search_commands(cmds, q)]
        assert "Variable fillet" in hits and "Fillet" in hits, (q, hits)
    next(c for c in cmds if c.name == "Variable fillet").action()
    assert win.mode == "dialog" and win.dialog.ftype == "fillet"
    assert win.dialog.feature["params"]["fillet_type"] == "variable"
    assert win.dialog.widgets["fillet_type"].currentData() == "variable"
    assert win.dialog.fields["vertices"].isVisibleTo(win.dialog)
