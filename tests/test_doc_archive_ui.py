"""I11.4: Save Document archive and Open Document archive in the desktop. Offscreen, `MainWindow(engine=True)`
as the real app starts it; the two file dialogs are replaced by the paths the test picks."""
import os
import shutil
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton  # noqa: E402

from freeshape import doc_registry as R  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from test_engine_picking import _spawned  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRACKET = os.path.join(ROOT, "examples", "documents", "bracket")
SETTLE = 180


@pytest.fixture
def window(tmp_path, monkeypatch):
    """A window in engine mode and the list of message boxes it showed (none is modal here)."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    shown = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, _n=name, **k: shown.append((_n, a[2:3])) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    before = set(_spawned())
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    app.processEvents()
    try:
        yield w, shown
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


def _document_menu(w):
    (menu,) = [b.menu() for b in w.findChildren(QToolButton) if b.objectName() == "menuButton" and b.menu()
               and "Open Document folder…" in [a.text() for a in b.menu().actions()]]
    return menu


def _trigger(w, text):
    (action,) = [a for a in _document_menu(w).actions() if a.text() == text]
    action.trigger()


def _volumes(doc):
    return {pid: K.volume(p.shape) for pid, p in doc.state.parts.items()}


def test_document_menu_places_the_archive_actions(window):
    w, _shown = window
    texts = [a.text() for a in _document_menu(w).actions()]
    assert texts.index("Open Document archive…") == texts.index("Open Document folder…") + 1
    assert texts.index("Save Document archive…") == texts.index("Save as…") + 1


def test_save_then_open_document_archive_gives_a_second_document_with_the_same_part(window, tmp_path):
    w, shown = window
    src = str(tmp_path / "received" / "bracket")
    shutil.copytree(BRACKET, src)
    first = w.open_document(src)
    assert first is not None and w.studio_engine.settle(SETTLE)
    before = _volumes(w.doc)
    assert len(before) == 1 and all(v > 0 for v in before.values())
    first_path, first_id = first.path, first.id

    os.makedirs(tmp_path / "out")
    archive = str(tmp_path / "out" / "bracket")                # the extension is added
    w._pick_archive_save_path = lambda name: archive
    _trigger(w, "Save Document archive…")
    assert shown == []
    assert zipfile.is_zipfile(archive + ".fsdoc")
    assert not os.path.exists(archive + ".fsdoc.tmp")

    w._pick_archive_to_open = lambda: archive + ".fsdoc"
    _trigger(w, "Open Document archive…")
    assert shown == []
    second = w.folder
    assert second is not None and second is not first
    assert second.id != first_id and os.path.realpath(second.path) != os.path.realpath(first_path)
    assert os.path.dirname(os.path.realpath(second.path)) == os.path.realpath(R.managed_root())
    assert [(t.kind, t.name) for t in second.tabs] == [(t.kind, t.name) for t in first.tabs]
    assert w.studio_engine.settle(SETTLE)
    after = _volumes(w.doc)
    assert after.keys() == before.keys()
    for pid in before:
        assert after[pid] == pytest.approx(before[pid], rel=1e-9)
