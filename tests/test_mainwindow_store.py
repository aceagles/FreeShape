"""I4.8: the main window reads and writes Document tabs through the Document (ADR 0009). A window opened on a
Document backed by a counting store opens, edits, autosaves, saves, crashes and recovers with every Document-tab
read and write going through the store, and `mainwindow.py` itself opens no `document.json` and no tab file of that
Document. A folder Document autosaves to the same file with the same bytes as before the store held autosaves, and
a loose file opens, autosaves and recovers as it did. Offscreen; every modal is stubbed and the autosave timer is
driven by direct calls."""
import builtins
import hashlib
import json
import os
import sys

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from freeshape import doc_folder as DF  # noqa: E402
from freeshape import docstore as DS  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

# What the app wrote for the fixed Document below before autosaves went through the store (recorded on the
# untouched build): the file name, and the sha256 of its bytes with the Document's folder and id replaced.
BEFORE_DOCUMENT_TAB = {"name": "<ID>.T1.fshape", "sha": "74f2ef370e69658da8d0ca84a0d4965ed58b13d12468fc893ab3130d4b475b73"}
BEFORE_LOOSE = {"name": "loose-fixed.fshape",
                "sha": "078cba49d94654aa70e59e17ee708e618d09213888103c8b25694187e7b238e0"}
FIXED_TIME = 1234.5


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    asked = []
    monkeypatch.setattr(MW.QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(("question", a[2])) or QMessageBox.Yes))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(MW.QMessageBox, name,
                            staticmethod(lambda *a, _n=name, **k: asked.append((_n, a[2])) or QMessageBox.Ok))
    made = []

    def make(pid="pid"):
        w = MW.MainWindow()
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        w._asked = asked
        d = str(tmp_path / "autosave" / pid)
        os.makedirs(d, exist_ok=True)
        monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: d))
        app.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w._autosave_timer.stop()
        w.folder = None
        w.deleteLater()


def _box(studio):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=10.0, op="new")
    assert not studio.errors, studio.errors


def _new_document(w, path):
    w._pick_new_document_path = lambda: path
    w.home.newDocument.emit()
    assert w.folder is not None and w.folder.path == path


def _kill(tmp_path, pid="pid"):
    """The process dies: its autosave folder is now owned by a dead pid."""
    dead = next(p for p in range(4_000_000, 4_100_000) if not MW.MainWindow._process_alive(p))
    os.rename(tmp_path / "autosave" / pid, tmp_path / "autosave" / str(dead))
    return tmp_path / "autosave" / str(dead)


def _autosave_at(w, monkeypatch):
    with monkeypatch.context() as m:
        import time
        m.setattr(time, "time", lambda: FIXED_TIME)
        w.autosave()


def _normal(data, folder, doc_id):
    return data.replace(folder.encode(), b"<DOC>").replace(doc_id.encode(), b"<ID>")


