"""I4.7: a Document on a MemoryStore never touches disk (ADR 0009). Part Studio, a Variable Studio it references, an
Assembly instancing the studio and a Drawing of the Assembly are created, saved, dropped and reopened from the same
store with every file write refused; links resolve through the Document's tabs, the geometry hashes match, and the
tab data equals byte for byte what a FolderStore writes for the same script. Versions, Workspaces and merge refuse
a store with no folder."""
import builtins
import os
import tempfile

import pytest
from conftest import add, rect_lines, region_refs, sketch_feature, TOP

from freeshape import doc_folder as DF
from freeshape import docstore as DS
from freeshape import kernel as K
from freeshape import links as L
from freeshape.engine.encode import Encoder

NAMES = {"studio": "Studio", "variables": "Vars", "assembly": "Asm", "drawing": "Dwg"}


def _hash(part):
    return Encoder(meshes=False).brep(part.shape)[1]


def _build(fd):
    """The script run on both stores: ids, uids and names fixed so the bytes are comparable."""
    fd.id, fd.name = "doc-mem", "doc-mem"
    var = fd.add_tab("variables", NAMES["variables"])
    var.doc.uid = "uid-vars"
    var.doc.add_row("w", "20 mm")
    st = fd.add_tab("studio", NAMES["studio"])
    st.doc.uid = "uid-studio"
    add(st.doc, "variable_studio_ref", source={"kind": "variables", "tab": var.id, "name": var.name})
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth="#w", op="new")
    assert not st.doc.errors, st.doc.errors
    pid = next(iter(st.doc.state.parts))
    asm = fd.add_tab("assembly", NAMES["assembly"])
    asm.doc.uid = "uid-asm"
    asm.doc.insert(L.studio_source(st, pid), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    assert not asm.doc.errors, asm.doc.errors
    dwg = fd.add_tab("drawing", NAMES["drawing"])
    dwg.doc.uid = "uid-dwg"
    dwg.doc.source = L.assembly_source(asm)
    dwg.doc.refresh(notify=False)
    return st, pid


def _tab_data(store, fd):
    return {t.id: store.read_tab(t.id) for t in fd.tabs}


@pytest.fixture
def no_writes(monkeypatch, tmp_path):
    """Refuse every write under the test's folders and the working directory. The kernel writes scratch files
    when it serialises shapes, in the system temp folder, so that folder alone is allowed."""
    scratch = os.path.realpath(tempfile.gettempdir())
    denied = [os.path.realpath(str(tmp_path))]      # the test's own folders sit inside the temp folder
    cwd = os.path.realpath(os.getcwd())
    if not (scratch + os.sep).startswith(cwd + os.sep):
        denied.append(cwd)
    real_open, real_makedirs, real_replace, real_rename = builtins.open, os.makedirs, os.replace, os.rename
    real_os_open, real_mkdir = os.open, os.mkdir

    def refused(path):
        full = os.path.realpath(os.fspath(path))
        return any(full == d or full.startswith(d + os.sep) for d in denied)

    def guard(real, mode_of=None):
        def call(path, *a, **kw):
            if isinstance(path, (str, os.PathLike)) and refused(path) and (mode_of is None or mode_of(a, kw)):
                raise AssertionError(f"wrote to disk: {path}")
            return real(path, *a, **kw)
        return call

    def open_writes(a, kw):
        mode = a[0] if a else kw.get("mode", "r")
        return isinstance(mode, str) and any(c in mode for c in "wax+")

    def os_open_writes(a, kw):
        flags = a[0] if a else kw.get("flags", 0)
        return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))

    def two(real):
        def call(src, dst, *a, **kw):
            if refused(src) or refused(dst):
                raise AssertionError(f"wrote to disk: {dst}")
            return real(src, dst, *a, **kw)
        return call

    monkeypatch.setattr(builtins, "open", guard(real_open, open_writes))
    monkeypatch.setattr(os, "open", guard(real_os_open, os_open_writes))
    monkeypatch.setattr(os, "makedirs", guard(real_makedirs))
    monkeypatch.setattr(os, "mkdir", guard(real_mkdir))
    monkeypatch.setattr(os, "replace", two(real_replace))
    monkeypatch.setattr(os, "rename", two(real_rename))
    # the system temp folder is the one place a write is allowed; a temp file made anywhere else is refused
    real_mkstemp, real_mkdtemp = tempfile.mkstemp, tempfile.mkdtemp

    def mk(real):
        def call(*a, **kw):
            d = kw.get("dir") or (a[2] if len(a) > 2 else None)
            if d is not None and refused(d):
                raise AssertionError(f"wrote to disk: {d}")
            return real(*a, **kw)
        return call

    monkeypatch.setattr(tempfile, "mkstemp", mk(real_mkstemp))
    monkeypatch.setattr(tempfile, "mkdtemp", mk(real_mkdtemp))


