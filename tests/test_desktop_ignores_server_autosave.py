"""I2.6: the desktop never sees the server's autosave. With a server autosave present under the data directory, the
desktop's own orphan scan (`MainWindow._orphaned_autosaves`) returns nothing for it and no file under
`autosave-server/` is removed, written or changed, however the window starts, autosaves and closes.

The data directory is placed where it is closest to the desktop's autosave folder: it is the parent of the folder the
desktop scans (`<data>/autosave`), so `autosave-server` is its sibling. A real desktop orphan (a folder named after a
process id that is not alive) sits beside it, to show the scan finds what is its own. Offscreen Qt, a MainWindow in
engine mode as the real app starts it; the window is closed at the end."""
import hashlib
import os
import subprocess
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from freeshape import autosave as AS  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402


def _tree(path):
    """Every file and folder under `path` with the hash of each file."""
    out = {}
    for d, dirs, files in os.walk(path):
        for n in dirs:
            out[os.path.relpath(os.path.join(d, n), path)] = None
        for n in files:
            full = os.path.join(d, n)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, path)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_the_desktops_orphan_scan_ignores_the_servers_autosave_and_removes_nothing_of_it(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    data = tmp_path / "FreeShape"                                          # the data dir, parent of the desktop's folder
    desktop_root = data / "autosave"
    desktop_root.mkdir(parents=True)
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(data))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(desktop_root)))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    # a server autosave, written as the server writes it, and a decoy named the way the desktop's scan looks for
    instance = "a1" * 16
    AS.write(str(data), instance, {
        "document": "doc-0001", "name": "Doc", "entries": [{"id": "T1", "kind": "studio", "file": "a.fshape", "name": "A"}],
        "added": [], "renamed": [], "deleted": [], "tabs": {"T1": (b'{"features": []}', None)}, "blobs": {},
        "manifest_base": None})
    decoy = data / AS.DIR_NAME / str(_dead_pid())
    decoy.mkdir()
    (decoy / "Part Studio 1.fshape").write_text("{}")
    # a real desktop orphan: a folder named after a process that is gone
    orphan_dir = desktop_root / str(_dead_pid())
    orphan_dir.mkdir()
    orphan = orphan_dir / "Lost.fshape"
    orphan.write_text("{}")

    served = _tree(data / AS.DIR_NAME)
    assert len(served) > 4

    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    try:
        app.processEvents()
        assert w.autosave_root() == str(desktop_root)
        assert w._orphaned_autosaves() == [str(orphan)]                    # its own orphan, and nothing of the server's
        assert _tree(data / AS.DIR_NAME) == served
        w.autosave()                                                       # the window's own autosave pass
        w.offer_recovery()                                                 # the prompt (patched to No): it must not look there
        assert _tree(data / AS.DIR_NAME) == served
    finally:
        for d in w.docs.values():
            d.dirty = False
        if isValid(w):
            w.close()
            if w.studio_engine is not None:
                w.studio_engine.shutdown()
            w.deleteLater()
        P.SPARES.clear()
        app.processEvents()
    assert _tree(data / AS.DIR_NAME) == served                              # nothing was removed on the way out either
