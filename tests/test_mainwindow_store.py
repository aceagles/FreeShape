"""I4.8: the main window reads and writes Document tabs through the Document (ADR 0009). A window opened on a
Document backed by a counting store opens, edits, autosaves, saves, crashes and recovers with every Document-tab
read and write going through the store, and `mainwindow.py` itself opens no `document.json` and no tab file of that
Document. A folder Document autosaves to the same file with the same bytes as before the store held autosaves, and
a loose file opens, autosaves and recovers as it did. Since I4.9 a tab's binary data is in blobs, so an autosave
carries the blobs its data names and recovery finds them; dropping it drops them. Offscreen; every modal is stubbed
and the autosave timer is driven by direct calls."""
import base64
import builtins
import hashlib
import json
import os
import re
import sys

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from conftest import TOP, add, rect_lines, region_refs, sketch_feature

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from freeshape import assembly as A  # noqa: E402
from freeshape import blobstore  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import docstore as DS  # noqa: E402
from freeshape import drawing as DW  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape import links as L  # noqa: E402
from freeshape.document import PartStudioDoc  # noqa: E402
from freeshape.ui import home as H  # noqa: E402
from freeshape.ui import mainwindow as MW  # noqa: E402

# What the app wrote for the fixed Document below before autosaves went through the store (recorded on the
# untouched build): the file name, and the sha256 of its bytes with the Document's folder and id replaced.
# The names are as recorded before I4.8. The sha256 values were re-recorded after I4.9, which changed the Part
# Studio format (version 2 -> 3), so the same tab writes other bytes; the tests still check those bytes against
# the old writer's shape (`_legacy_autosave_bytes`) applied to the tab's own `to_json`. Before I4.9 they were
# 74f2ef37...b475b73 (Document tab) and 078cba49...b238e0 (loose file).
BEFORE_DOCUMENT_TAB = {"name": "<ID>.T1.fshape", "sha": "03356164071d5d8a4ef023a1b5bd6db5a648e14dd44d9c0ac2aecf39edf1a628"}
BEFORE_LOOSE = {"name": "loose-fixed.fshape",
                "sha": "a5627f800d3a521caa8812bcd2e3c343e4585c62b74381c3a8125707254a44e3"}
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
    """The reverse direction: a file the old code wrote is found and offered by the new one. It is in the Part
    Studio format before I4.9 (version 2: an imported STEP file inline, no blobs), so it is recovered through the
    migration, which holds the STEP file in memory until the next save writes it to the Document's blobs."""
    w = make_window()
    fd, tab = _fixed_document(w, str(tmp_path / "Doc"))
    raw = _step_raw(tmp_path, "box.step", [_mkbox(5, 6, 7, (40, 0, 0))])
    fid = _import(tab.doc, raw, "box.step")
    want = _studio_hashes(tab.doc)
    tab.doc.name = tab.name = "Left behind"
    tab.doc.dirty = True
    legacy = json.loads(_legacy_autosave_bytes(w, tab.doc, tab))
    legacy["version"] = 2
    imp = next(f for f in legacy["features"] if f["id"] == fid)
    imp["params"] = {"data": base64.b64encode(raw).decode(), "filename": "box.step"}
    d = tmp_path / "autosave" / "pid"
    (d / f"{fd.id}.T1.fshape").write_bytes(json.dumps(legacy).encode())     # as the previous version left it
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
    assert _studio_hashes(got.doc) == want and not got.doc.errors
    key = blobstore.key_of(raw)
    assert json.loads(got.doc.to_json())["version"] == 3
    assert next(f for f in json.loads(got.doc.to_json())["features"] if f["id"] == fid)["params"]["blob"] == key
    w2.folder.save()
    assert w2.folder.store.has_blob(key, "T1")


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


# =========================================================================== autosaves carry their blobs (I4.9)

def _mkbox(x, y, z, at=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*at), float(x), float(y), float(z)).Shape()


def _step_raw(tmp_path, name, shapes):
    p = str(tmp_path / name)
    K.export_step(shapes, p)
    with open(p, "rb") as fh:
        return fh.read()


def _import(studio, raw, name):
    """An import feature, made as the Import command makes it (the STEP file inline, held as a blob)."""
    f = studio.make_feature("import")
    f["params"] = {"data": base64.b64encode(raw).decode(), "filename": name}
    studio.add_feature(f)
    assert not studio.errors, studio.errors
    return f["id"]


def _hash(shape):
    """The shape's geometry key. Not the engine's BREP hash: that one also covers OCCT's "checked" flags, which
    meshing a shape for display sets, so the same geometry shown in a window and freshly loaded would differ."""
    return blobstore.geometry_key(shape)


