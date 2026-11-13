"""A MainWindow leaked by one test must not autosave into a later test's folder.

One self-contained test (CI shards by test id, so tests in a file may not depend on each other). It leaks a dirty
window with a 20 ms autosave timer, shows the stray autosave really happens, then runs what the autouse guard in
conftest.py does after every test (`stop_leaked_autosave_timers`) and shows the leaked window stays quiet."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import TOP, add, rect_lines, region_refs, sketch_feature, stop_leaked_autosave_timers  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402


def _run_events(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_a_leaked_window_autosaves_until_the_guard_stops_it(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_AUTOSAVE_MS", "20")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path)))
    w = MW.MainWindow()
    try:
        sid = sketch_feature(w.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)     # autosave skips an empty doc
        add(w.doc, "extrude", regions=region_refs(w.doc, sid), depth=10.0, op="new")
        w.doc.dirty = True
        folder = tmp_path / "autosave" / "pid"
        os.makedirs(folder)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(folder)))

        _run_events(300)                                    # the leak is real: the window writes into the folder
        assert os.listdir(folder) != []

        for f in os.listdir(folder):
            os.remove(folder / f)
        stop_leaked_autosave_timers()                       # what the autouse fixture does after each test
        _run_events(300)
        assert os.listdir(folder) == []
        assert not w._autosave_timer.isActive()
    finally:
        w._autosave_timer.stop()
        w.doc.dirty = False
        w.close()
        w.deleteLater()
        app.processEvents()
