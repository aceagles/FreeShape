"""I3.5: Mate connector at origin. Part Studio: right-click the Origin (viewport or feature list) opens the Mate
connector dialog with the origin preselected and an Owner part row. Assembly: right-click an instance adds an
explicit connector at its part's origin, usable as a named mate end. Offscreen."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from conftest import add, only_part  # noqa: E402
from freeshape import assembly as A  # noqa: E402
from freeshape.engine import InProcessEngine  # noqa: E402
from test_connector_frames import assembly, plate_studio, src  # noqa: E402

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402


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
    yield w
    w.deleteLater()


def menu_actions(monkeypatch):
    """Replace QMenu where the code under test looks it up with a recording subclass whose exec()/popup() never block;
    returns the list the menus' actions land in. Menus are kept alive so the actions outlive them."""
    from PySide6 import QtWidgets
    from PySide6.QtWidgets import QMenu
    import freeshape.ui.mainwindow as mw
    seen, keep = [], []

    class RecMenu(QMenu):
        def exec(self, *a, **k):
            keep.append(self)
            seen.extend(self.actions())
            return None

        def exec_(self, *a, **k):
            return self.exec(*a, **k)

        def popup(self, *a, **k):
            keep.append(self)
            seen.extend(self.actions())

    monkeypatch.setattr(mw, "QMenu", RecMenu)
    monkeypatch.setattr(QtWidgets, "QMenu", RecMenu)      # assembly_ui imports it inside each handler
    return seen


def find(actions, name):
    return next((a for a in actions if a.objectName() == name), None)


def test_feature_list_origin_menu_opens_dialog_and_commit_publishes(win, monkeypatch, tmp_path):
    seen = menu_actions(monkeypatch)
    win._tree_context("origin:None", win.pos())
    act = find(seen, "mate_connector_at_origin")
    assert act is not None and act.text() == "Mate connector at origin"
    act.trigger()
    assert win.mode == "dialog" and win.dialog.ftype == "mate_connector"
    p = win.dialog.feature["params"]
    assert p["ref"] == {"origin": True} and p["owner"] == only_part(win.doc).id   # one part: filled in
    win.commit_dialog()
    f = [f for f in win.doc.features if f["type"] == "mate_connector"][-1]
    assert win.doc.errors.get(f["id"]) is None
    c = win.doc.state.connectors[f["id"]]
    assert c["part"] == only_part(win.doc).id
    assert c["frame"] == {"o": [0.0, 0.0, 0.0], "z": [0.0, 0.0, 1.0], "x": [1.0, 0.0, 0.0]}
    path = str(tmp_path / "s.fshape")
    win.doc.save(path)
    eng = InProcessEngine()
    try:
        out = eng.outputs(eng.open_tab(path))
    finally:
        eng.close()
    assert [x.id for x in out.connectors] == [f["id"]] and np.allclose(out.connectors[0].frame["o"], 0)


def test_viewport_origin_right_click_offers_the_entry(win, monkeypatch):
    from PySide6.QtCore import QPoint
    seen = menu_actions(monkeypatch)
    vp = win.viewport
    sp = vp.cam.to_screen(np.zeros(3))
    win._viewport_context(None, vp.mapToGlobal(QPoint(int(sp[0]), int(sp[1]))))
    assert find(seen, "mate_connector_at_origin") is not None
    seen.clear()
    win._viewport_context(None, vp.mapToGlobal(QPoint(int(sp[0]) + 80, int(sp[1]) + 80)))
    assert find(seen, "mate_connector_at_origin") is None


def test_two_parts_need_an_owner_before_commit(win):
    doc = win.doc
    from conftest import region_refs
    sid = next(f["id"] for f in doc.features if f["type"] == "sketch")
    add(doc, "extrude", regions=region_refs(doc, sid), depth=3.0, op="new")
    assert len(doc.state.parts) == 2
    win.mate_connector_at_origin()
    d = win.dialog
    assert d.feature["params"]["owner"] is None
    d.accept()
    assert win.mode == "dialog" and "Pick the part" in d.err.text()       # refused
    d.widgets["owner"].setCurrentIndex(1)
    assert d.feature["params"]["owner"] in doc.state.parts
    d.accept()
    assert win.mode == "idle"


# ------------------------------------------------------------------------------------------------ Assembly
def two(tmp=None):
    studio, _ = plate_studio()
    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 10, 8).Shape()
    a = assembly(studio, {"block": block})
    ip = a.insert(src("plate"))
    ib = a.insert(src("block"))
    return studio, a, ip, ib