def _new_tab(w, doc):
    """A tab made in the window's open Document, as New Assembly / New Drawing make one; returns its Tab."""
    w._register_in_document(doc, like=w.folder.tabs[0].doc)
    w._add_tab(doc)
    return w.folder.tab(doc.tab_id)


def _studio_hashes(doc):
    return sorted(_hash(p.shape) for p in doc.state.parts.values())


def _asm_hash(a):
    rows = [[i["id"], _hash(a.parts[i["id"]].shape), [[round(x, 9) for x in r] for r in i["T"]]]
            for i in a.instances if i["id"] in a.parts]
    assert len(rows) == len(a.instances), a.errors
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _named_keys(data):
    return set(re.findall(r'"([0-9a-f]{64})"', data.decode() if isinstance(data, bytes) else data))


def _two_boxes(tmp_path):
    return _step_raw(tmp_path, "two.step", [_mkbox(10, 10, 10, (0, 40, 0)), _mkbox(5, 5, 5, (20, 40, 0))])


def _insert_step(asm, raw):
    key = asm.add_step_blob(raw)
    for i in range(2):
        asm.insert({"kind": "step", "blob": key, "index": i, "filename": "two.step"}, T=np.eye(4))
    return key


def test_document_tabs_autosave_their_blobs_and_recover_the_same_geometry(make_window, tmp_path, monkeypatch):
    """A Part Studio with an imported STEP file, an Assembly of a studio part and of an imported STEP file, and a
    Drawing of the Assembly: none ever saved. Their autosaves carry every blob they name; after the process dies
    the tabs are recovered from those alone, with the same geometry, and hold their blobs once the autosaves (and
    their blobs) are gone, so the next save writes them into the Document."""
    w = make_window()
    path = str(tmp_path / "Doc")
    _new_document(w, path)
    fd = w.folder
    st = fd.tabs[0]
    _box(st.doc)
    raw_cyl = _step_raw(tmp_path, "one.step", [_mkbox(3, 4, 5, (60, 0, 0))])
    _import(st.doc, raw_cyl, "one.step")
    pid = next(i for i, p in st.doc.state.parts.items() if not p.reference)
    asm = _new_tab(w, A.AssemblyDoc())
    asm.doc.insert(L.studio_source(st, pid), name="Plate")
    step_key = _insert_step(asm.doc, _two_boxes(tmp_path))
    asm.doc.refresh(solve=False, notify=False)
    assert len(asm.doc.parts) == 3 and not asm.doc.errors, asm.doc.errors
    w.dwg.request_exact = lambda *a, **k: None                  # no background hidden-line work
    dwg = _new_tab(w, DW.DrawingDoc())
    dwg.doc.source = L.assembly_source(asm)
    dwg.doc.refresh(notify=False)
    dwg.doc.add_view((0, 0, 1), (0, 1, 0), (150.0, 100.0))
    assert dwg.doc.shape is not None and not dwg.doc.error, dwg.doc.error
    want = {"studio": _studio_hashes(st.doc), "asm": _asm_hash(asm.doc), "dwg": _hash(dwg.doc.shape)}
    ids = {"studio": st.id, "asm": asm.id, "dwg": dwg.id}
    for t in (st, asm, dwg):
        t.doc.dirty = True

    w.autosave()
    folder = tmp_path / "autosave" / "pid"
    files = {n for n in os.listdir(folder) if n != "blobs"}
    assert files == {f"{fd.id}.{ids[k]}{ext}" for k, ext in (("studio", ".fshape"), ("asm", ".fasm"),
                                                              ("dwg", ".fdwg"))}
    named = set().union(*(_named_keys((folder / n).read_bytes()) for n in files))
    assert step_key in named and blobstore.key_of(raw_cyl) in named
    assert len(named) >= 5                      # STEP x2, a part copy for the studio instance, the Drawing's shape
    assert named <= set(os.listdir(folder / "blobs"))
    assert not fd.store.has_blob(step_key, asm.id)                  # never saved: only the autosave has it

    w.folder = None                                                # the in-memory tabs are gone
    w._autosave_timer.stop()
    dead = _kill(tmp_path)
    w2 = make_window("pid2")
    w2.dwg.request_exact = lambda *a, **k: None
    w2.offer_recovery()
    assert not [m for k, m in w2._asked if k in ("warning", "critical")], w2._asked
    back = {k: w2.folder.tab(t).doc for k, t in ids.items()}
    assert _studio_hashes(back["studio"]) == want["studio"]
    assert _asm_hash(back["asm"]) == want["asm"] and not back["asm"].errors
    assert back["dwg"].shape is not None and _hash(back["dwg"].shape) == want["dwg"]
    assert not [p for p in dead.rglob("*") if p.is_file()]          # autosaves and their blobs are gone

    w2.folder.save()                                                # the blobs were held: the save writes them
    store = w2.folder.store
    assert store.folder == path
    for k, t in ids.items():
        keys = _named_keys(store.read_tab(t))
        assert keys and all(store.has_blob(x, t) for x in keys), k
    assert store.has_blob(step_key, ids["asm"])