def test_a_document_on_a_memory_store_saves_and_reopens_without_touching_disk(tmp_path, no_writes):
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store, name="doc-mem")
    assert fd.path is None and fd.store is store and not fd.trusted
    st, pid = _build(fd)
    want = _hash(st.doc.state.parts[pid])
    assert st.doc.state.vars["w"] == 20
    fd.save()
    assert not fd.unsaved_tabs()
    assert sorted(store.list_tabs()) == sorted(t.id for t in fd.tabs)
    ids = [t.id for t in fd.tabs]
    del fd, st

    back = DF.FolderDocument.open_on(store)
    assert not back.errors and not back.warnings, (back.errors, back.warnings)
    assert [t.id for t in back.tabs] == ids and back.path is None and back.store is store
    st2, asm, dwg = (back.tab(i) for i in ("T2", "T3", "T4"))
    assert [t.kind for t in (st2, asm, dwg)] == ["studio", "assembly", "drawing"]
    assert st2.doc.state.vars["w"] == 20
    assert _hash(st2.doc.state.parts[pid]) == want
    inst = asm.doc.instances[0]
    assert inst["source"]["tab"] == st2.id and "path" not in inst["source"] and "relpath" not in inst["source"]
    assert not asm.doc.errors and asm.doc.parts[inst["id"]].shape is not None
    assert _hash(asm.doc.parts[inst["id"]]) == want                    # the instance resolved to the studio's part
    assert dwg.doc.source["tab"] == asm.id and not dwg.doc.error and dwg.doc.shape is not None
    assert back.resolver.resolve_source(inst["source"]) is not None
    # a link change shows through the Document: the Variable Studio edit reaches the studio and the Assembly
    back.tab("T1").doc.set_row(0, expression="30 mm")
    back.refresh_links()
    assert st2.doc.state.vars["w"] == 30 and _hash(asm.doc.parts[inst["id"]]) != want


def test_add_rename_and_delete_a_tab_on_a_memory_store(tmp_path, no_writes):
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store)
    _build(fd)
    fd.save()
    extra = fd.add_tab("studio", "Extra")
    assert extra.file == "Extra.fshape" and not store.has_tab(extra.id)
    fd.save()
    assert store.has_tab(extra.id) and store.file_taken("Extra.fshape")
    fd.tab("T2").name = "Renamed"                              # the manifest holds the name; data is keyed by id
    fd.save()
    again = DF.FolderDocument.open_on(store)
    assert again.tab("T2").name == "Renamed" and not again.errors
    assert not again.tab("T3").doc.errors
    fd.remove_tab(extra.id)
    store.delete_tab(extra.id)
    fd.save()
    final = DF.FolderDocument.open_on(store)
    assert [t.id for t in final.tabs] == ["T1", "T2", "T3", "T4"] and not final.warnings
    store.delete_tab("T2")                                     # a tab with no data is reported, not fatal
    broken = DF.FolderDocument.open_on(store)
    assert "T2" in broken.errors
    assert "missing-file" in {w["problem"] for w in broken.document_warnings()} or "T2" in broken.errors


def test_a_link_to_a_tab_the_document_lacks_is_reported_without_a_folder(tmp_path):
    fd = DF.FolderDocument.create_on(DS.MemoryStore())
    st = fd.add_tab("studio", "S")
    asm = fd.add_tab("assembly", "A")
    asm.doc.instances = []
    with pytest.raises(K.KernelError, match="no longer in the Document"):
        fd.resolver.resolve_source({"kind": "studio", "tab": "T99", "part": None})
    # a legacy path names no tab: it is never read from disk, even if the file exists
    loose = tmp_path / "x.fshape"
    st.doc.save(str(loose))
    with pytest.raises(K.KernelError, match="names no tab of the Document"):
        fd.resolver.resolve_studio({"kind": "studio", "uid": "nope", "path": str(loose)})


