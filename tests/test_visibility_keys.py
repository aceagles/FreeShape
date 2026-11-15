"""Onshape's visibility keys (P, Shift+P, Y, Shift+Y, Shift+I, Shift+T, Shift+X, K, Shift+H, J) in a Part Studio
and an Assembly, and the same letters still reaching the sketcher, a Drawing and a text box. The window is shown
and active so the keys go through Qt's shortcut handling, as real key presses do. Offscreen."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from conftest import TOP, add, only_part, rect_lines  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.drawing import DrawingDoc  # noqa: E402
from freeshape.ui.panels import ROLE_ID  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from freeshape.variable_studio import VariableStudioDoc  # noqa: E402
from test_assembly import box, make, src  # noqa: E402
from test_connector_frames import plate_studio  # noqa: E402
from test_studio_connectors import connector_params, second_part  # noqa: E402

SHIFT = Qt.ShiftModifier


def key(k, mods=Qt.NoModifier, target=None):
    QTest.keyClick(target or QApplication.focusWidget(), k, mods)
    QApplication.processEvents()


def _window(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.show()
    w.activateWindow()
    QTest.qWaitForWindowActive(w)
    w.viewport.setFocus()
    app.processEvents()
    assert QApplication.activeWindow() is w and QApplication.focusWidget() is w.viewport
    return w


@pytest.fixture
def win(tmp_path, monkeypatch):
    """A Part Studio: a plate with a mate connector and an axis, a second part beside it, two sketches and one
    custom plane."""
    w = _window(tmp_path, monkeypatch)
    doc, _ = plate_studio()
    plate = only_part(doc)
    edge = next(e for e in K.edges(plate.shape) if K.edge_info(e)["type"] == int(K.GeomAbs_Line))
    w.axis = add(doc, "axis", refs=[{"k": "edge", "ref": F.make_ref(plate.id, edge, "edge", plate.shape)}])["id"]
    w.conn = add(doc, "mate_connector", **connector_params(doc, name="C1"))["id"]
    w.plate = plate.id
    w.other = second_part(doc)
    w._add_tab(doc)
    QApplication.processEvents()
    assert w.doc is doc
    doc.state.planes["myplane"] = K.std_plane("Top")        # a plane the user made
    w._show_state(doc.state)
    w.viewport.setFocus()
    yield w
    w.hide()
    w.deleteLater()


@pytest.fixture
def awin(tmp_path, monkeypatch):
    """An Assembly of three blocks."""
    w = _window(tmp_path, monkeypatch)
    a = make({"block": box(10, 10, 10)})
    w.ids = [a.insert(src("block"), T=np.array([[1, 0, 0, 20 * k], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float))
             for k in range(3)]
    w._add_tab(a)
    QApplication.processEvents()
    assert w.doc is a and w.is_asm
    w.viewport.setFocus()
    yield w
    w.hide()
    w.deleteLater()


def plane_keys(w):
    return {p[0] for p in w.viewport.planes}


def triad_ids(w):
    return {t.get("id") for t in w.viewport.triads}


def face(pid):
    return Pick("face", (pid, 0))


def select_in_parts_list(w, pid):
    lw = w.left.parts
    for i in range(lw.count()):
        lw.item(i).setSelected(lw.item(i).data(ROLE_ID) == pid)
    assert lw.selected_part_ids() == [pid]


# ------------------------------------------------------------------ Part Studio
def test_p_hides_and_shows_default_and_custom_planes(win):
    assert ("plane", "myplane") in plane_keys(win) and ("std", "Top") in plane_keys(win)
    assert win.act_planes.text() == "Show planes\tP"
    key(Qt.Key_P)
    assert plane_keys(win) == set() and not win.act_planes.isChecked()
    assert win.status.currentMessage() == "Planes hidden: P shows them again"
    assert triad_ids(win) == {win.conn} and len(win.viewport.axes) == 1      # only the planes went
    key(Qt.Key_P)
    assert ("plane", "myplane") in plane_keys(win) and ("std", "Top") in plane_keys(win)
    assert win.act_planes.isChecked() and "hides them again" in win.status.currentMessage()


def test_shift_p_hides_planes_axes_and_connectors_together(win):
    key(Qt.Key_K)                                        # one of the three already off: Shift+P still hides the rest
    key(Qt.Key_P, SHIFT)
    assert plane_keys(win) == set() and win.viewport.axes == [] and triad_ids(win) == set()
    assert "Shift+P shows them again" in win.status.currentMessage()
    key(Qt.Key_P, SHIFT)                                 # nothing showing: all three come back
    assert ("std", "Top") in plane_keys(win) and len(win.viewport.axes) == 1 and triad_ids(win) == {win.conn}
    assert win.show_axes and win.show_connectors and win.act_planes.isChecked()


def test_k_hides_connectors_but_a_picked_or_edited_one_still_shows(win):
    key(Qt.Key_K)
    assert triad_ids(win) == set() and "K shows them again" in win.status.currentMessage()
    win._feature_hovered(win.conn)                       # pointed at in the feature list
    assert triad_ids(win) == {win.conn}
    win._feature_hovered(None)
    assert triad_ids(win) == set()
    win.start_feature("mate_connector", edit_fid=win.conn)      # its own dialog needs it on screen
    win.show_preview(win.dialog.feature, win.dialog.index, [])
    assert win.mode == "dialog" and triad_ids(win) == {win.conn}
    win.cancel_dialog()
    key(Qt.Key_K)
    assert triad_ids(win) == {win.conn}


def test_y_hides_the_selection_and_shift_y_shows_all_parts(win):
    doc = win.doc
    win.viewport.selection = [face(win.plate)]
    key(Qt.Key_Y)
    assert doc.hidden_parts == {win.plate} and "Shift+Y" in win.status.currentMessage()
    assert win.plate not in {p[0] for p in win.viewport.parts}
    key(Qt.Key_Y, SHIFT)
    assert doc.hidden_parts == set() and win.status.currentMessage() == "All parts shown"


def test_y_falls_back_to_what_is_under_the_mouse(win):
    doc = win.doc
    win.viewport.hover = face(win.other)
    key(Qt.Key_Y)
    assert doc.hidden_parts == {win.other}
    key(Qt.Key_Y)                                        # nothing selected, nothing under the mouse
    assert doc.hidden_parts == {win.other} and win.status.currentMessage().startswith("Nothing to hide")


def test_y_hides_sketches_planes_axes_and_connectors(win):
    doc = win.doc
    sid = next(iter(doc.state.sketches))
    win.viewport.selection = [Pick("region", (sid, 0)), Pick("plane", ("std", "Top")), Pick("plane", ("plane", "myplane")),
                              Pick("axis", (win.axis,)), Pick("connector", (win.conn,))]
    key(Qt.Key_Y)
    assert doc.hidden_features == {sid, "Top", "myplane", win.axis, win.conn} and doc.hidden_parts == set()
    assert ("std", "Top") not in plane_keys(win) and win.viewport.axes == [] and triad_ids(win) == set()
    assert "feature list" in win.status.currentMessage()


def test_y_works_with_the_focus_in_the_parts_list(win):
    select_in_parts_list(win, win.plate)
    win.left.parts.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is win.left.parts
    key(Qt.Key_Y)
    assert win.doc.hidden_parts == {win.plate}
    win.viewport.hover = face(win.other)                 # the list still has the hidden part selected:
    key(Qt.Key_Y)                                        # the thing under the mouse goes next
    assert win.doc.hidden_parts == {win.plate, win.other}


def test_shift_i_isolates_the_selection_or_the_hovered_part(win):
    doc = win.doc
    win.viewport.selection = [face(win.plate)]
    key(Qt.Key_I, SHIFT)
    assert doc.hidden_parts == {win.other} and "Shift+Y" in win.status.currentMessage()
    key(Qt.Key_Y, SHIFT)
    win.viewport.selection = []
    win.viewport.hover = face(win.other)
    key(Qt.Key_I, SHIFT)
    assert doc.hidden_parts == {win.plate}
    key(Qt.Key_Y, SHIFT)
    win.viewport.hover = None
    key(Qt.Key_I, SHIFT)
    assert doc.hidden_parts == set() and win.status.currentMessage().startswith("Nothing to isolate")


def test_shift_t_makes_parts_transparent_in_the_view_only(win):
    doc, vp = win.doc, win.viewport
    saved = json.dumps(doc.to_json(), sort_keys=True, default=str)
    assert vp.transparent_ids == set() and vp._batch["nsolid"] == vp._batch["ntri"]
    vp.selection = [face(win.plate)]
    key(Qt.Key_T, SHIFT)
    assert vp.transparent_ids == {win.plate} and vp.reference_ids == set()
    assert 0 < vp._batch["nsolid"] < vp._batch["ntri"]               # the plate is in the translucent tail
    assert vp._batch["ntri"] - vp._batch["nsolid"] == len(dict((p[0], p[1]) for p in vp.parts)[win.plate].tris)
    assert vp._pick_exists(face(win.plate)) and vp.selection == [face(win.plate)]    # still there to pick
    assert not doc.state.parts[win.plate].reference
    assert json.dumps(doc.to_json(), sort_keys=True, default=str) == saved          # nothing for the file
    vp.selection = []
    vp.hover = face(win.other)
    key(Qt.Key_T, SHIFT)                                 # the hovered part joins
    assert vp.transparent_ids == {win.plate, win.other} and vp._batch["nsolid"] == 0
    key(Qt.Key_T, SHIFT)                                 # again: solid
    assert vp.transparent_ids == {win.plate} and "solid again" in win.status.currentMessage()
    doc.notify()                                         # a redraw keeps it
    assert vp.transparent_ids == {win.plate} and vp._batch["nsolid"] < vp._batch["ntri"]
    vp.hover = None
    key(Qt.Key_T, SHIFT)                                 # nothing targeted: all solid
    assert vp.transparent_ids == set() and vp._batch["nsolid"] == vp._batch["ntri"]
    key(Qt.Key_T, SHIFT)
    assert win.status.currentMessage().startswith("Nothing to make transparent")
    assert json.dumps(doc.to_json(), sort_keys=True, default=str) == saved


def test_transparency_is_kept_per_tab(win):
    doc, vp = win.doc, win.viewport
    vp.selection = [face(win.plate)]
    key(Qt.Key_T, SHIFT)
    win.new_doc()
    QApplication.processEvents()
    assert win.doc is not doc and vp.transparent_ids == set()
    win.tabs.setCurrentIndex(win._tab_index(doc))
    QApplication.processEvents()
    assert win.doc is doc and vp.transparent_ids == {win.plate} and vp._batch["nsolid"] < vp._batch["ntri"]


def test_shift_x_toggles_the_section_view_and_ctrl_shift_x_still_does(win):
    key(Qt.Key_X, SHIFT)
    assert win.section_panel is not None and win.act_section.isChecked()
    assert win.status.currentMessage() == "Section view on: Shift+X closes it"
    key(Qt.Key_X, SHIFT)
    assert win.section_panel is None and not win.act_section.isChecked()
    key(Qt.Key_X, Qt.ControlModifier | SHIFT)
    assert win.section_panel is not None
    key(Qt.Key_X, Qt.ControlModifier | SHIFT)
    assert win.section_panel is None


def test_shift_h_hides_all_sketches_then_shows_them(win):
    doc = win.doc
    sketches = set(doc.state.sketches)
    assert len(sketches) == 2
    doc.set_features_hidden([next(iter(sketches))], True)            # one already hidden: the rest go too
    key(Qt.Key_H, SHIFT)
    assert doc.hidden_features == sketches and win.viewport.sketches == []
    assert win.status.currentMessage() == "Sketches hidden: Shift+H shows them again"
    key(Qt.Key_H, SHIFT)
    assert doc.hidden_features == set() and len(win.viewport.sketches) == 2


def test_j_does_nothing_in_a_part_studio(win):
    doc = win.doc
    before = (set(doc.hidden_parts), set(doc.hidden_features), plane_keys(win), triad_ids(win))
    key(Qt.Key_J)
    assert (doc.hidden_parts, doc.hidden_features, plane_keys(win), triad_ids(win)) == before
    assert "Assembly" in win.status.currentMessage()


def test_in_a_feature_dialog_only_the_hovered_thing_counts(win):
    doc, vp = win.doc, win.viewport
    win.start_feature("fillet")
    assert win.mode == "dialog"
    vp.selection = [face(win.plate)]                     # the dialog's picks
    key(Qt.Key_Y)
    assert doc.hidden_parts == set() and win.status.currentMessage().startswith("Nothing to hide")
    key(Qt.Key_T, SHIFT)
    assert vp.transparent_ids == set()
    key(Qt.Key_I, SHIFT)
    assert doc.hidden_parts == set()
    vp.hover = face(win.other)
    key(Qt.Key_T, SHIFT)
    assert vp.transparent_ids == {win.other}
    key(Qt.Key_Y)
    assert doc.hidden_parts == {win.other} and win.mode == "dialog"
    key(Qt.Key_P)                                        # the view switches work during a dialog too
    assert not win.act_planes.isChecked() and win.mode == "dialog"
    win.cancel_dialog()


# ------------------------------------------------------------------ the letters still reach their old owners
def _start_sketch(w):
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    rect_lines(w.sketcher.sk, 200, 0, 10, 10)
    assert w.mode == "sketch"
    w.viewport.setFocus()
    QApplication.processEvents()


def test_in_a_sketch_p_and_h_are_still_the_sketch_tools(win, monkeypatch):
    doc = win.doc
    _start_sketch(win)
    passed = []
    real = win._pass_key
    monkeypatch.setattr(win, "_pass_key", lambda seq: (passed.append(seq), real(seq))[1])
    win.viewport.hover = face(win.plate)
    key(Qt.Key_P)
    assert win.sketcher.tool == "point" and win.act_planes.isChecked()
    assert passed == ["P"]                               # handed on once: the key did not come round again
    key(Qt.Key_H)
    assert win.sketcher.tool == "horizontal"
    for k, mods in ((Qt.Key_Y, Qt.NoModifier), (Qt.Key_K, Qt.NoModifier), (Qt.Key_J, Qt.NoModifier),
                    (Qt.Key_P, SHIFT), (Qt.Key_Y, SHIFT), (Qt.Key_I, SHIFT), (Qt.Key_T, SHIFT), (Qt.Key_H, SHIFT)):
        key(k, mods)
    assert doc.hidden_parts == set() and doc.hidden_features == set() and win.viewport.transparent_ids == set()
    assert win.show_connectors and win.show_axes and win.act_planes.isChecked() and win.mode == "sketch"
    assert len(passed) == 9                              # P and the eight above; plain H was never taken
    win.left.features.setFocus()                         # focus in a list: the sketcher still gets the key
    QApplication.processEvents()
    key(Qt.Key_S)
    win.left.features.setFocus()
    key(Qt.Key_P)
    assert win.sketcher.tool == "point"
    key(Qt.Key_X, SHIFT)                                 # the section view is the exception: it works everywhere
    assert win.section_panel is not None
    key(Qt.Key_X, SHIFT)
    win.cancel_sketch()


def test_in_a_drawing_the_canvas_gets_the_key(win, monkeypatch):
    g = DrawingDoc()
    g.name = "Drawing T"
    win._add_tab(g)
    QApplication.processEvents()
    assert win.is_dwg
    got = []
    monkeypatch.setattr(win.dwg, "key", lambda e: e.key() == Qt.Key_Shift
                        or got.append((e.key(), e.modifiers(), e.text())) or True)
    win.dwg.canvas.setFocus()
    QApplication.processEvents()
    key(Qt.Key_P)
    key(Qt.Key_T, SHIFT)
    assert got == [(Qt.Key_P, Qt.NoModifier, "p"), (Qt.Key_T, SHIFT, "T")]
    assert win.act_planes.isChecked() and win.viewport.transparent_ids == set()


def test_in_a_variable_studio_the_keys_do_nothing_to_the_view(win):
    win._add_tab(VariableStudioDoc())
    QApplication.processEvents()
    for k, mods in ((Qt.Key_P, Qt.NoModifier), (Qt.Key_Y, Qt.NoModifier), (Qt.Key_K, Qt.NoModifier),
                    (Qt.Key_T, SHIFT), (Qt.Key_P, SHIFT), (Qt.Key_H, SHIFT)):
        key(k, mods, QApplication.focusWidget() or win)
    assert win.act_planes.isChecked() and win.show_connectors and win.show_axes


def test_typing_in_a_text_box_types(win):
    box_ = QLineEdit(win)
    box_.show()
    box_.setFocus()
    QApplication.processEvents()
    assert QApplication.focusWidget() is box_
    win.viewport.hover = face(win.plate)
    key(Qt.Key_P)
    key(Qt.Key_Y)
    key(Qt.Key_T, SHIFT)
    key(Qt.Key_K)
    assert box_.text().lower() == "pytk"                 # (QTest types the lower-case letter with Shift too)
    assert win.act_planes.isChecked() and win.show_connectors
    assert win.doc.hidden_parts == set() and win.viewport.transparent_ids == set()


def test_a_passed_key_is_not_passed_again_from_inside(win):
    _start_sketch(win)
    seen = []
    real = win.viewport.keyPressEvent

    def nested(e):
        seen.append(e.key())
        win._pass_key("P")                               # a handler asking again must not loop
        real(e)
    win.viewport.keyPressEvent = nested
    try:
        win._pass_key("P")
    finally:
        del win.viewport.keyPressEvent
    assert seen == [Qt.Key_P] and win.sketcher.tool == "point"
    win.cancel_sketch()


def test_search_tools_lists_the_new_commands(win, awin):
    got = {c.name for c in win._palette_commands()}
    assert {"Show or hide planes", "Show or hide construction geometry", "Show or hide mate connectors",
            "Show or hide sketches", "Hide", "Isolate", "Show all parts", "Make transparent"} <= got
    win.viewport.selection = [face(win.plate)]
    next(c for c in win._palette_commands() if c.name == "Make transparent").action()
    assert win.viewport.transparent_ids == {win.plate}
    got = [c.name for c in awin._palette_commands()]
    assert {"Make transparent", "Show or hide mate connectors", "Show or hide mates", "Show or hide sketches"} <= set(got)
    assert got.count("Hide") == got.count("Isolate") == got.count("Show all") == 1


# ------------------------------------------------------------------ Assembly
def hidden(w):
    return {i["id"] for i in w.doc.instances if i.get("hidden")}


def test_assembly_p_does_nothing_and_says_so(awin):
    acts = awin.asm.panel.vis_actions
    before = {k: a.isChecked() for k, a in acts.items()}
    key(Qt.Key_P)
    assert {k: a.isChecked() for k, a in acts.items()} == before and hidden(awin) == set()
    assert "no planes" in awin.status.currentMessage()


def test_assembly_shift_p_switches_connectors_and_mates_together(awin):
    acts = awin.asm.panel.vis_actions
    assert acts["connectors"].isChecked() and not acts["mates"].isChecked()
    key(Qt.Key_P, SHIFT)
    assert not acts["connectors"].isChecked() and not acts["mates"].isChecked() and acts["sketches"].isChecked()
    assert "Shift+P shows them again" in awin.status.currentMessage()
    key(Qt.Key_P, SHIFT)
    assert acts["connectors"].isChecked() and acts["mates"].isChecked()


def test_assembly_k_shift_h_and_j_tick_the_show_dropdown(awin):
    acts = awin.asm.panel.vis_actions
    for k, mods, name in ((Qt.Key_K, Qt.NoModifier, "connectors"), (Qt.Key_H, SHIFT, "sketches"),
                          (Qt.Key_J, Qt.NoModifier, "mates")):
        was = acts[name].isChecked()
        others = {n: a.isChecked() for n, a in acts.items() if n != name}
        key(k, mods)
        assert acts[name].isChecked() is (not was), name
        assert {n: a.isChecked() for n, a in acts.items() if n != name} == others
        assert "again" in awin.status.currentMessage()
        key(k, mods)
        assert acts[name].isChecked() is was, name
    key(Qt.Key_K)                                        # the dropdown redraws the scene
    assert awin.viewport.triads == []


def test_assembly_y_hides_selected_or_hovered_and_shift_y_shows_all(awin):
    a, ids = awin.doc, awin.ids
    awin.asm.panel.select_instances({ids[0]})
    awin.viewport.hover = face(ids[2])                   # the selection wins over the mouse
    key(Qt.Key_Y)
    assert hidden(awin) == {ids[0]} and "Shift+Y" in awin.status.currentMessage()
    key(Qt.Key_Y)                                        # the selected one is already hidden: the hovered one goes
    assert hidden(awin) == {ids[0], ids[2]}
    a.undo()
    assert hidden(awin) == {ids[0]}                      # one undo step per press
    key(Qt.Key_Y, SHIFT)
    assert hidden(awin) == set()
    awin.asm.panel.select_instances(set())
    awin.viewport.hover = None
    key(Qt.Key_Y)
    assert hidden(awin) == set() and awin.status.currentMessage().startswith("Nothing to hide")


def test_assembly_shift_i_isolates_selected_or_hovered(awin):
    ids = awin.ids
    awin.viewport.selection = [face(ids[0]), face(ids[1])]
    key(Qt.Key_I, SHIFT)
    assert hidden(awin) == {ids[2]}
    key(Qt.Key_Y, SHIFT)
    awin.viewport.selection = []
    awin.viewport.hover = face(ids[1])
    key(Qt.Key_I, SHIFT)
    assert hidden(awin) == {ids[0], ids[2]}
    key(Qt.Key_Y, SHIFT)
    awin.viewport.hover = None
    key(Qt.Key_I, SHIFT)
    assert hidden(awin) == set() and awin.status.currentMessage().startswith("Nothing to isolate")


def test_assembly_shift_t_makes_instances_transparent_in_the_view_only(awin):
    a, ids, vp = awin.doc, awin.ids, awin.viewport
    saved = json.dumps(a.to_json(), sort_keys=True, default=str)
    vp.hover = face(ids[1])
    key(Qt.Key_T, SHIFT)
    assert vp.transparent_ids == {ids[1]} and vp.reference_ids == set()
    assert 0 < vp._batch["nsolid"] < vp._batch["ntri"] and vp._pick_exists(face(ids[1]))
    awin.asm.show_scene()                                # a redraw keeps it
    assert vp.transparent_ids == {ids[1]} and vp._batch["nsolid"] < vp._batch["ntri"]
    assert json.dumps(a.to_json(), sort_keys=True, default=str) == saved
    awin.asm.panel.select_instances({ids[1]})
    vp.hover = None
    key(Qt.Key_T, SHIFT)
    assert vp.transparent_ids == set() and vp._batch["nsolid"] == vp._batch["ntri"]
    awin.asm.panel.select_instances({ids[0], ids[2]})
    key(Qt.Key_T, SHIFT)
    awin.asm.panel.select_instances(set())
    key(Qt.Key_T, SHIFT)                                 # nothing targeted: all solid
    assert vp.transparent_ids == set()


def test_assembly_shift_x_toggles_the_section_view(awin):
    key(Qt.Key_X, SHIFT)
    assert awin.section_panel is not None
    key(Qt.Key_X, SHIFT)
    assert awin.section_panel is None


def test_assembly_mate_dialog_uses_the_hovered_part_only(awin):
    ids, vp = awin.ids, awin.viewport
    awin.asm.start_mate("fastened")
    assert awin.mode == "dialog"
    vp.selection = [face(ids[0])]
    key(Qt.Key_Y)
    assert hidden(awin) == set()
    vp.hover = face(ids[2])
    key(Qt.Key_T, SHIFT)
    assert vp.transparent_ids == {ids[2]}
    key(Qt.Key_Y)
    assert hidden(awin) == {ids[2]} and awin.mode == "dialog"
    awin.asm.mate_dialog.reject()


def test_assembly_i_still_opens_insert(awin):
    key(Qt.Key_I)
    assert awin.asm.insert_panel is not None
    awin.asm.close_insert()
