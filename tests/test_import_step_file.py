"""I11.1: MainWindow.import_step_file builds the same import feature before and after the move of its feature
building into freeshape/step_import.py. Offscreen, `MainWindow(engine=True)` as the real app starts it."""
import hashlib
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from test_engine_picking import _spawned  # noqa: E402

SETTLE = 180


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    before = set(_spawned())
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    app.processEvents()
    try:
        yield w
    finally:
        for d in w.docs.values():
            d.dirty = False
        w.close()
        if w.studio_engine is not None:
            w.studio_engine.shutdown()
        P.SPARES.clear()
        for p in sorted(set(_spawned()) - before):
            try:
                os.kill(p, 9)
            except OSError:
                pass


def _box_step(tmp_path):
    path = str(tmp_path / "box.step")
    K.export_step([BRepPrimAPI_MakeBox(20.0, 20.0, 20.0).Shape()], path)
    return path


def _import_feature(doc):
    feats = [f for f in doc.features if f["type"] == "import"]
    assert len(feats) == 1
    return feats[0]


def _check(doc, raw):
    f = _import_feature(doc)
    assert f["name"] == "Import box.step" and f["suppressed"] is False
    assert f["params"]["filename"] == "box.step"
    assert sorted(f["params"]) == ["blob", "filename"]          # the bytes are held as a blob, as add_feature does
    assert f["params"]["blob"] == hashlib.sha256(raw).hexdigest()
    return f


def test_import_step_file_into_a_new_tab(window, tmp_path):
    path = _box_step(tmp_path)
    window.import_step_file(path)
    assert window.studio_engine.settle(SETTLE)
    assert window.doc.name == "box"
    _check(window.doc, open(path, "rb").read())


def test_import_step_file_into_the_current_tab(window, tmp_path):
    path = _box_step(tmp_path)
    before = window.doc
    window.import_step_file(path, True)
    assert window.studio_engine.settle(SETTLE)
    assert window.doc is before
    _check(window.doc, open(path, "rb").read())
