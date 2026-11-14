"""I1.4: the Axis tool in the Part Studio window: command, toolbar button, dialog picks, preview, edit and cancel.
Offscreen main window."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import only_part  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, plate_studio  # noqa: E402
from test_studio_connectors import top_index  # noqa: E402

W, D = 60.0, 40.0


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, _ = plate_studio(W, D)
    w._add_tab(doc)
    w.resize(900, 700)
    w.show()
    app.processEvents()
    assert w.doc is doc
    yield w
    w.deleteLater()


def _edge(shape, c, d):
    """Index of the straight edge through c along d."""
    for i, e in enumerate(K.edges(shape)):
        info = K.edge_info(e)
        if info["type"] == int(K.GeomAbs_Line) and abs(abs(np.dot(info["d"], d)) - 1) < 1e-6 \
                and np.linalg.norm(np.array(info["c"]) - c) < 1e-6:
            return i
    raise AssertionError("no such edge")


def _vertex(shape, p):
    pts = K.vertex_points(shape)
    return next(i for i, q in enumerate(pts) if np.linalg.norm(np.asarray(q) - p) < 1e-6)


def _axes(doc):
    return [f for f in doc.features if f["type"] == "axis"]


def _start(w):
    w.start_feature("axis")
    assert w.mode == "dialog" and w.dialog.ftype == "axis"
    return w.dialog


def test_command_and_toolbar(win):
    cmds = {c.key: c for c in win._palette_commands()}
    assert "axis" in cmds and cmds["axis"].name == "Axis"
    for word in ("datum", "construction", "centreline"):
        assert word in cmds["axis"].keywords
    from PySide6.QtWidgets import QToolButton
    btns = [b for b in win.findChildren(QToolButton) if b.property("icon_name") == "axis"]
    assert len(btns) == 1
    assert not btns[0].icon().isNull()
    btns[0].click()
    assert win.mode == "dialog" and win.dialog.ftype == "axis"


def test_edge_pick_commits_axis_on_edge(win):
    part = only_part(win.doc)
    ei = _edge(part.shape, np.array([W / 2, 0, 0]), np.array([1.0, 0, 0]))
    dlg = _start(win)
    dlg.handle_pick(Pick("edge", (part.id, ei)))
    win.commit_dialog()
    (f,) = _axes(win.doc)
    assert [r["k"] for r in f["params"]["refs"]] == ["edge"]
    assert win.doc.errors.get(f["id"]) is None
    ax = win.doc.state.axes[f["id"]]
    assert abs(abs(ax["d"][0]) - 1) < 1e-6
    assert abs(ax["o"][1]) < 1e-6 and abs(ax["o"][2]) < 1e-6
    assert any(a[0] == ("axis", f["id"]) for a in win.viewport.axes)


def test_face_pick_records_click_and_detects_type(win):
    part = only_part(win.doc)
    vp = win.viewport
    s = vp.cam.to_screen(np.array([20.0, 15.0, H]))
    vp.last_click = (float(s[0]), float(s[1]))
    dlg = _start(win)
    dlg.handle_pick(Pick("face", (part.id, top_index(part.shape))))
    p = dlg.feature["params"]
    assert p["click"] is not None and abs(p["click"][2] - H) < 1e-3
    assert np.allclose(p["click"][:2], [20, 15], atol=0.5)
    assert F.AXIS_TYPE_NAMES["face_point"] == "Perpendicular to face at click"
    assert dlg._info_rows[0][1].text() == "Type: Perpendicular to face at click"
    win.commit_dialog()
    (f,) = _axes(win.doc)
    ax = win.doc.state.axes[f["id"]]
    assert abs(abs(ax["d"][2]) - 1) < 1e-6 and abs(ax["d"][0]) < 1e-6
    assert np.allclose(np.array(ax["o"])[:2], f["params"]["click"][:2], atol=1e-6) and abs(ax["o"][2] - H) < 1e-6


def test_removing_the_face_clears_the_click(win):
    part = only_part(win.doc)
    vp = win.viewport
    s = vp.cam.to_screen(np.array([20.0, 15.0, H]))
    vp.last_click = (float(s[0]), float(s[1]))
    dlg = _start(win)
    pk = Pick("face", (part.id, top_index(part.shape)))
    dlg.handle_pick(pk)
    assert dlg.feature["params"]["click"] is not None
    dlg.handle_pick(pk)                      # a second click on the same face removes it
    assert dlg.feature["params"]["refs"] == [] and dlg.feature["params"]["click"] is None
    win.cancel_dialog()


def test_two_vertices_and_opposite_direction(win):
    part = only_part(win.doc)
    a = _vertex(part.shape, np.array([0.0, 0, 0]))
    b = _vertex(part.shape, np.array([W, 0, 0]))
    dlg = _start(win)
    dlg.handle_pick(Pick("vertex", (part.id, a)))
    dlg.handle_pick(Pick("vertex", (part.id, b)))
    win.commit_dialog()
    (f,) = _axes(win.doc)
    assert F.axis_type(f["params"]) == "two_points"
    d0 = np.array(win.doc.state.axes[f["id"]]["d"])
    win.start_feature("axis", edit_fid=f["id"])
    win.dialog._set("flip", True)
    win.commit_dialog()
    d1 = np.array(win.doc.state.axes[f["id"]]["d"])
    assert np.allclose(d1, -d0)


def _connector(w):
    part = only_part(w.doc)
    w.start_feature("mate_connector")
    w.dialog.handle_pick(Pick("face", (part.id, top_index(part.shape))))
    w.commit_dialog()
    return [f for f in w.doc.features if f["type"] == "mate_connector"][-1]


def test_connector_pick_shows_connector_axis_choice(win):
    c = _connector(win)
    part = only_part(win.doc)
    dlg = _start(win)
    row = next(w for s, w in dlg.rows if s["key"] == "connector_axis")
    assert not row.isVisibleTo(dlg)
    vp = win.viewport
    tri = next(t for t in vp.triads if t["id"] == c["id"])
    s = vp.cam.to_screen(np.asarray(tri["o"], float))
    assert vp.pick(float(s[0]), float(s[1]), kinds={"connector"}) == Pick("connector", (c["id"],))
    dlg.handle_pick(Pick("connector", (c["id"],)))
    assert dlg.feature["params"]["refs"] == [{"k": "connector", "ref": {"fid": c["id"]}}]
    assert F.axis_type(dlg.feature["params"]) == "connector"
    assert row.isVisibleTo(dlg)
    dlg._set("connector_axis", "x")
    win.commit_dialog()
    (f,) = _axes(win.doc)
    assert win.doc.errors.get(f["id"]) is None
    assert abs(abs(win.doc.state.axes[f["id"]]["d"][0]) - 1) < 1e-6
    # with an edge instead, the choice is hidden
    win.start_feature("axis")
    ei = _edge(part.shape, np.array([W / 2, 0, 0]), np.array([1.0, 0, 0]))
    win.dialog.handle_pick(Pick("edge", (part.id, ei)))
    row = next(w for s, w in win.dialog.rows if s["key"] == "connector_axis")
    assert not row.isVisibleTo(win.dialog)
    win.cancel_dialog()


def test_reopen_shows_items_and_cancel_leaves_feature(win):
    part = only_part(win.doc)
    ei = _edge(part.shape, np.array([W / 2, 0, 0]), np.array([1.0, 0, 0]))
    dlg = _start(win)
    dlg.handle_pick(Pick("edge", (part.id, ei)))
    win.commit_dialog()
    (f,) = _axes(win.doc)
    before = [dict(g) for g in win.doc.features]
    saved = repr(f["params"])
    win.start_feature("axis", edit_fid=f["id"])
    fld = win.dialog.fields["refs_sel"]
    assert [k for k, _ in fld.items] == ["edge"] and fld.items[0][1] == f["params"]["refs"][0]["ref"]
    win.dialog.handle_pick(Pick("edge", (part.id, ei)))         # clicking it again removes it from the dialog only
    assert win.dialog.fields["refs_sel"].items == []
    win.cancel_dialog()
    assert win.mode == "idle"
    assert repr(win.doc.feature(f["id"])["params"]) == saved and len(win.doc.features) == len(before)
    assert f["id"] in win.doc.state.axes


def test_preview_holds_the_axis_while_dialog_is_open(win):
    part = only_part(win.doc)
    ei = _edge(part.shape, np.array([W / 2, 0, 0]), np.array([1.0, 0, 0]))
    dlg = _start(win)
    fid = dlg.feature["id"]
    dlg.handle_pick(Pick("edge", (part.id, ei)))
    dlg._timer.stop()
    dlg._do_preview()
    assert not dlg.err.isVisibleTo(dlg)
    assert fid not in win.doc.state.axes                          # not committed
    assert [a[0] for a in win.viewport.axes] == [("axis", fid)]   # but drawn from the previewed state
    win.cancel_dialog()
    assert win.viewport.axes == [] or all(a[0] != ("axis", fid) for a in win.viewport.axes)


def test_unfitting_selection_shows_the_build_error(win):
    part = only_part(win.doc)
    dlg = _start(win)
    dlg.handle_pick(Pick("face", (part.id, top_index(part.shape))))      # no click: a face alone fits nothing
    dlg._timer.stop()
    dlg._do_preview()
    assert "does not fit an axis type" in dlg.err.text()
    win.cancel_dialog()
