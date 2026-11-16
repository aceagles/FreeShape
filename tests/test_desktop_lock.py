"""I2.2: the desktop honours the Document lock. Opening a Document takes the lock and a plain thread keeps its
heartbeat; another holder makes the Document open read-only (banner, disabled actions, refused writes); a lock taken
from us turns the window read-only on the next save; Take over asks first. Offscreen Qt, real Document folders; the
first and the read-only tests drive `MainWindow(engine=True)` as the real app starts it."""
import os
import shutil
import signal
import threading
import time

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_lock as L  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape.engine import process as P  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402
from test_engine_picking import _spawned  # noqa: E402

SETTLE = 180


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    for name in ("warning", "critical", "information"):     # never block on a message box
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)   # a test that says yes patches it
    made = []
    before = set(_spawned())

    def make(engine=False):
        w = MW.MainWindow(engine=engine)
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        app.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        if not isValid(w):                          # a test that deleted its window has nothing left to close
            continue
        for d in w.docs.values():
            d.dirty = False
        w.close()                                   # releases the lock and joins the heartbeat thread
        if w.studio_engine is not None:
            w.studio_engine.shutdown()
        assert w._lock_hold is None
        w.deleteLater()
    P.SPARES.clear()
    for p in sorted(set(_spawned()) - before):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    app.processEvents()


def _box(studio, size=20.0, depth=10.0):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, size, size), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _document(tmp_path):
    """A Document folder with one Part Studio (a box), a version v1 and a second Workspace "side"; main is current."""
    reg = R.default_registry()
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), "Doc", registry=reg)
    tab = fd.add_tab("studio", "Plate")
    _box(tab.doc)
    fd.save()
    fd.create_version("v1", "first box")
    fd.create_workspace("side", "v1")
    fd.switch_workspace("main")
    return fd.path


def _disk(folder):
    """Every file of the Document folder (without .git) and the refs and HEAD of its repository."""
    out = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, folder)] = fh.read()
    git = os.path.join(folder, ".git")
    for name in ("HEAD", "packed-refs"):
        if os.path.exists(os.path.join(git, name)):
            with open(os.path.join(git, name), "rb") as fh:
                out[".git/" + name] = fh.read()
    for root, _dirs, files in os.walk(os.path.join(git, "refs")):
        for f in files:
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, folder)] = fh.read()
    return out


def _action(w, text):
    return next(a for a in w._lock_actions if a.text() == text)


def _edit_depth(w, depth):
    studio = w.folder.tabs[0].doc
    feature = next(f for f in studio.features if f["type"] == "extrude")
    feature["params"]["depth"] = depth
    studio.replace_feature(feature)
    studio.dirty = True
    return studio


def test_unlocked_document_creates_the_lock_and_closing_it_removes_it(make_window, tmp_path):
    folder = _document(tmp_path)
    assert L.holder(folder) is None
    w = make_window(engine=True)
    assert w.studio_engine is not None
    assert w.open_document(folder) is not None
    assert w.studio_engine.settle(SETTLE)
    held = L.holder(folder)
    assert held is not None and held.program == "desktop" and not held.stale
    assert w._lock_hold is not None and w._lock_hold.running() and w._lock_ro is None
    assert w.lock_banner.isHidden()
    assert _action(w, "Save").isEnabled() and w.tool_stack.isEnabled()
    assert w.save() is True                                  # nothing changes for a Document nobody else holds
    hold = w._lock_hold
    assert w._close_document()
    assert L.holder(folder) is None                          # closing the Document removes the lock
    assert not hold.running() and w._lock_hold is None
    assert w.open_document(folder) is not None               # and it can be opened again
    assert L.holder(folder).program == "desktop"


def test_document_held_by_another_holder_opens_read_only(make_window, tmp_path):
    folder = _document(tmp_path)
    other = L.acquire(folder, "browser")
    before = _disk(folder)
    w = make_window(engine=True)
    assert w.open_document(folder) is not None
    assert w.studio_engine.settle(SETTLE)
    assert w._lock_hold is None and w._lock_ro is not None
    assert not w.lock_banner.isHidden()
    assert "browser" in w.lock_label.text() and "read-only" in w.lock_label.text()
    assert w.lock_take_over.text() == "Take over"
    assert not _action(w, "Save").isEnabled() and not _action(w, "Save as…").isEnabled()
    assert not _action(w, "Add files to Document…").isEnabled() and not w.tool_stack.isEnabled()
    assert w.save() is False and w.save_as() is False
    w.start_sketch()
    w.start_feature("extrude")
    assert w.mode == "idle"                                  # no tool opens
    assert w.autosave() is None and os.listdir(tmp_path / "autosave") == []
    assert L.holder(folder).instance == other.instance      # the other holder's lock is untouched
    assert _disk(folder) == {**before, L.LOCK_NAME: _disk(folder)[L.LOCK_NAME]}


def test_save_is_refused_after_a_take_over_by_another_holder(make_window, tmp_path):
    folder = _document(tmp_path)
    w = make_window()
    w.open_document(folder)
    assert w._lock_hold is not None
    studio = _edit_depth(w, 30.0)
    taken = L.take_over(folder, "browser")
    before = _disk(folder)
    assert w.save() is False                                 # refused without waiting for the heartbeat
    assert studio.dirty
    assert _disk(folder) == before                           # no file changed
    assert w._lock_ro is not None and w._lock_ro["lost"] and w._lock_hold is None
    assert not w.lock_banner.isHidden() and "browser" in w.lock_label.text()
    assert not _action(w, "Save").isEnabled() and not w.tool_stack.isEnabled()
    assert L.holder(folder).instance == taken.instance
    w.autosave()
    assert os.listdir(tmp_path / "autosave") == []           # a read-only window writes no autosave either


