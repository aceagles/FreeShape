"""I3.6: Fill dialog, toolbar and command (offscreen; nothing modal runs). The fixture is I3.5's first one, the four
coplanar edges of a box's open top (Delete face / leave open), filled at Position into a planar face of area 800.
Tangency is a per-edge tick; a sketch-curve row shows Position and has no tick (it would be ignored)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from conftest import add, face_refs, normal_is, only_part, rect_lines, sketch_feature, TOP  # noqa: E402
from test_fill import BOX, area, box_with_open_top  # noqa: E402

from freeshape import kernel as K  # noqa: E402
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


def start(win, **kw):
    if kw:
        win.start_feature("fill", **kw)
    else:
        next(c for c in win._palette_commands() if c.name == "Fill").action()
    assert win.mode == "dialog" and win.dialog.ftype == "fill"
    return win.dialog


def rim_picks(part):
    """The viewport picks of the four edges around the open top of the box."""
    out = []
    for i, e in enumerate(K.edges(part.shape)):
        a = K.edge_info(e)
        if a["type"] == int(K.GeomAbs_Line) and abs(a["p0"][2] - BOX[2]) < 1e-6 and abs(a["p1"][2] - BOX[2]) < 1e-6:
            out.append(Pick("edge", (part.id, i)))
    assert len(out) == 4
    return out


def ticks(d):
    """The Tangency ticks of the rows now in the dialog (rows replaced by a rebuild are still children until Qt deletes
    them, so walk the layout)."""
    from PySide6.QtWidgets import QCheckBox
    lay = d._fill_box.layout()
    return [b for i in range(lay.count()) for b in lay.itemAt(i).widget().findChildren(QCheckBox)]


def surfaces(doc):
    return [p for p in doc.state.parts.values() if K.is_surface(p.shape)]


def test_fill_command_stores_picks_and_builds_the_plane(win):
    doc, part, rim = box_with_open_top()
    win._add_tab(doc)
    win.viewport.part_shapes = {part.id: part.shape}
    d = start(win)
    for pk in rim_picks(part):
        d.handle_pick(pk)
    p = d.feature["params"]
    assert [e["continuity"] for e in p["edges"]] == ["position"] * 4
    assert all(e["ref"]["kind"] == "edge" and e["faces"] == [] for e in p["edges"])
    assert p["op"] == "new" and p["guides"] == [] and p["scope"] == []
    win.commit_dialog()
    f = [f for f in doc.features if f["type"] == "fill"][-1]
    assert doc.errors.get(f["id"]) is None
    new = [q for q in surfaces(doc) if q.id != part.id]
    assert len(new) == 1 and len(K.faces(new[0].shape)) == 1
    assert area(new[0].shape) == pytest.approx(BOX[0] * BOX[1], abs=1e-6)
    assert K.face_info(K.faces(new[0].shape)[0])["type"] == int(K.GeomAbs_Plane)


def test_add_closes_the_box_into_a_solid(win):
    doc, part, rim = box_with_open_top()
    win._add_tab(doc)
    win.viewport.part_shapes = {part.id: part.shape}
    d = start(win)
    for pk in rim_picks(part):
        d.handle_pick(pk)
    d._set("op", "add")
    assert d.fields["scope"].isVisibleTo(d)
    win.commit_dialog()
    assert not doc.errors, doc.errors
    s = only_part(doc)
    assert not K.is_surface(s.shape) and K.volume(s.shape) == pytest.approx(BOX[0] * BOX[1] * BOX[2], abs=1e-3)


def test_scope_shows_only_for_add(win):
    doc, part, rim = box_with_open_top()
    win._add_tab(doc)
    d = start(win)
    d._set("op", "new")
    assert not d.fields["scope"].isVisibleTo(d)
    d._set("op", "add")
    assert d.fields["scope"].isVisibleTo(d)


def test_fill_edit_rebuilds_with_the_new_value(win):
    doc, part, rim = box_with_open_top()
    f = add(doc, "fill", edges=rim, op="new")
    win._add_tab(doc)
    assert len(surfaces(doc)) == 2
    win.start_feature("fill", edit_fid=f["id"])
    assert len(win.dialog.fields["edges"].items) == 4
    win.dialog._set("op", "add")                     # the fill now sews to the box and closes it
    win.commit_dialog()
    assert doc.errors.get(f["id"]) is None
    assert len(doc.state.parts) == 1 and not K.is_surface(only_part(doc).shape)


def test_one_edge_set_to_tangency_stores_it_on_that_edge_only(win):
    doc, part, rim = box_with_open_top()
    win._add_tab(doc)
    win.viewport.part_shapes = {part.id: part.shape}
    d = start(win)
    for pk in rim_picks(part):
        d.handle_pick(pk)
    boxes = ticks(d)
    assert len(boxes) == 4 and all(b.text() == "Tangency" and not b.isChecked() for b in boxes)
    boxes[2].setChecked(True)
    assert d.feature["params"]["edges"][2]["continuity"] == "tangency"
    assert [e["continuity"] for e in d.feature["params"]["edges"]] == ["position", "position", "tangency", "position"]
    d.handle_pick(rim_picks(part)[0])                # picking a listed edge again removes it; the others keep theirs
    assert [e["continuity"] for e in d.feature["params"]["edges"]] == ["position", "tangency", "position"]
    d.handle_pick(rim_picks(part)[0])                # ...and picking it back adds it at the end, at Position
    assert [e["continuity"] for e in d.feature["params"]["edges"]] == ["position", "tangency", "position", "position"]


def test_a_sketch_curve_row_says_position_and_has_no_tangency_tick(win):
    from freeshape.document import PartStudioDoc
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    win._add_tab(doc)
    win.viewport.sketches = [(sid, doc.state.sketches[sid], [])]
    ent = next(i for i, e in doc.state.sketches[sid].sketch.entities.items() if e["type"] == "line")
    d = start(win)
    d.handle_pick(Pick("sketch_line", (sid, ent)))
    (e,) = d.feature["params"]["edges"]
    assert e["ref"] == {"kind": "sketch_chain", "sketch": sid, "entity": ent} and e["continuity"] == "position"
    from PySide6.QtWidgets import QLabel
    assert not ticks(d)
    assert "Position" in [lb.text() for lb in d._fill_box.findChildren(QLabel)]
    win.commit_dialog()
    f = [f for f in doc.features if f["type"] == "fill"][-1]
    assert doc.errors.get(f["id"]) is None and len(surfaces(doc)) == 1      # four lines of a square: a plane


def test_guide_vertices_are_stored(win):
    doc, part, rim = box_with_open_top()
    win._add_tab(doc)
    win.viewport.part_shapes = {part.id: part.shape}
    d = start(win)
    for pk in rim_picks(part):
        d.handle_pick(pk)
    assert "guides" in d.fields and d.fields["guides"].want == {"vertex"}
    d.activate(d.fields["guides"])
    d.handle_pick(Pick("vertex", (part.id, 0)))
    assert len(d.feature["params"]["guides"]) == 1 and d.feature["params"]["guides"][0]["kind"] == "vertex"


def test_open_boundary_marks_the_two_open_ends_and_holds_ok_back(win):
    doc, part, rim = box_with_open_top()
    win._add_tab(doc)
    win.viewport.part_shapes = {part.id: part.shape}
    d = start(win)
    picks = rim_picks(part)
    for pk in picks[:3]:
        d.handle_pick(pk)
    d._do_preview()
    assert d.err.text().startswith("⚠ " + K.FILL_GAP), d.err.text()
    assert not d.ok_button.isEnabled()
    assert len(d._gap_points) == 2
    ends = {tuple(round(float(x), 6) for x in p) for p in d._gap_points}
    last = K.edges(part.shape)[picks[3].key[1]]
    want = {tuple(round(float(x), 6) for x in (p[0], p[-1])[k]) for p in [K.discretize_edge(last)] for k in (0, 1)}
    assert ends == want
    assert len([o for o in d.overlays() if o[0][0] == "gap"]) == 2
    d.handle_pick(picks[3])                          # the fourth edge closes the loop
    d._do_preview()
    assert d.err.isHidden() and d.ok_button.isEnabled() and d._gap_points == []
    assert not [o for o in d.overlays() if o[0][0] == "gap"]


def test_fill_found_by_search_in_part_studio_not_in_assembly(win):
    assert "Fill" in {c.name for c in win._palette_commands()}
    assert "Fill" in [c.name for c in search_commands(win._palette_commands(), "fill")][:3]
    for q in ("patch", "cap hole", "fill surface"):
        assert search_commands(win._palette_commands(), q)[0].name == "Fill", q
    from test_assembly import make
    win._add_tab(make({}))
    assert "Fill" not in {c.name for c in win._palette_commands()}


def test_fill_has_a_toolbar_button_with_an_icon_after_enclose(win):
    from PySide6.QtWidgets import QToolButton
    names = [b.property("icon_name") for b in win.findChildren(QToolButton) if b.property("icon_name")]
    assert names.count("fill") == 1 and names.index("fill") == names.index("enclose") + 1
    btn = next(b for b in win.findChildren(QToolButton) if b.property("icon_name") == "fill")
    assert not btn.icon().isNull()


def test_no_label_promises_tangent_continuity():
    import inspect
    from freeshape.ui import dialogs
    cls = next(c for c in vars(dialogs).values() if isinstance(c, type) and hasattr(c, "_fill_rows"))
    src = inspect.getsource(cls._fill_rows)
    assert "continuous" not in src.lower() and "smooth" not in src.lower()


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


def engine_open_box(w):
    from conftest import region_refs
    se, doc = w.studio_engine, w.doc
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, BOX[0], BOX[1]), TOP)
    assert se.settle(180)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=BOX[2], op="new")
    assert se.settle(180)
    part = only_part(doc)
    add(doc, "delete_face", faces=face_refs(part, normal_is((0, 0, 1)), expect=1), mode="open")
    assert se.settle(180)
    part = only_part(doc)
    assert K.is_surface(part.shape)
    w.viewport.part_shapes = getattr(w.viewport, "part_shapes", {}) or {part.id: part.shape}
    return part


def engine_fill(w):
    """The open box with its top filled through the dialog in the engine window; returns the box part."""
    se, doc = w.studio_engine, w.doc
    part = engine_open_box(w)
    w.start_feature("fill")
    dlg = w.dialog
    assert dlg.ftype == "fill"
    shape = w.viewport.part_shapes[part.id]
    picks = []
    for i, e in enumerate(K.edges(shape)):
        a = K.edge_info(e)
        if a["type"] == int(K.GeomAbs_Line) and abs(a["p0"][2] - BOX[2]) < 1e-6 and abs(a["p1"][2] - BOX[2]) < 1e-6:
            picks.append(Pick("edge", (part.id, i)))
    assert len(picks) == 4
    for pk in picks:
        dlg.handle_pick(pk)
    assert len(dlg.feature["params"]["edges"]) == 4
    dlg._do_preview()                                # the edge references resolve in the worker
    assert se.settle(180)
    assert dlg.err.isHidden(), dlg.err.text()
    w.commit_dialog()
    assert se.settle(180)
    assert not doc.errors, doc.errors
    new = [q for q in surfaces(doc) if q.id != part.id]
    assert len(new) == 1 and area(new[0].shape) == pytest.approx(BOX[0] * BOX[1], abs=1e-6)
    return part


def test_fill_made_in_the_engine_window_builds_the_face(engine_win):
    engine_fill(engine_win)


def test_fill_tangency_on_one_edge_resolves_in_the_engine_window(engine_win):
    w = engine_win
    se, doc = w.studio_engine, w.doc
    if not any(f["type"] == "fill" for f in doc.features):     # run without the test above (a CI shard)
        engine_fill(w)
    fills = [f for f in doc.features if f["type"] == "fill"]
    assert fills
    part = next(q for q in doc.state.parts.values() if q.name and K.is_surface(q.shape) and len(K.faces(q.shape)) == 5)
    w.start_feature("fill", edit_fid=fills[-1]["id"])
    dlg = w.dialog
    boxes = ticks(dlg)
    assert len(boxes) == 4
    boxes[0].setChecked(True)
    got = [e["continuity"] for e in dlg.feature["params"]["edges"]]
    assert got == ["tangency", "position", "position", "position"]
    dlg._do_preview()
    assert se.settle(180)
    w.commit_dialog()
    assert se.settle(180)
    f = next(f for f in doc.features if f["id"] == fills[-1]["id"])
    assert [e["continuity"] for e in f["params"]["edges"]] == got
    assert doc.errors.get(f["id"]) is None, doc.errors.get(f["id"])       # the support face resolved in the worker
    new = [q for q in surfaces(doc) if len(K.faces(q.shape)) == 1]
    assert len(new) == 1
    assert K.face_info(K.faces(new[0].shape)[0])["type"] != int(K.GeomAbs_Plane)      # a plate, not the exact plane
