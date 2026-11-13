"""I3.3: the Mate connector tool in the Part Studio window: toolbar, command search, the dialog's element pick
and snap points, the viewport's triads and the feature row's highlight. Offscreen."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import only_part  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui import dialogs  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_connector_frames import H, plate_studio  # noqa: E402
from test_studio_connectors import top_index  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    doc, _ = plate_studio()
    w._add_tab(doc)
    app.processEvents()
    assert w.doc is doc
    yield w
    w.deleteLater()


def make_connector(w, **extra):
    doc = w.doc
    part = only_part(doc)
    w.start_feature("mate_connector")
    assert w.mode == "dialog" and w.dialog.ftype == "mate_connector"
    w.dialog.handle_pick(Pick("face", (part.id, top_index(part.shape))))
    w.dialog.feature["params"].update(extra)
    w.commit_dialog()
    return [f for f in doc.features if f["type"] == "mate_connector"][-1]


def test_start_pick_face_commit_adds_feature_and_one_triad(win):
    n = len(win.doc.features)
    f = make_connector(win)
    assert len(win.doc.features) == n + 1 and win.doc.errors.get(f["id"]) is None
    assert f["params"]["ref"].get("name") and f["params"]["kind"] == "face"      # found by element name
    assert win.doc.state.connectors[f["id"]]["part"] == only_part(win.doc).id
    assert len(win.viewport.triads) == 1
    assert win.mode == "idle" and win.viewport.snap_provider is None


def test_command_search_has_mate_connector(win):
    cmds = {c.key: c for c in win._palette_commands()}
    assert "mate_connector" in cmds
    cmds["mate_connector"].action()
    assert win.mode == "dialog" and win.dialog.ftype == "mate_connector"
    assert "mate_connector" in dialogs.SCHEMAS


def test_edit_and_flip_reverses_z(win):
    f = make_connector(win)
    z0 = np.array(win.doc.state.connectors[f["id"]]["frame"]["z"])
    win.start_feature("mate_connector", edit_fid=f["id"])
    win.dialog.widgets["flip"].setChecked(True)
    win.commit_dialog()
    z1 = np.array(win.doc.state.connectors[f["id"]]["frame"]["z"])
    assert np.allclose(z1, -z0, atol=1e-9) and np.allclose(z0, [0, 0, 1], atol=1e-9)
    assert len(win.viewport.triads) == 1 and np.allclose(win.viewport.triads[0]["z"], z1)


def test_drawn_triad_origin_is_the_frame_origin(win):
    f = make_connector(win)
    fr = win.doc.state.connectors[f["id"]]["frame"]
    tri = win.viewport.triads[0]
    assert np.allclose(tri["o"], fr["o"], atol=1e-9)
    top = K.face_info(K.faces(only_part(win.doc).shape)[top_index(only_part(win.doc).shape)])
    assert np.allclose(tri["o"], top["c"], atol=1e-9) and abs(tri["o"][2] - H) < 1e-9
    assert np.allclose(np.cross(tri["x"], tri["y"]), tri["z"], atol=1e-9)


def test_feature_row_and_selecting_it_highlights_the_triad(win):
    f = make_connector(win)
    lst = win.left.features
    assert any(f["name"] in (lst.item(i).text() or "") for i in range(lst.count()))
    plain = win.viewport.triads[0]
    assert "size_px" not in plain
    for i in range(lst.count()):
        if lst.item(i).text() == f["name"]:
            lst.setCurrentRow(i)
            lst.item(i).setSelected(True)
    win._update_feature_hl()
    assert win.viewport.triads[0]["size_px"] > 34
    assert np.allclose(win.viewport.triads[0]["o"], plain["o"])


def test_snap_points_and_a_snap_pick(win):
    part = only_part(win.doc)
    pick = Pick("face", (part.id, top_index(part.shape)))
    win.start_feature("mate_connector")
    snaps = win.dialog.snap_candidates(pick)
    assert {"vertex", "mid", "centroid"} <= {s["type"] for s in snaps}
    mid = next(s for s in snaps if s["type"] == "mid")
    vp = win.viewport
    vp.snap_base, vp.snap_hi = pick, mid
    win.dialog.handle_pick(Pick("face", (part.id, 0)))              # the highlighted snap wins over the pick
    p = win.dialog.feature["params"]
    assert p["snap"]["type"] == "mid" and p["snap"]["edge_ref"] and p["x_ref"] is not None
    win.commit_dialog()
    f = [g for g in win.doc.features if g["type"] == "mate_connector"][0]
    assert win.doc.errors.get(f["id"]) is None
    o = np.array(win.doc.state.connectors[f["id"]]["frame"]["o"])
    assert np.allclose(o, mid["p"], atol=1e-9)


def test_edge_pick(win):
    part = only_part(win.doc)
    shape = part.shape
    i = next(i for i, e in enumerate(K.edges(shape)) if abs(K.edge_info(e)["p0"][2] - H) < 1e-6
             and abs(K.edge_info(e)["p1"][2] - H) < 1e-6)
    win.start_feature("mate_connector")
    win.dialog.handle_pick(Pick("edge", (part.id, i)))
    win.commit_dialog()
    f = [g for g in win.doc.features if g["type"] == "mate_connector"][0]
    assert f["params"]["kind"] == "edge" and win.doc.errors.get(f["id"]) is None
    assert len(win.viewport.triads) == 1


def test_owner_part_row_is_offered_for_a_face_connector(win):
    """The Owner part row shows for a face pick too; choosing another part makes the connector that part's."""
    from test_studio_connectors import second_part
    doc = win.doc
    plate = only_part(doc)
    other = second_part(doc)
    win.start_feature("mate_connector")
    d = win.dialog
    d.handle_pick(Pick("face", (plate.id, top_index(plate.shape))))
    cb = d.widgets["owner"]
    assert cb.isVisibleTo(d), "the Owner part row must show for a face or edge connector"
    assert cb.currentData() is None and d.feature["params"].get("owner") is None      # default: the face's own part
    cb.setCurrentIndex(cb.findData(other))
    win.commit_dialog()
    f = [x for x in doc.features if x["type"] == "mate_connector"][-1]
    assert not doc.errors.get(f["id"])
    assert doc.state.connectors[f["id"]]["part"] == other
