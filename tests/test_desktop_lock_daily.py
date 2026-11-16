"""I13.1: the Document lock on the daily desktop's branch. tests/test_desktop_lock.py (ported unedited) covers the
window's side of the lock; these cover what the daily desktop meets next to the browser server: a stale or fresh
`browser` lock, a lock left by a desktop that died, the lock released on close and on Quit, and saved files that do
not depend on the lock. Offscreen Qt, real Document folders, `MainWindow(engine=True)` as the real app starts it.
"HOLDER" below is the literal the server writes ("browser"); it is not read from the desktop's code."""
import os
import shutil
import subprocess
import sys
import time

import pytest
from PySide6.QtGui import QAction

from test_desktop_lock import SETTLE, _action, _disk, _document, _edit_depth, make_window  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from freeshape import doc_lock as L  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

HOLDER = "browser"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_window(make_window, folder):
    w = make_window(engine=True)
    assert w.open_document(folder) is not None
    assert w.studio_engine.settle(SETTLE)
    return w


def test_stale_browser_lock_opens_the_document_editable(make_window, tmp_path, monkeypatch):
    monkeypatch.setattr(MW.MainWindow, "LOCK_TIMEOUT", 1.0)
    folder = _document(tmp_path)
    L.acquire(folder, HOLDER, now=time.time() - 30)          # heartbeat 30 s old, timeout 1 s
    assert L.holder(folder, 1.0).stale
    w = _fresh_window(make_window, folder)
    assert w._lock_ro is None and w._lock_hold is not None and w._lock_hold.running()
    assert w.lock_banner.isHidden() and _action(w, "Save").isEnabled()
    held = L.holder(folder)                                  # judged by the default 60 s: the 10 s beat is fresh
    assert held.program == "desktop" and not held.stale      # the lock now names the desktop
    assert w.save() is True


def test_fresh_browser_lock_opens_read_only_and_names_the_holder(make_window, tmp_path):
    folder = _document(tmp_path)
    other = L.acquire(folder, HOLDER)                        # heartbeat now, default timeout 60 s
    w = _fresh_window(make_window, folder)
    assert w._lock_hold is None and w._lock_ro is not None
    assert not w.lock_banner.isHidden()
    assert HOLDER in w.lock_label.text() and "read-only" in w.lock_label.text()
    assert not _action(w, "Save").isEnabled() and w.save() is False
    assert L.holder(folder).instance == other.instance       # the browser's lock is untouched


def test_default_timeout_is_sixty_seconds_like_the_server():
    assert MW.MainWindow.LOCK_TIMEOUT == 60.0 and L.DEFAULT_TIMEOUT == 60.0
    assert MW.MainWindow.LOCK_PROGRAM == "desktop"


def test_lock_left_by_a_dead_desktop_blocks_until_the_timeout_then_opens(make_window, tmp_path, monkeypatch):
    monkeypatch.setattr(MW.MainWindow, "LOCK_TIMEOUT", 1.0)
    folder = _document(tmp_path)
    code = ("import os, sys; from freeshape import doc_lock as L; "
            "L.acquire(sys.argv[1], 'desktop'); os._exit(0)")      # exits without releasing, like a killed desktop
    r = subprocess.run([sys.executable, "-c", code, folder], cwd=ROOT, timeout=120,
                       env={**os.environ, "PYTHONPATH": ROOT})
    assert r.returncode == 0 and os.path.exists(L.lock_path(folder))
    w = _fresh_window(make_window, folder)
    assert w._lock_hold is None and w._lock_ro is not None   # still fresh: the window cannot know the holder died
    assert "desktop" in w.lock_label.text() and not w.lock_banner.isHidden()
    assert w.save() is False
    deadline = time.time() + 60
    while not L.holder(folder, 1.0).stale:                   # waits for the heartbeat to age; no timer in the window
        assert time.time() < deadline
        time.sleep(0.1)
    assert w._close_document()
    assert w.open_document(folder) is not None               # no prompt: the stale lock is simply replaced
    assert w._lock_ro is None and w._lock_hold is not None
    assert L.holder(folder, 1.0).program == "desktop" and w.lock_banner.isHidden()
    assert w.save() is True


def _quit_action(w):
    return next(a for a in w.actions() if a.text() == "Quit")


def test_closing_the_window_releases_the_lock_and_ends_the_heartbeat(make_window, tmp_path):
    folder = _document(tmp_path)
    w = _fresh_window(make_window, folder)
    hold = w._lock_hold
    assert hold.running() and os.path.exists(L.lock_path(folder))
    w.close()
    assert not os.path.exists(L.lock_path(folder)) and not hold.running() and w._lock_hold is None


def test_quit_menu_item_releases_the_lock_and_ends_the_heartbeat(make_window, tmp_path):
    folder = _document(tmp_path)
    w = _fresh_window(make_window, folder)
    hold = w._lock_hold
    assert hold.running() and os.path.exists(L.lock_path(folder))
    _quit_action(w).trigger()                                # Ctrl+Q's action: the same path as the close button
    assert not os.path.exists(L.lock_path(folder)) and not hold.running() and w._lock_hold is None


def test_opening_a_second_document_releases_the_first_and_takes_the_second(make_window, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _document(tmp_path / "a")
    second = _document(tmp_path / "b")
    w = _fresh_window(make_window, first)
    hold = w._lock_hold
    assert L.holder(first).program == "desktop"
    assert w.open_document(second) is not None
    assert not hold.running() and L.holder(first) is None
    assert L.holder(second).program == "desktop" and w._lock_hold is not None and w._lock_hold.running()


def _tracked(folder):
    names = subprocess.run(["git", "ls-files", "-z"], cwd=folder, capture_output=True, check=True).stdout
    out = {}
    for n in filter(None, names.decode().split("\0")):
        with open(os.path.join(folder, n), "rb") as fh:
            out[n] = fh.read()
    return out


def _edit_save_close(make_window, folder):
    w = _fresh_window(make_window, folder)
    _edit_depth(w, 17.0)
    assert w.save() is True
    for d in w.docs.values():
        d.dirty = False
    w.close()


def test_saved_files_are_byte_equal_with_and_without_the_lock(make_window, tmp_path, monkeypatch):
    (tmp_path / "base").mkdir()
    base = _document(tmp_path / "base")
    locked, unlocked = str(tmp_path / "locked"), str(tmp_path / "unlocked")
    shutil.copytree(base, locked)
    shutil.copytree(base, unlocked)
    _edit_save_close(make_window, locked)
    assert not os.path.exists(L.lock_path(locked))           # released again
    with monkeypatch.context() as m:                         # the lock location is unusable: opens as it always did

        def refuse(*a, **k):
            raise OSError("no lock here")
        m.setattr(L, "acquire", refuse)
        _edit_save_close(make_window, unlocked)
    a, b = _tracked(locked), _tracked(unlocked)
    assert a.keys() == b.keys() and a
    assert {k: v for k, v in a.items() if v != b[k]} == {}   # byte-equal tracked files
    for folder, files in ((locked, a), (unlocked, b)):
        status = subprocess.run(["git", "status", "--porcelain"], cwd=folder, capture_output=True, check=True)
        assert L.LOCK_NAME not in status.stdout.decode()
        assert not any(L.LOCK_NAME.encode() in v for v in files.values()) and L.LOCK_NAME not in files