def test_the_same_script_writes_the_same_bytes_on_a_memory_store_and_a_folder_store(tmp_path):
    mem = DS.MemoryStore()
    fm = DF.FolderDocument.create_on(mem)
    _build(fm)
    fm.save()
    folder = DS.FolderStore(str(tmp_path / "doc"))
    ff = DF.FolderDocument.create_on(folder)
    _build(ff)
    ff.save()
    assert [t.file for t in fm.tabs] == [t.file for t in ff.tabs]
    assert _tab_data(mem, fm) == _tab_data(folder, ff)
    assert mem.read_manifest() == folder.read_manifest()
    # and what a plain folder Document (FolderDocument.create) writes is the same again
    plain = DF.FolderDocument.create(str(tmp_path / "plain"))
    _build(plain)
    plain.save()
    assert _tab_data(plain.store, plain) == _tab_data(mem, fm)


def test_both_stores_refuse_the_same_bad_file_names(tmp_path):
    for bad, why in (("../x.fshape", "'..' segment"), ("/abs.fshape", "absolute path"), ("", "missing a file"),
                     ("a/../../b.fshape", "'..' segment"), ("C:/x.fshape", "absolute path")):
        messages = []
        for store in (DS.MemoryStore(), DS.FolderStore(str(tmp_path))):
            with pytest.raises(K.KernelError, match=why) as e:
                store.place_tab("T1", bad)
            messages.append(str(e.value))
        assert messages[0] == messages[1]
    mem = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(mem)
    with pytest.raises(K.KernelError, match="'..' segment"):
        fd.adopt_tab(fd.add_tab("studio", "S").doc, "studio", tab_id="T9", file="../evil.fshape")


def test_memory_store_blobs_are_checked_like_the_folder_blobs():
    from freeshape import blobstore
    store = DS.MemoryStore()
    key = store.write_blob(b"hello", "T1")
    assert store.read_blob(key, "T1") == b"hello" and store.has_blob(key, "T1") and not store.has_blob(key, "T2")
    assert store.blobs("T1").get(key) == b"hello" and store.blobs("T1").put(b"hello") == key
    for bad in ("short", "A" * 64):
        with pytest.raises(blobstore.BlobError):
            store.read_blob(bad, "T1")
        assert not store.has_blob(bad, "T1")
    with pytest.raises(blobstore.BlobError, match="no blob"):
        store.read_blob("0" * 64, "T1")
    with pytest.raises(blobstore.BlobError, match="over the 2 byte limit"):
        store.read_blob(key, "T1", max_bytes=2)
    with pytest.raises(K.KernelError, match="missing"):
        store.read_tab("T1")
    with pytest.raises(K.KernelError, match="missing"):
        store.read_manifest()
    store.write_manifest(b"{}")
    with pytest.raises(K.KernelError, match="over 1 bytes"):
        store.read_manifest(max_bytes=1)


def test_versions_workspaces_and_merge_need_a_folder_on_a_memory_store():
    fd = DF.FolderDocument.create_on(DS.MemoryStore())
    fd.add_tab("studio", "S")
    fd.save()
    need = "need a Document kept in a folder under git; this one is kept in an in-memory store"
    for call in (lambda: fd.create_version("v1"), fd.versions, lambda: fd.open_version("v1"), fd.workspaces,
                 lambda: fd.create_workspace("w"), lambda: fd.switch_workspace("w"),
                 lambda: fd.merge_preview("w"), lambda: fd.merge_workspace("w"), lambda: fd.push("x")):
        with pytest.raises(K.KernelError, match=need):
            call()
    assert "in-memory store" in fd.git_problem()
    with pytest.raises(K.KernelError, match=need):
        DS.git_folder(fd.store)


def test_the_write_guard_refuses_writes_and_allows_the_system_temp_folder(tmp_path, no_writes):
    for call in (lambda: open(tmp_path / "a", "w"), lambda: os.makedirs(tmp_path / "d"),
                 lambda: os.replace(tmp_path / "a", tmp_path / "b"), lambda: tempfile.mkstemp(dir=str(tmp_path)),
                 lambda: os.open(str(tmp_path / "c"), os.O_WRONLY | os.O_CREAT)):
        with pytest.raises(AssertionError, match="wrote to disk"):
            call()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "scratch"), "wb") as fh:
            fh.write(b"ok")
