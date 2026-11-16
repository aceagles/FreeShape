"""I13.2: what the gate of the daily desktop's lock needs pinned beyond tests/test_desktop_lock*.py. The heartbeat
runs across several refresh intervals; a window that opened a Document read-only writes no autosave; and WHERE the
desktop writes the lock for a Document in the registry, for a folder that is not in the registry (the desktop
imports a clone and works on that), and for a legacy single file (no lock at all), against where a second program
given the same folder would look. Offscreen Qt, real Document folders; every wait polls the lock's state."""
import os
import shutil
import time

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from test_desktop_lock import _document, make_window  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

from freeshape import doc_folder as DF  # noqa: E402
from freeshape import doc_lock as L  # noqa: E402
from freeshape import doc_registry as R  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402


def _locks_under(root):
    """Every lock file (not its temp files) below `root`, relative to it."""
    out = []
    for base, _dirs, files in os.walk(root):
        out += [os.path.relpath(os.path.join(base, f), root) for f in files if f == L.LOCK_NAME]
    return sorted(out)


def test_the_heartbeat_keeps_a_held_lock_fresh_across_several_refresh_intervals(make_window, tmp_path, monkeypatch):
    monkeypatch.setattr(MW.MainWindow, "LOCK_INTERVAL", 0.05)
    folder = _document(tmp_path)
    w = make_window()
    assert w.open_document(folder) is not None
    beats = {L.holder(folder).heartbeat}
    deadline = time.time() + 60
    while len(beats) < 4:                                    # polls the lock file: four distinct heartbeats
        assert time.time() < deadline, f"the heartbeat did not advance: {sorted(beats)}"
        beats.add(L.holder(folder).heartbeat)
        time.sleep(0.01)
    got = L.holder(folder, timeout=1.0)
    assert got.program == "desktop" and not got.stale        # fresh by a 1 s timeout, so refreshed within it
    assert w._lock_hold.running() and w._lock_hold.lock.still_mine()


def test_a_window_that_opened_read_only_neither_saves_nor_autosaves(make_window, tmp_path):
    folder = _document(tmp_path)
    other = L.acquire(folder, "browser")
    w = make_window()
    assert w.open_document(folder) is not None
    assert w._lock_ro is not None and w._lock_hold is None
    studio = w.folder.tabs[0].doc
    studio.dirty = True                                      # unsaved changes: what the server would autosave
    assert w._has_content(studio)
    before = sorted(os.listdir(folder))
    w.autosave()                                             # what the window's 60 s autosave timer calls
    assert os.listdir(tmp_path / "autosave") == []           # nothing in the autosave folder
    assert w.save() is False
    assert sorted(os.listdir(folder)) == before
    assert L.holder(folder).instance == other.instance
    studio.dirty = False


def test_lock_of_a_document_in_the_registry_is_at_its_own_folder(make_window, tmp_path):
    """The owner's case: the Document was made (or imported) by the app and the registry records its own folder."""
    folder = _document(tmp_path)
    assert R.default_registry().trusted_path(DF.FolderDocument.open(folder).id) == os.path.realpath(folder)
    w = make_window()
    assert w.open_document(folder) is not None
    assert os.path.realpath(w.folder.path) == os.path.realpath(folder)
    assert _locks_under(tmp_path) == ["Doc/" + L.LOCK_NAME]
    assert L.holder(folder).program == "desktop"             # a second program given the registry's folder finds it
    with pytest.raises(L.LockHeld) as e:
        L.acquire(R.default_registry().trusted_path(w.folder.id), "browser")
    assert e.value.holder.program == "desktop"


def test_lock_through_a_symlink_to_a_registered_folder_is_at_the_real_folder(make_window, tmp_path):
    folder = _document(tmp_path)
    link = str(tmp_path / "Link")
    os.symlink(folder, link)
    w = make_window()
    assert w.open_document(link) is not None
    assert w.folder.path == os.path.realpath(folder)
    assert _locks_under(tmp_path) == ["Doc/" + L.LOCK_NAME]  # none at the link's name


def test_lock_of_a_folder_not_in_the_registry_is_on_the_clone_the_desktop_imports(make_window, tmp_path):
    """GAP, pinned as it is: a folder the app did not create is not worked on in place. open_document clones it
    into <data dir>/documents/<id> and the desktop edits and locks the CLONE. A second program that opens the folder
    that was passed finds no lock there and can take its own: the two hold "the lock" in two places."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    foreign = R.DocRegistry(str(elsewhere / "documents.json"))           # a registry this desktop does not read
    fd = DF.FolderDocument.create(str(elsewhere / "Doc"), "Doc", registry=foreign)
    tab = fd.add_tab("studio", "Plate")
    sid = sketch_feature(tab.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=10.0, op="new")
    fd.save()
    passed = fd.path
    assert R.default_registry().trusted_path(fd.id) is None              # not in this desktop's registry
    w = make_window()
    assert w.open_document(passed) is not None
    clone = w.folder.path
    assert os.path.realpath(clone) != os.path.realpath(passed)
    assert os.path.realpath(clone) == os.path.realpath(os.path.join(R.managed_root(), os.path.basename(clone)))
    assert os.path.realpath(R.managed_root()) == os.path.realpath(str(tmp_path / "appdata" / "documents"))
    assert _locks_under(tmp_path) == ["appdata/documents/" + os.path.basename(clone) + "/" + L.LOCK_NAME]
    assert L.holder(clone).program == "desktop" and L.holder(passed) is None
    second = L.acquire(passed, "browser")                                # the folder that was passed: free
    assert second.still_mine() and L.holder(clone).program == "desktop"  # both hold a lock, on two folders
    second.release()


def test_a_legacy_single_file_document_takes_no_lock(make_window, tmp_path):
    """A .fshape file opens through open_file: no Document folder, so no lock is taken anywhere."""
    doc = PartStudioDoc()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=10.0, op="new")
    path = str(tmp_path / "loose.fshape")
    doc.save(path)
    w = make_window()
    w.open_file(path)
    assert any(d.path == path for d in w.docs.values())      # it opened
    assert w.folder is None and w._lock_hold is None and w._lock_ro is None and w._lock_path is None
    assert _locks_under(tmp_path) == []
