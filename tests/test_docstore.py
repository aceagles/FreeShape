"""I4.6: a Document's tabs, manifest and blobs go through a DocumentStore (ADR 0009); FolderStore keeps today's
bytes on disk."""
import hashlib
import json
import os
import shutil

import pytest
from conftest import EXAMPLES_DIR, TOP, add, rect_lines, region_refs, sketch_feature
from test_drawing import DIRECTIONS
from test_pyfeature_outputs import LABELLED, add_py

from freeshape import doc_folder as DF
from freeshape import docstore as DS
from freeshape import kernel as K
from freeshape import links as L
from freeshape import pyfeature


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    yield
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


# =========================================================================== the Documents saved

EXAMPLE_SETS = {"bracket": ["bracket.fshape"],
                "hinge": ["hinge_parts.fshape", "hinge.fasm"],
                "bolted_bracket": ["bolted_bracket_parts.fshape", "bolted_bracket.fasm"]}


def _pin(fd, doc_id):
    """Fix the ids that are random per run, so two runs write the same bytes."""
    fd.id = doc_id
    fd.name = doc_id
    return fd


def _example_document(base, key):
    """A Document made from one set of example files by add_files (which saves it)."""
    src = os.path.join(base, "src", key)
    os.makedirs(src)
    for name in EXAMPLE_SETS[key]:
        shutil.copy(os.path.join(EXAMPLES_DIR, name), os.path.join(src, name))
    fd = _pin(DF.FolderDocument(), "doc-" + key)
    fd.path = os.path.join(base, "docs", key)
    os.makedirs(fd.path)
    fd.add_files([os.path.join(src, n) for n in EXAMPLE_SETS[key]])
    return fd