def test_heartbeat_thread_notices_the_lost_lock_and_the_window_follows(make_window, tmp_path, monkeypatch):
    monkeypatch.setattr(MW.MainWindow, "LOCK_INTERVAL", 0.05)
    folder = _document(tmp_path)
    w = make_window()
    w.open_document(folder)
    hold = w._lock_hold
    L.take_over(folder, "browser")
    assert hold.lost.wait(10)                                # the thread saw it, off the Qt thread
    assert not hold.running()
    assert w._lock_ro is None                                # the window has not been touched by the thread
    w._poll_lock()                                           # what the Qt timer does
    assert w._lock_ro is not None and w._lock_ro["lost"]
    assert not w.lock_banner.isHidden()


def test_read_only_document_refuses_versions_and_workspaces(make_window, tmp_path):
    folder = _document(tmp_path)
    L.acquire(folder, "browser")
    w = make_window()
    w.open_document(folder)
    assert w._lock_ro is not None
    before = _disk(folder)
    versions = [v["name"] for v in w.folder.versions()]
    assert w.create_version_dialog() is False
    assert isinstance(w.versions_ui.create_version("v2", "nope"), str)
    assert w.switch_workspace("side") is False
    assert w.create_workspace("third", "v1") is False
    assert w.new_workspace_dialog() is False
    assert w.merge_workspace("side") is False and w.merge_dialog() is False
    assert w.updates_dialog() is None and w.versions_ui.update_links() == 0
    assert w.add_to_document(["/nonexistent.fshape"]) == []
    assert not w.versions_ui.create_btn.isEnabled() and not w.workspace_new.isEnabled()
    assert not w.merge_action.isEnabled()
    assert _disk(folder) == before                           # files, HEAD and refs are as they were
    assert [v["name"] for v in w.folder.versions()] == versions
    assert w.folder.current_workspace() == "main" and w.folder.workspaces() == ["main", "side"]


def test_heartbeat_stays_fresh_while_the_event_loop_is_blocked(make_window, tmp_path, monkeypatch):
    monkeypatch.setattr(MW.MainWindow, "LOCK_TIMEOUT", 1.0)
    monkeypatch.setattr(MW.MainWindow, "LOCK_INTERVAL", 0.1)
    folder = _document(tmp_path)
    w = make_window()
    w.open_document(folder)
    started = L.holder(folder, timeout=1.0).heartbeat
    time.sleep(2.5)                                          # the Qt thread is stuck for 2.5 timeouts
    held = L.holder(folder, timeout=1.0)
    assert not held.stale and held.heartbeat > started + 1.0
    with pytest.raises(L.LockHeld):                          # a server arriving now still finds the lock live
        L.acquire(folder, "browser", timeout=1.0)


def test_closing_the_window_or_deleting_it_stops_the_thread(make_window, tmp_path):
    folder = _document(tmp_path)
    w = make_window()
    w.open_document(folder)
    hold = w._lock_hold
    assert hold.running() and threading.active_count() >= 2
    w.close()
    assert not hold.running() and L.holder(folder) is None
    w2 = make_window()
    w2.open_document(folder)
    hold2 = w2._lock_hold
    w2.deleteLater()                                         # never closed: destroyed
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not hold2.running() and L.holder(folder) is None


def test_take_over_asks_first_then_reads_the_document_again(make_window, tmp_path, monkeypatch):
    folder = _document(tmp_path)
    L.acquire(folder, "browser")
    w = make_window()
    w.open_document(folder)
    assert w._lock_ro is not None
    other = DF.FolderDocument.open_document(folder, registry=R.default_registry())   # the browser saves a change
    feature = next(f for f in other.tabs[0].doc.features if f["type"] == "extrude")
    feature["params"]["depth"] = 33.0
    other.tabs[0].doc.replace_feature(feature)
    other.tabs[0].doc.dirty = True
    other.save()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1])
    w.lock_take_over.click()
    assert asked and "browser client" in asked[0] and "Take over" in asked[0]
    assert w._lock_ro is not None and L.holder(folder).program == "browser"       # No: nothing changed
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w.lock_take_over.click()
    assert w._lock_ro is None and w._lock_hold is not None and w._lock_hold.running()
    assert L.holder(folder).program == "desktop"
    assert w.lock_banner.isHidden() and _action(w, "Save").isEnabled() and w.tool_stack.isEnabled()
    shown = next(f for f in w.folder.tabs[0].doc.features if f["type"] == "extrude")
    assert shown["params"]["depth"] == 33.0                  # what the other program saved, not the old copy
    assert w.save() is True


def test_loose_file_has_no_lock_and_stays_editable(make_window, tmp_path):
    w = make_window()
    path = str(tmp_path / "loose.fshape")
    _box(w.doc)
    w.doc.save(path)
    w.open_any(path)
    assert w.folder is None and w._lock_hold is None and w._lock_ro is None
    assert w.tool_stack.isEnabled() and _action(w, "Save").isEnabled() and w.lock_banner.isHidden()
    assert not any(n.startswith(L.LOCK_NAME) for n in os.listdir(tmp_path))


def test_unreadable_lock_location_opens_as_before(make_window, tmp_path, monkeypatch):
    folder = _document(tmp_path)

    def refuse(*a, **k):
        raise PermissionError("read-only folder")
    monkeypatch.setattr(L, "acquire", refuse)
    w = make_window()
    assert w.open_document(folder) is not None
    assert w._lock_hold is None and w._lock_ro is None and w.lock_banner.isHidden()
    assert w.save() is True