def test_a_loose_assembly_autosaves_its_blobs_and_recovers(make_window, tmp_path):
    path = str(tmp_path / "loose.fasm")
    a = A.AssemblyDoc()
    key = _insert_step(a, _two_boxes(tmp_path))
    a.save(path)
    want = _asm_hash(a)
    w = make_window()
    w.open_file(path)
    doc = w.doc
    assert doc.path == path and w.folder is None and len(doc.parts) == 2
    doc.name = "Loose asm"
    doc.dirty = True
    w.autosave()
    folder = tmp_path / "autosave" / "pid"
    assert os.listdir(folder / "blobs") == [key]
    for name in os.listdir(tmp_path / "blobs"):                     # the file's own blobs are lost too
        os.remove(tmp_path / "blobs" / name)
    w._autosave_timer.stop()
    dead = _kill(tmp_path)
    w2 = make_window("pid2")
    w2.offer_recovery()
    got = w2.doc
    assert got.name == "Loose asm (recovered)" and got.path == path and got.dirty
    assert _asm_hash(got) == want and not got.errors
    assert not dead.exists()                                        # the autosave, its blobs and its folder
    got.save(str(tmp_path / "again.fasm"))                          # held: written beside the new file
    assert blobstore.store_for(str(tmp_path / "again.fasm")).has(key)


def test_an_autosave_writes_each_blob_once_and_dropping_it_drops_its_blobs(make_window, tmp_path, monkeypatch):
    """Two Document tabs and a loose Part Studio import the same STEP file: one blob in the autosave folder. A
    second autosave tick writes no blob. Dropping an autosave keeps the blobs another one names; dropping the last
    removes them and the blobs folder."""
    raw = _step_raw(tmp_path, "one.step", [_mkbox(3, 4, 5)])
    key = blobstore.key_of(raw)
    loose_path = str(tmp_path / "loose.fshape")
    src = PartStudioDoc()
    _import(src, raw, "one.step")
    src.save(loose_path)
    w = make_window()
    _new_document(w, str(tmp_path / "Doc"))
    fd = w.folder
    one = fd.tabs[0]
    two = _new_tab(w, PartStudioDoc())
    for doc in (one.doc, two.doc):
        _import(doc, raw, "one.step")
    w.open_file(loose_path)
    loose = w.doc
    assert loose.path == loose_path and w.folder is fd and len(w.docs) == 3
    for doc in (loose, one.doc, two.doc):
        doc.dirty = True
    w.autosave()
    folder = tmp_path / "autosave" / "pid"
    assert os.listdir(folder / "blobs") == [key]
    assert len([n for n in os.listdir(folder) if n != "blobs"]) == 3

    puts = []
    real = blobstore.BlobStore.put
    monkeypatch.setattr(blobstore.BlobStore, "put", lambda self, data: puts.append(1) or real(self, data))
    w.autosave()
    assert puts == []                                               # already there: not written again

    w._drop_autosave(one.doc)
    assert os.listdir(folder / "blobs") == [key]
    w._drop_autosave(loose)
    assert os.listdir(folder / "blobs") == [key]
    w._drop_autosave(two.doc)
    assert os.listdir(folder) == []                                 # no autosave, no blob, no blobs folder


def test_a_memory_store_autosave_carries_its_blobs(tmp_path):
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store, "Mem")
    tab = fd.add_tab("studio", "Studio")
    raw = _step_raw(tmp_path, "one.step", [_mkbox(3, 4, 5)])
    _import(tab.doc, raw, "one.step")
    want = _studio_hashes(tab.doc)
    key = blobstore.key_of(raw)
    where = str(tmp_path / "x")
    tab.doc.write_blobs(store=fd.autosave_blobs(where))
    akey = fd.autosave_tab(tab.id, tab.kind, tab.doc.to_data(), where=where)
    assert not (tmp_path / "x").exists() and not store.has_blob(key, tab.id)
    assert store.side_blobs(where).has(key)
    back = fd.recover_tab(tab.kind, akey, tab.id, where=where)
    assert _studio_hashes(back) == want
    fd.discard_autosave(akey, where=where)
    assert not store.side_blobs(where).has(key)                     # dropped with the autosave
    assert back._blob_get(key) == raw                               # held by the recovered tab
