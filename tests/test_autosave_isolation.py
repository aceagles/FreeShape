"""A MainWindow leaked by one test must not autosave into a later test's folder.

The first test leaves a dirty window alive on purpose, with its autosave timer firing every 20 ms. The second
patches the autosave folder as tests/test_mainwindow_store.py does and lets Qt run; the autouse guard in
conftest.py (`_stop_leaked_autosave_timers`) must have stopped the leaked timer. Order within the file matters."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import TOP, add, rect_lines, region_refs, sketch_feature  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

_LEAKED = []        # keeps the window alive past its test, as a test that forgets to close it does


def test_a_leaks_a_dirty_window_with_a_running_autosave_timer(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_AUTOSAVE_MS", "20")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path)))
    w = MW.MainWindow()
    sid = sketch_feature(w.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)     # autosave skips an empty doc
    add(w.doc, "extrude", regions=region_refs(w.doc, sid), depth=10.0, op="new")
    w.doc.dirty = True
    app.processEvents()
    _LEAKED.append(w)
    assert w._autosave_timer.isActive()


def test_b_a_later_test_sees_no_stray_autosave(tmp_path, monkeypatch):
    app = QApplication.instance()
    assert _LEAKED, "test_a must run first"
    folder = tmp_path / "autosave" / "pid"
    os.makedirs(folder)
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(folder)))
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)           # ~15 intervals of the leaked window's 20 ms timer
    loop.exec()
    app.processEvents()
    assert os.listdir(folder) == []
    assert not _LEAKED[0]._autosave_timer.isActive()