def test_instance_menu_entry_adds_one_connector_that_follows_the_instance(win, monkeypatch):
    from freeshape.ui.assembly_ui import AssemblyController  # noqa: F401
    studio, a, ip, ib = two()
    win._add_tab(a)
    seen = menu_actions(monkeypatch)
    win.asm.instance_menu(ib, win.pos())
    act = find(seen, "add_connector_instance_origin")
    assert act is not None and act.text() == "Add mate connector to instance origin"
    act.trigger()
    act.trigger()
    assert len(a.connectors) == 1 and a.connectors[0]["inst"] == ib and a.connectors[0]["kind"] == "origin"
    end = a.named_connector(ib, a.connectors[0]["id"])
    T = np.eye(4)
    T[:3, 3] = [5, 6, 7]
    a.instance(ib)["T"] = T.tolist()
    fr = a.world_frame(end)
    assert np.allclose(fr["o"], [5, 6, 7]) and np.allclose(fr["z"], [0, 0, 1]) and np.allclose(fr["x"], [1, 0, 0])
    assert len(win.viewport.triads) >= 1


def fastened(a, ip, ib, flip=False):
    ca = a.add_instance_origin_connector(ip)
    cb = a.add_instance_origin_connector(ib)
    m = a.make_mate("fastened")
    m["a"] = a.named_connector(ip, ca)
    m["b"] = a.named_connector(ib, cb)
    m["flip"] = flip                                  # default opposes the Z axes (faces touching); flip aligns them
    a.add_mate(m)
    return m


def test_fastened_on_instance_origins_coincide_and_align():
    studio, a, ip, ib = two()
    a.set_fixed(ip, True)
    T = np.eye(4)
    T[:3, :3] = A.rot_about([1, 1, 0], 0.7)[:3, :3]
    T[:3, 3] = [30, -20, 15]
    a.instance(ib)["T"] = T.tolist()
    fastened(a, ip, ib, flip=True)
    a.solve()
    assert not a.errors, a.errors
    assert np.allclose(a.T(ib), a.T(ip), atol=1e-6)


def test_round_trip_delete_and_red(tmp_path):
    studio, a, ip, ib = two()
    m = fastened(a, ip, ib)
    a.path = str(tmp_path / "x.fasm")
    a.save()
    assert len(json.load(open(a.path))["connectors"]) == 2
    b = A.AssemblyDoc.load(a.path, resolver=a.resolver)
    assert b.connectors == a.connectors and not b.errors
    # a connector that is gone turns the mate red
    b.connectors = [c for c in b.connectors if c["inst"] != ib]
    b.refresh()
    assert b.errors.get(m["id"], "").startswith("Lost its mate connector")
    # deleting the instance removes its connector (and the mate on it); undo brings both back
    a.delete_instances([ib])
    assert [c["inst"] for c in a.connectors] == [ip] and a.mates == []
    a.undo()
    assert len(a.connectors) == 2 and len(a.mates) == 1


def test_file_without_connectors_key_is_unchanged(tmp_path):
    studio, a, ip, ib = two()
    a.set_fixed(ip, True)
    m = a.make_mate("fastened")
    m["a"] = a.connector(ip, "face", 0)
    m["b"] = a.connector(ib, "face", 0)
    a.add_mate(m)
    a.solve()
    a.path = str(tmp_path / "x.fasm")
    a.save()
    d = json.load(open(a.path))
    assert "connectors" not in d
    b = A.AssemblyDoc.load(a.path, resolver=a.resolver)
    b.solve()
    assert b.connectors == [] and np.allclose(b.T(ib), a.T(ib), atol=1e-6)


def test_click_on_a_triad_over_empty_space_reaches_the_dialog(win):
    studio, a, ip, ib = two()
    win._add_tab(a)
    cid = a.add_instance_origin_connector(ip)
    T = np.eye(4)
    T[:3, 3] = [500, 500, 500]                       # far from any geometry
    a.instance(ip)["T"] = T.tolist()
    win.asm.start_mate("fastened")
    d = win.asm.mate_dialog
    vp = win.viewport
    assert vp.miss_handler == d.handle_miss
    sp = vp.cam.to_screen(np.array([500.0, 500.0, 500.0]))
    vp.last_click = (float(sp[0]), float(sp[1]))
    vp.miss_handler()
    assert d.mate.get("a") and d.mate["a"].get("named") == cid