def _built_document(base):
    """A Document with all four tab kinds; the Part Studio has a Python feature with a stored build."""
    assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
    fd = _pin(DF.FolderDocument(), "doc-built")
    fd.path = os.path.join(base, "docs", "built")
    st = fd.add_tab("studio", "Studio")
    st.doc.uid = "uid-studio"
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 20, 20), TOP)
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth=10.0, op="new")
    add_py(st.doc, "py:labelled/box")
    assert not st.doc.errors, st.doc.errors
    pid = next(iter(st.doc.state.parts))
    asm = fd.add_tab("assembly", "Asm")
    asm.doc.uid = "uid-asm"
    asm.doc.insert(L.studio_source(st, pid), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    dwg = fd.add_tab("drawing", "Dwg")
    dwg.doc.uid = "uid-dwg"
    dwg.doc.source = L.studio_source(st, pid)
    dwg.doc.refresh(notify=False)
    dwg.doc.add_view(*DIRECTIONS["Front"], (100, 150), checkpoint=False)
    var = fd.add_tab("variables", "Vars")
    var.doc.rows = [{"name": "w", "expression": "20 mm"}, {"name": "h", "expression": "w / 2"}]
    fd.save()
    assert not st.doc.python_builds == {}, "the Python feature stored no build"
    return fd


def _hashes(folder):
    """{relative path: sha256} of every file in a Document folder."""
    out = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for f in sorted(files):
            full = os.path.join(root, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, folder).replace(os.sep, "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _resave(folder):
    """Open a saved Document, mark every tab dirty and save it again in place."""
    fd = DF.FolderDocument.open(folder)
    assert not fd.errors, fd.errors
    for tab in fd.tabs:
        with open(os.path.join(folder, tab.file), "rb") as fh:
            if "uid" not in json.loads(fh.read()):      # bracket.fshape has none: the load made a random one
                tab.doc.uid = "uid-" + tab.id
        tab.doc.dirty = True
    fd.save()
    return fd


def _save_all(base):
    """{document key: {"saved": hashes, "resaved": hashes}} for every example Document and the built one."""
    out = {}
    folders = {k: _example_document(base, k).path for k in EXAMPLE_SETS}
    folders["built"] = _built_document(base).path
    for key, folder in folders.items():
        out[key] = {"saved": _hashes(folder)}
    for key, folder in folders.items():
        _resave(folder)
        out[key]["resaved"] = _hashes(folder)
    return out


# sha256 of every file each Document's save wrote at commit 0ed1882 (before this card), the same on OCP 7.9 and
# 8.0; "resaved" is after opening the saved Document, marking every tab dirty and saving it again in place.
BEFORE = {
    'bolted_bracket': {
        'saved': {
            'bolted_bracket.fasm':
                'e6af173889dc324e91d20e01c595c4c576a08c013b06d8addba9ebeb57cdb78b',
            'bolted_bracket_parts.fshape':
                'd78e42b624c96ca05a885eb87282fb4975532a3584d1e1ae22987ad350ee8547',
            'document.json':
                'daf12f6b952cf1df83bb3aacfb077f45e11d03229c71212aee5e4d54bf87d5a9',
        },
        'resaved': {
            'bolted_bracket.fasm':
                'e6af173889dc324e91d20e01c595c4c576a08c013b06d8addba9ebeb57cdb78b',
            'bolted_bracket_parts.fshape':
                'd78e42b624c96ca05a885eb87282fb4975532a3584d1e1ae22987ad350ee8547',
            'document.json':
                'daf12f6b952cf1df83bb3aacfb077f45e11d03229c71212aee5e4d54bf87d5a9',
        },
    },
    'bracket': {
        'saved': {
            'bracket.fshape':
                '6c1d3d17e36ce4a37b3717b42e408eeda823d41664a5e0e1cde211abcabbd351',
            'document.json':
                '356cc886bbf61d1f3bf99378ece1590f20698510599f0e70ed869ffa4c4e2827',
        },
        'resaved': {
            'bracket.fshape':
                'c42e87035fde2971cc0cf958d741eb2d43d8081f3f289c4480d8e6c5a500d699',
            'document.json':
                '356cc886bbf61d1f3bf99378ece1590f20698510599f0e70ed869ffa4c4e2827',
        },
    },
    'built': {
        'saved': {
            'Asm.fasm':
                '6029eb72d3fc0b89e6a726c3817be9e798980070af792d0a0ad187464d0e792c',
            'Dwg.fdwg':
                '9b8f06687d1b07c69581a36f1a6246708adfb79bcb2c74eefeda1bdeb4f26409',
            'Studio.fshape':
                'b02955ff05f4154afec8155e5243f801d77342341a71a73d04579af69c8afaf8',
            'Vars.fvars':
                'dbca3925a4c877982c7b7190459ce9cba4779c56d068c9951ee007700de0c86c',
            'blobs/d4c57ab80879e2853080a97a1ff00bc5f3fd2a4a2758b6acc22fc962c80b7b19':
                'd4c57ab80879e2853080a97a1ff00bc5f3fd2a4a2758b6acc22fc962c80b7b19',
            'blobs/e27641546df8ad051f91ae83fc49ac6e9a8008816bbc188afdd265ef9aec5cb5':
                'e27641546df8ad051f91ae83fc49ac6e9a8008816bbc188afdd265ef9aec5cb5',
            'document.json':
                '81ca62d0994dabb3c23138e841e65f42ecb77c2b64ba96cf280cc3d4c9084f4d',
        },
        'resaved': {
            'Asm.fasm':
                '6029eb72d3fc0b89e6a726c3817be9e798980070af792d0a0ad187464d0e792c',
            'Dwg.fdwg':
                '1a6ffe9ca3f66d6f495e09788e612ce7d62fd7760586741d8ecbfe9e772fce9b',
            'Studio.fshape':
                'b02955ff05f4154afec8155e5243f801d77342341a71a73d04579af69c8afaf8',
            'Vars.fvars':
                'dbca3925a4c877982c7b7190459ce9cba4779c56d068c9951ee007700de0c86c',
            'blobs/d4c57ab80879e2853080a97a1ff00bc5f3fd2a4a2758b6acc22fc962c80b7b19':
                'd4c57ab80879e2853080a97a1ff00bc5f3fd2a4a2758b6acc22fc962c80b7b19',
            'blobs/e27641546df8ad051f91ae83fc49ac6e9a8008816bbc188afdd265ef9aec5cb5':
                'e27641546df8ad051f91ae83fc49ac6e9a8008816bbc188afdd265ef9aec5cb5',
            'document.json':
                '81ca62d0994dabb3c23138e841e65f42ecb77c2b64ba96cf280cc3d4c9084f4d',
        },
    },
    'hinge': {
        'saved': {
            'document.json':
                '593e8418f86ab9035bdea279960a3bd39f8e47c7b858a29c8525b3b4b777db6a',
            'hinge.fasm':
                '851b136b9e439857f222785b75f62794eb3b0aea157ba5bf5dc9dfe2c06ab262',
            'hinge_parts.fshape':
                'd7f5336ecbbcc841d7c8cc39039840d237c4e266ef6023d52222a4659503ea2b',
        },
        'resaved': {
            'document.json':
                '593e8418f86ab9035bdea279960a3bd39f8e47c7b858a29c8525b3b4b777db6a',
            'hinge.fasm':
                '851b136b9e439857f222785b75f62794eb3b0aea157ba5bf5dc9dfe2c06ab262',
            'hinge_parts.fshape':
                'f0da97d792b8ad1dd7ca4b6d349861678a79a562e32427b728c243b0b87ee23a',
        },
    },
}


def test_every_document_saves_to_the_bytes_it_saved_before(tmp_path):
    got = _save_all(str(tmp_path))
    assert set(got) == set(BEFORE)
    for key in BEFORE:
        for phase in ("saved", "resaved"):
            assert got[key][phase] == BEFORE[key][phase], (key, phase)


# =========================================================================== all I/O goes through the store

class _Counting(DS.DocumentStore):
    """Wraps a FolderStore and counts each call."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = {}

    def __getattr__(self, name):          # has_folder, folder and anything else not counted
        return getattr(self.inner, name)

    def _count(self, name, *args, **kw):
        self.calls[name] = self.calls.get(name, 0) + 1
        return getattr(self.inner, name)(*args, **kw)

    has_folder = property(lambda self: self.inner.has_folder)
    folder = property(lambda self: self.inner.folder)

    def describe(self):
        return "a counting " + self.inner.describe()


for _name in ("read_manifest", "write_manifest", "place_tab", "list_tabs", "has_tab", "read_tab", "write_tab",
              "delete_tab", "strays", "tab_path", "checked_tab_path", "file_taken", "read_blob", "write_blob",
              "has_blob", "blobs"):
    setattr(_Counting, _name, (lambda n: lambda self, *a, **kw: self._count(n, *a, **kw))(_name))
_Counting.__abstractmethods__ = frozenset()


@pytest.fixture
def opens_from_doc_folder(monkeypatch):
    """Paths of tab files and document.json that code in doc_folder.py opens directly."""
    import builtins
    import sys
    real, seen = builtins.open, []
    exts = (".json",) + tuple(DF.EXTENSIONS.values())

    def spy(file, *a, **kw):
        caller = sys._getframe(1).f_code.co_filename
        if os.path.basename(caller) == "doc_folder.py" and str(file).endswith(exts):
            seen.append(str(file))
        return real(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    return seen


def test_open_and_save_do_all_tab_and_manifest_io_through_the_store(tmp_path, opens_from_doc_folder):
    folder = _built_document(str(tmp_path)).path
    store = _Counting(DS.FolderStore(folder))
    fd = DF.FolderDocument.open(folder, store=store)
    assert not fd.errors, fd.errors
    assert fd.store is store
    n = len(fd.tabs)
    assert store.calls["read_manifest"] == 1
    assert store.calls["read_tab"] >= n          # each tab loaded, then its stamped id checked
    for tab in fd.tabs:
        tab.doc.dirty = True
    fd.save()
    assert store.calls["write_tab"] == n and store.calls["write_manifest"] == 1
    assert store.calls["blobs"] >= n            # the Part Studio's builds go to the store's blobs
    assert opens_from_doc_folder == []
    assert _hashes(folder) == BEFORE["built"]["resaved"]


# =========================================================================== from_data / to_data without disk

@pytest.fixture
def no_disk(monkeypatch, tmp_path):
    """Arm to refuse every open of a file under tmp_path, where the Document and its tab files are. The
    kernel's own scratch files (BREP encoding goes through the system temp folder) are allowed."""
    import builtins
    real_open, real_os_open, root = builtins.open, os.open, str(tmp_path)

    def guard(real):
        def call(file, *a, **kw):
            if isinstance(file, (str, os.PathLike)) and os.path.abspath(os.fspath(file)).startswith(root):
                raise AssertionError(f"touched the Document on disk: {file}")
            return real(file, *a, **kw)
        return call

    def arm():
        monkeypatch.setattr(builtins, "open", guard(real_open))
        monkeypatch.setattr(os, "open", guard(real_os_open))
    return arm


def test_from_data_round_trips_each_tab_kind_without_disk(tmp_path, no_disk):
    from freeshape import tabkinds as TK
    fd = _built_document(str(tmp_path))
    docs = {t.kind: t.doc for t in fd.tabs}
    for d in docs.values():
        d.path = None                         # no file behind them: to_data writes no relative path
    data = {k: d.to_data() for k, d in docs.items()}
    no_disk()
    for kind, raw in data.items():
        assert isinstance(raw, bytes)
        extra = {"resolver": docs[kind].resolver} if kind in ("assembly", "drawing") else {}
        back = TK.get(kind).from_data(raw, **extra)
        assert back.path is None
        assert back.to_data() == raw, kind


def test_loose_files_still_load_and_save_as_before(tmp_path):
    from freeshape import tabkinds as TK
    fd = _built_document(str(tmp_path))
    for tab in fd.tabs:
        loose = str(tmp_path / ("loose" + TK.get(tab.kind).ext))
        tab.doc.save(loose)
        with open(loose, "rb") as fh:
            assert fh.read() == tab.doc.to_data()
        back = TK.get(tab.kind).load(loose)
        assert back.path == loose and back.to_data() == tab.doc.to_data()


# =========================================================================== git stays on a folder

class _NoFolder(DS.FolderStore):
    has_folder = False

    def describe(self):
        return "a test store"


def test_versions_workspaces_and_merge_refuse_a_store_without_a_folder(tmp_path):
    folder = _built_document(str(tmp_path)).path
    fd = DF.FolderDocument.open(folder, store=_NoFolder(folder))
    fd.trusted = True
    for call in (fd.versions, fd.workspaces, lambda: fd.merge_preview("other"), lambda: fd.create_version("v1")):
        with pytest.raises(K.KernelError, match="need a Document kept in a folder under git; this one is kept "
                                                "in a test store"):
            call()
    assert "a test store" in fd.git_problem()
    with pytest.raises(K.KernelError, match="a test store"):
        DS.git_folder(DS.FolderStore.__new__(_NoFolder))
    assert DS.git_folder(DS.FolderStore(folder)) == folder


# =========================================================================== FolderStore keeps the folder rules

def test_folder_store_checks_names_and_refuses_symlinks(tmp_path):
    store = DS.FolderStore(str(tmp_path))
    for bad, why in (("../x.fshape", "'..' segment"), ("/abs.fshape", "absolute path"), ("", "missing a file")):
        with pytest.raises(K.KernelError, match=why):
            store.place_tab("T1", bad)
    store.place_tab("T1", "a.fshape")
    with pytest.raises(K.KernelError, match="missing from the Document folder"):
        store.read_tab("T1")
    store.write_tab("T1", b'{"a": 1}')
    assert store.read_tab("T1") == b'{"a": 1}' and store.has_tab("T1") and store.list_tabs() == ["T1"]
    assert not os.path.exists(str(tmp_path / "a.fshape.tmp"))
    if hasattr(os, "symlink"):
        os.symlink(str(tmp_path / "a.fshape"), str(tmp_path / "b.fshape"))
        store.place_tab("T2", "b.fshape")
        with pytest.raises(K.KernelError, match="refusing to open a symlink"):
            store.read_tab("T2")
        assert store.strays(["T1", "T2"]) == []
    key = store.write_blob(b"hello", "T1")
    assert store.read_blob(key, "T1") == b"hello" and store.has_blob(key, "T1")
    assert os.path.exists(str(tmp_path / "blobs" / key))
    store.delete_tab("T1")
    assert not store.has_tab("T1") and store.list_tabs(["T1"]) == []