class _Counting(DS.DocumentStore):
    """Wraps a FolderStore and counts each call."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = {}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _count(self, name, *args, **kw):
        self.calls[name] = self.calls.get(name, 0) + 1
        return getattr(self.inner, name)(*args, **kw)

    has_folder = property(lambda self: self.inner.has_folder)
    folder = property(lambda self: self.inner.folder)

    def describe(self):
        return "a counting " + self.inner.describe()


for _name in ("read_manifest", "write_manifest", "has_manifest", "place_tab", "list_tabs", "has_tab", "read_tab",
              "write_tab", "delete_tab", "strays", "tab_path", "checked_tab_path", "file_taken", "read_blob",
              "write_blob", "has_blob", "blobs", "write_side", "read_side", "delete_side", "side_path"):
    setattr(_Counting, _name, (lambda n: lambda self, *a, **kw: self._count(n, *a, **kw))(_name))
_Counting.__abstractmethods__ = frozenset()


@pytest.fixture
def opens_from_mainwindow(monkeypatch):
    """Paths under `watch` (a list the test fills with Document folders) that mainwindow.py opens directly."""
    class Seen(list):
        watch = []

    real, seen = builtins.open, Seen()
    watch = seen.watch

    def spy(file, *a, **kw):
        if os.path.basename(sys._getframe(1).f_code.co_filename) == "mainwindow.py" \
                and isinstance(file, (str, os.PathLike)):
            full = os.path.abspath(os.fspath(file))
            if any(full.startswith(os.path.abspath(f) + os.sep) for f in watch):
                seen.append(full)
        return real(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    return seen


def _fixed_document(w, path):
    """A Document with one Part Studio tab with a box, ids fixed so the autosave bytes are comparable."""
    _new_document(w, path)
    fd = w.folder
    st = fd.tabs[0].doc
    st.uid = "uid-fixed"
    _box(st)
    return fd, fd.tabs[0]


# =========================================================================== same file, same bytes

def _legacy_autosave_bytes(w, doc, tab):
    """What the window wrote for a Document tab before autosaves went through the store."""
    d = json.loads(doc.to_json())
    d["autosave"] = {"path": doc.path, "name": doc.name, "time": FIXED_TIME}
    d["autosave"]["document"] = {"id": w.folder.id, "name": w.folder.name, "path": w.folder.path,
                                 "tab": tab.id, "kind": tab.kind, "file": tab.file, "tab_name": tab.name}
    import io
    out = io.StringIO()
    json.dump(d, out)
    return out.getvalue().encode()


def test_folder_document_autosaves_the_same_file_with_the_same_bytes(make_window, tmp_path, monkeypatch):
    w = make_window()
    fd, tab = _fixed_document(w, str(tmp_path / "Doc"))
    tab.doc.dirty = True
    _autosave_at(w, monkeypatch)
    files = sorted(os.listdir(tmp_path / "autosave" / "pid"))
    assert files == [f"{fd.id}.T1.fshape"]                                    # one file, no .tmp left behind
    got = (tmp_path / "autosave" / "pid" / files[0]).read_bytes()
    assert got == _legacy_autosave_bytes(w, tab.doc, tab)                     # what the old code wrote
    recorded = {"name": files[0].replace(fd.id, "<ID>"),
                "sha": hashlib.sha256(_normal(got, fd.path, fd.id)).hexdigest()}
    assert recorded == BEFORE_DOCUMENT_TAB, recorded


def test_loose_file_autosaves_the_same_file_with_the_same_bytes(make_window, tmp_path, monkeypatch):
    w = make_window()
    doc = w.doc
    doc.uid = "uid-loose"
    _box(doc)
    doc.dirty = True
    doc._autosave_id = "loose-fixed"
    _autosave_at(w, monkeypatch)
    files = sorted(os.listdir(tmp_path / "autosave" / "pid"))
    assert files == ["loose-fixed.fshape"]
    got = (tmp_path / "autosave" / "pid" / files[0]).read_bytes()
    d = json.loads(doc.to_json())
    d["autosave"] = {"path": doc.path, "name": doc.name, "time": FIXED_TIME}
    import io
    out = io.StringIO()
    json.dump(d, out)
    assert got == out.getvalue().encode()
    recorded = {"name": files[0], "sha": hashlib.sha256(got).hexdigest()}
    assert recorded == BEFORE_LOOSE, recorded


def test_an_autosave_left_by_the_previous_version_is_recovered(make_window, tmp_path, monkeypatch):
    """The reverse direction: a file the old code wrote is found and offered by the new one."""
    w = make_window()
    fd, tab = _fixed_document(w, str(tmp_path / "Doc"))
    tab.doc.name = tab.name = "Left behind"
    tab.doc.dirty = True
    legacy = _legacy_autosave_bytes(w, tab.doc, tab)
    d = tmp_path / "autosave" / "pid"
    (d / f"{fd.id}.T1.fshape").write_bytes(legacy)                           # as the previous version left it
    w.folder = None
    dead = _kill(tmp_path)
    w2 = make_window("pid2")
    w2.offer_recovery()
    asked = [m for kind, m in w2._asked if kind == "question"]
    assert len(asked) == 1 and "Left behind" in asked[0] and "Doc" in asked[0]
    assert w2.folder is not None and w2.folder.id == fd.id
    got = w2.folder.tab("T1")
    assert got.doc.dirty and got.doc.features and got.doc.name == "Left behind"
    assert not (dead / f"{fd.id}.T1.fshape").exists()


# =========================================================================== through the store

def test_open_edit_autosave_close_reopen_recover_on_a_counting_store(make_window, tmp_path, monkeypatch,
                                                                    opens_from_mainwindow):
    w = make_window()
    path = str(tmp_path / "Counted")
    _new_document(w, path)
    w.save()
    fd0 = w.folder
    doc_id, tab_id = fd0.id, fd0.tabs[0].id
    w.folder = None                                                          # close; the files stay
    w._autosave_timer.stop()

    # open: a window on a Document backed by the counting store
    opens_from_mainwindow.watch.append(path)
    store = _Counting(DS.FolderStore(path))
    w1 = make_window("pid1")
    w1._adopt_document(DF.FolderDocument.open(path, store=store))
    fd = w1.folder
    assert fd.store is store and not fd.errors
    tab = fd.tabs[0]
    assert w1._folder_tab(tab.doc) is tab

    # edit, autosave: the tab's data goes to the store's side area, and nowhere else
    _box(tab.doc)
    tab.doc.dirty = True
    w1.autosave()
    key = f"{doc_id}.{tab_id}.fshape"
    assert store.calls["write_side"] == 1
    assert os.listdir(tmp_path / "autosave" / "pid1") == [key]

    # save: the autosave is dropped through the store
    assert w1.save() is True
    assert store.calls["delete_side"] == 1 and os.listdir(tmp_path / "autosave" / "pid1") == []
    assert store.calls["write_tab"] >= 1

    # edit again, autosave, and the process dies
    tab.doc.name = tab.name = "Edited"
    tab.doc.dirty = True
    w1.autosave()
    assert os.listdir(tmp_path / "autosave" / "pid1") == [key]
    w1._autosave_timer.stop()
    w1.folder = None
    dead = _kill(tmp_path, "pid1")

    # reopen on a fresh counting store and recover
    store2 = _Counting(DS.FolderStore(path))
    w2 = make_window("pid2")
    w2._adopt_document(DF.FolderDocument.open(path, store=store2))
    w2.offer_recovery()
    assert [m for k, m in w2._asked if k == "question"] and not [m for k, m in w2._asked if k == "critical"]
    got = w2.folder.tab(tab_id)
    assert got.doc.dirty and got.doc.features and got.doc.name == "Edited"
    assert w2.docs.get(id(got.doc)) is got.doc
    assert store2.calls.get("side_path", 0) >= 1 and store2.calls["delete_side"] == 1
    assert not (dead / key).exists()
    assert store.calls["read_manifest"] >= 1

    assert opens_from_mainwindow == [], opens_from_mainwindow                # nothing opened directly


def test_recent_entry_manifest_is_read_through_a_store(make_window, tmp_path, opens_from_mainwindow):
    w = make_window()
    path = str(tmp_path / "Recent")
    _new_document(w, path)
    opens_from_mainwindow.watch.append(path)
    info = w._document_info(path)
    assert info["id"] == w.folder.id and info["name"] == "Recent"
    assert w._document_info(str(tmp_path)) is None                          # a folder with no manifest
    assert w._document_info(str(tmp_path / "nothing")) is None
    assert opens_from_mainwindow == []


def test_memory_store_holds_autosaves_without_files(tmp_path):
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store, "Mem")
    tab = fd.add_tab("studio", "Studio")
    _box(tab.doc)
    key = fd.autosave_key(tab.id, tab.kind)
    assert key == f"{fd.id}.{tab.id}.fshape"
    assert fd.autosave_tab(tab.id, tab.kind, json.dumps({**json.loads(tab.doc.to_json()), "autosave": {}})
                           .encode(), where=str(tmp_path / "x")) == key
    assert not (tmp_path / "x").exists()                                    # nothing on disk
    back = fd.recover_tab(tab.kind, key, tab.id, where=str(tmp_path / "x"))
    assert isinstance(back, PartStudioDoc) and len(back.features) == len(tab.doc.features)
    fd.drop_autosave(tab.id, tab.kind, where=str(tmp_path / "x"))
    with pytest.raises(K.KernelError):
        store.read_side(key)                                                 # dropped
    fd.drop_autosave(tab.id, tab.kind, where=str(tmp_path / "x"))           # nothing left: no error


def test_a_loose_file_opens_autosaves_and_recovers(make_window, tmp_path, monkeypatch):
    path = str(tmp_path / "loose.fshape")
    src = PartStudioDoc()
    _box(src)
    src.save(path)
    w = make_window()
    w.open_file(path)
    doc = w.doc
    assert doc.path == path and w.folder is None and doc.features
    doc.name = "Loose edit"
    doc.dirty = True
    w.autosave()
    files = os.listdir(tmp_path / "autosave" / "pid")
    assert len(files) == 1 and files[0].endswith(".fshape")
    meta = json.loads((tmp_path / "autosave" / "pid" / files[0]).read_text())["autosave"]
    assert meta["path"] == path and "document" not in meta
    w._autosave_timer.stop()
    _kill(tmp_path)
    w2 = make_window("pid2")
    w2.offer_recovery()
    assert w2.doc.name == "Loose edit (recovered)" and w2.doc.dirty and w2.doc.features
    assert w2.doc.path == path
