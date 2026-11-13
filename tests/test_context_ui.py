"""Edit in context, reference bodies and Update context in the windows (I5.5, ADR 0004). Offscreen."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from conftest import block_doc  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from freeshape.ui import panels as P  # noqa: E402
from tests.test_assembly import face, mate  # noqa: E402


@pytest.fixture
def rig(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    w = MW.MainWindow()
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    path = str(tmp_path / "Doc")
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    fd = w.folder
    plate = fd.tabs[0]
    plate.doc.__dict__.update(block_doc(40, 20, 10).__dict__)
    pin = fd.add_tab("studio", "Pin")
    pin.doc.__dict__.update(block_doc(10, 10, 10).__dict__)
    fd._attach(plate)
    fd._attach(pin)
    asm = fd.add_tab("assembly", "Asm")
    a = asm.doc
    ip = a.insert(L.studio_source(plate, f"{plate.doc.features[1]['id']}/1"), name="Plate")
    ipin = a.insert(L.studio_source(pin, f"{pin.doc.features[1]['id']}/1"), name="Pin")
    a.refresh(solve=False, notify=False)
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    m = mate(a, "fastened", top, pb)
    for t in (pin, asm):
        w._add_tab(t.doc)
    app.processEvents()
    assert w.doc is asm.doc
    w.asm.selected_instance_ids = lambda: [w._sel]
    w._sel = ip
    yield w, fd, plate, pin, asm, ip, ipin, m
    w.folder = None
    w.deleteLater()


def _contexts(studio):
    return [f for f in studio.features if f["type"] == "context"]


def _trigger(w):
    act = w.findChild(QAction, "edit_in_context")
    assert act is not None
    act.trigger()


def test_edit_in_context_adds_one_feature_and_switches(rig):
    w, fd, plate, pin, asm, ip, *_ = rig
    _trigger(w)
    assert w.doc is plate.doc and not w.is_asm
    ctx = _contexts(plate.doc)
    assert len(ctx) == 1 and ctx[0]["params"]["instance"] == ip and ctx[0]["params"]["assembly"] == asm.doc.uid
    assert plate.doc.features[-1] is ctx[0] or plate.doc.features[-1]["id"] == ctx[0]["id"]


def test_second_run_updates_the_capture(rig):
    w, fd, plate, pin, asm, ip, ipin, m = rig
    _trigger(w)
    first = _contexts(plate.doc)[0]["params"]["capture"]
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    asm.doc.replace_mate(dict(m, offset=5.0))
    _trigger(w)
    ctx = _contexts(plate.doc)
    assert len(ctx) == 1 and ctx[0]["params"]["capture"] != first


def test_row_status_and_update_context(rig):
    w, fd, plate, pin, asm, ip, ipin, m = rig
    _trigger(w)
    fid = _contexts(plate.doc)[0]["id"]

    def row():
        lst = w.left.features
        return next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(P.ROLE_ID) == fid)

    assert row().data(P.ROLE_STATUS) == "current"
    w.tabs.setCurrentIndex(w._tab_index(asm.doc))
    asm.doc.replace_mate(dict(m, offset=5.0))
    w.tabs.setCurrentIndex(w._tab_index(plate.doc))
    assert row().data(P.ROLE_STATUS) == "out of date" and "out of date" in row().text()
    cmds = {c.key: c for c in w._palette_commands()}
    cmds["update_context"].action()
    assert row().data(P.ROLE_STATUS) == "current" and "out of date" not in row().text()


def test_viewport_marks_context_body_as_reference(rig):
    w, fd, plate, pin, asm, ip, *_ = rig
    _trigger(w)
    ref_id = f"{_contexts(plate.doc)[0]['id']}/1"
    assert ref_id in w.viewport.reference_ids
    assert any(pid == ref_id for pid, _m, _c in w.viewport.parts)       # still in the scene, so pickable
    assert w.viewport._batch["nsolid"] < w.viewport._batch["ntri"]       # drawn in the translucent pass
    assert not (w.viewport.reference_ids - {ref_id})


def test_refused_for_another_document(rig):
    w, fd, plate, pin, asm, ip, ipin, m = rig
    inst = asm.doc.instance(ip)
    inst["source"] = dict(inst["source"], document="other-doc-id", version="v1")
    _trigger(w)
    assert w.is_asm and not _contexts(plate.doc)
    assert "another Document" in w.status.currentMessage()


def test_refused_for_read_only_version(rig):
    w, fd, plate, pin, asm, ip, *_ = rig
    fd.read_only = True
    _trigger(w)
    assert not _contexts(plate.doc) and "read-only" in w.status.currentMessage()
