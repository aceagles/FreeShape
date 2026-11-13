"""I4.7: a Python feature's parts carry element names, its last build is stored in blobs/ on save, and a
Part Studio whose plugin is missing is locked on that stored build.

Every test points FREESHAPE_PLUGIN_DIR at an empty tmp dir and unregisters the plugins it registered.
"""
import builtins
import json
import os
import shutil
import tempfile

import pytest
from conftest import TOP, add, rect_lines, region_refs, sketch_feature

from freeshape import blobstore as B
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import kernel as K
from freeshape import naming as N
from freeshape import pyfeature
from freeshape.document import Document
from freeshape.pyfeature import host

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins")
LABELLED = os.path.join(DATA, "labelled")
FACES = ("front", "back", "left", "right", "top", "bottom")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
needs_symlink = pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform has no symlink support")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    yield user
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


@pytest.fixture
def labelled():
    info = pyfeature.register_plugin(LABELLED)
    assert info is not None, pyfeature.problems()
    return info


def add_py(doc, ftype, **params):
    f = doc.make_feature(ftype)
    f["params"].update(params)
    doc.add_feature(f)
    return f


def names_of(doc_or_state, pid):
    st = doc_or_state.state if isinstance(doc_or_state, Document) else doc_or_state
    return host.names_json(st.named(pid))


def face_named(state, pid, label):
    """(face, name) of the face whose name starts with `label;`."""
    hits = [(s, n) for s, n in state.named(pid).named(N.FACE) if n.startswith(label + ";")]
    assert len(hits) == 1, hits
    return K._as_face(hits[0][0]), hits[0][1]


def volume(doc, pid):
    return K.volume(doc.state.parts[pid].shape)


def top_face_ref(doc, pid):
    face, _ = face_named(doc.state, pid, "top")
    part = doc.state.parts[pid]
    return F.make_ref(pid, face, "face", part.shape, named=doc.state.named(pid))


def studio_with_box(doc, **params):
    """A labelled box, a sketch on its top face (referenced by name) extruded as a new part, and a sketch
    on the Top plane extruded as another new part."""
    box = add_py(doc, "py:labelled/box", **params)
    pid = f"{box['id']}/1"
    ref = top_face_ref(doc, pid)
    assert ref.get("name", "").startswith("top;")
    on_face = sketch_feature(doc, lambda s: rect_lines(s, 1, 1, 3, 3),
                             {"kind": "face", "face": ref, "origin": [0, 0, 30], "xdir": [1, 0, 0]})
    add(doc, "extrude", regions=region_refs(doc, on_face), depth=5, op="new")
    on_top = sketch_feature(doc, lambda s: rect_lines(s, 50, 0, 4, 4), TOP)
    add(doc, "extrude", regions=region_refs(doc, on_top), depth=2, op="new")
    assert doc.errors == {}
    return box, pid


def saved(tmp_path, name="studio.fshape", **params):
    doc = Document()
    box, pid = studio_with_box(doc, **params)
    path = str(tmp_path / "doc" / name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.save(path)
    return doc, box, pid, path


def snapshot(doc):
    return {pid: (round(K.volume(p.shape), 6), names_of(doc, pid)) for pid, p in doc.state.parts.items()}


# =========================================================================== names

def test_labelled_box_parts_carry_names_with_feature_id(labelled):
    doc = Document()
    f = add_py(doc, "py:labelled/box")
    pid = f"{f['id']}/1"
    named = doc.state.named(pid)
    assert named is not None
    counts = {N.FACE: 6, N.EDGE: 12, N.VERTEX: 8}
    for letter in (N.FACE, N.EDGE, N.VERTEX):
        names = named.names_of(letter)
        assert len(names) == counts[letter] == len(named.keys(letter)) and all(names)
        assert all(f["id"] in n for n in names), names
    top, name = face_named(doc.state, pid, "top")
    assert "top" in name
    assert K.face_info(top)["c"][2] == pytest.approx(30.0)
    assert {n.split(";")[0] for n in named.names_of(N.FACE)} == set(FACES)


def test_resizing_keeps_every_face_name(labelled):
    doc = Document()
    f = add_py(doc, "py:labelled/box")
    pid = f"{f['id']}/1"
    before = sorted(doc.state.named(pid).names_of(N.FACE))
    new = json.loads(json.dumps(f))
    new["params"].update(w=13.0, d=7.5, h=4.0)
    doc.replace_feature(new)
    assert doc.errors == {}
    assert volume(doc, pid) == pytest.approx(13 * 7.5 * 4)
    assert sorted(doc.state.named(pid).names_of(N.FACE)) == before
    top, _ = face_named(doc.state, pid, "top")
    assert K.face_info(top)["c"][2] == pytest.approx(4.0)


def test_plain_shape_gets_index_names_on_every_face(labelled):
    doc = Document()
    f = add_py(doc, "py:labelled/plain")
    pid = f"{f['id']}/1"
    names = doc.state.named(pid).names_of(N.FACE)
    assert len(names) == 6
    assert sorted(n.split(";")[0] for n in names) == [f"f{i}" for i in range(1, 7)]
    assert all(f";{host.OP_PYFEATURE};" in n and f["id"] in n for n in names)


def test_each_solid_of_a_multi_solid_result_takes_its_own_names(labelled):
    doc = Document()
    f = add_py(doc, "py:labelled/pair")
    a, b = f"{f['id']}/1", f"{f['id']}/2"
    assert list(doc.state.parts) == [a, b]
    assert {n.split(";")[0] for n in doc.state.named(a).names_of(N.FACE)} == {"a_" + k for k in FACES}
    assert {n.split(";")[0] for n in doc.state.named(b).names_of(N.FACE)} == {"b_" + k for k in FACES}
    for pid in (a, b):
        assert not doc.state.named(pid).unnamed()


# =========================================================================== save

def test_save_writes_one_brep_and_one_names_blob_per_part(labelled, tmp_path):
    doc = Document()
    box = add_py(doc, "py:labelled/box")
    pair = add_py(doc, "py:labelled/pair")
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    with open(path) as fh:
        data = json.load(fh)
    builds = data["python_builds"]
    assert set(builds) == {box["id"], pair["id"]}
    store = B.store_for(path)
    assert os.path.isdir(tmp_path / "blobs")
    keys = []
    for fid, n in ((box["id"], 1), (pair["id"], 2)):
        entry = builds[fid]
        assert entry["plugin"] == {"id": "labelled", "version": "1.0.0"}
        assert [p["id"] for p in entry["parts"]] == [f"{fid}/{i}" for i in range(1, n + 1)]
        for p in entry["parts"]:
            assert set(p) == {"id", "brep", "names"}
            keys += [p["brep"], p["names"]]
            assert B.load_json(store.get(p["names"])) == json.loads(json.dumps(names_of(doc, p["id"])))
            shape = B.shape_from_brep(store.get(p["brep"]))
            assert K.volume(shape) == pytest.approx(volume(doc, p["id"]))
    assert sorted(keys) == store.keys() and len(keys) == 6


def test_deleted_feature_entry_is_dropped_on_next_save(labelled, tmp_path):
    doc = Document()
    a = add_py(doc, "py:labelled/box")
    b = add_py(doc, "py:labelled/plain")
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    doc.delete_features({a["id"]})
    doc.save(path)
    with open(path) as fh:
        assert set(json.load(fh)["python_builds"]) == {b["id"]}


def test_no_python_features_writes_no_builds_and_no_blobs(tmp_path):
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 4, 4), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth=2, op="new")
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    with open(path) as fh:
        assert "python_builds" not in json.load(fh)
    assert not os.path.exists(tmp_path / "blobs")


# =========================================================================== locked when the plugin is missing

def test_missing_plugin_locks_on_stored_build_and_references_still_resolve(labelled, tmp_path):
    doc, box, pid, path = saved(tmp_path)
    before = snapshot(doc)
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    assert loaded.errors == {}
    assert snapshot(loaded) == before                        # same ids, volumes and names, downstream too
    assert list(loaded.state.parts)[0] == pid
    text = 'locked: plugin "labelled" 1.0.0 is not installed; showing its last build'
    assert loaded.notices() == {box["id"]: text}
    assert pyfeature.notices(loaded) == {box["id"]: text}
    assert pyfeature.notices(loaded.features, loaded.locked_builds()) == {box["id"]: text}
    # the sketch on the box's top face found it by name in the stored map
    top, _ = face_named(loaded.state, pid, "top")
    assert K.face_info(top)["c"][2] == pytest.approx(30.0)
    # editing a downstream built-in feature still rebuilds
    ext = loaded.features[-1]
    new = json.loads(json.dumps(ext))
    new["params"]["depth"] = 7
    loaded.replace_feature(new)
    assert loaded.errors == {}
    last = list(loaded.state.parts)[-1]
    assert K.volume(loaded.state.parts[last].shape) == pytest.approx(4 * 4 * 7)
    # the plugin comes back: the feature rebuilds and the notice is gone
    assert pyfeature.register_plugin(LABELLED) is not None
    loaded.rebuild()
    assert loaded.errors == {}
    assert loaded.notices() == {}
    assert loaded.locked_builds() == {}
    assert names_of(loaded, pid) == before[pid][1]


def test_without_a_stored_build_the_missing_plugin_error_stands(labelled, tmp_path):
    doc, box, pid, path = saved(tmp_path)
    with open(path) as fh:
        data = json.load(fh)
    del data["python_builds"]
    with open(path, "w") as fh:
        json.dump(data, fh)
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    assert "not installed" in loaded.errors[box["id"]]
    assert "locked" not in pyfeature.notices(loaded)[box["id"]]


def test_locked_build_is_kept_on_resave_and_save_elsewhere(labelled, tmp_path):
    doc, box, pid, path = saved(tmp_path)
    before = snapshot(doc)
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    loaded.save(path)
    other = str(tmp_path / "elsewhere" / "copy.fshape")
    os.makedirs(os.path.dirname(other))
    loaded.save(other)
    for p in (path, other):
        again = Document.load(p)
        assert again.errors == {} and snapshot(again) == before
        assert set(again.locked_builds()) == {box["id"]}


# =========================================================================== damaged and hostile stored builds

def _entry(path, fid):
    with open(path) as fh:
        return json.load(fh)["python_builds"][fid]


def _edit_builds(path, fn):
    with open(path) as fh:
        data = json.load(fh)
    fn(data["python_builds"])
    with open(path, "w") as fh:
        json.dump(data, fh)


def two_boxes_saved(tmp_path):
    doc = Document()
    a = add_py(doc, "py:labelled/box")
    b = add_py(doc, "py:labelled/box", w=3.0)
    path = str(tmp_path / "doc" / "s.fshape")
    os.makedirs(os.path.dirname(path))
    doc.save(path)
    return a, b, path


def test_overwritten_blob_damages_that_feature_only(labelled, tmp_path):
    a, b, path = two_boxes_saved(tmp_path)
    key = _entry(path, a["id"])["parts"][0]["brep"]
    with open(os.path.join(os.path.dirname(path), "blobs", key), "wb") as fh:
        fh.write(b"not the shape")
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    assert loaded.errors == {a["id"]: 'stored build of "Labelled box 1" is damaged'}
    assert list(loaded.state.parts) == [f"{b['id']}/1"]
    assert set(loaded.locked_builds()) == {b["id"]}


@pytest.mark.parametrize("bad", [
    {"parts": [{"id": "?", "brep": "../../x", "names": "../../x"}]},
    "not an entry", {"parts": []}, {"parts": "x"}, {"parts": [None]},
])
def test_malformed_entry_is_damaged_and_opens_nothing_outside_blobs(labelled, tmp_path, monkeypatch, bad):
    a, b, path = two_boxes_saved(tmp_path)
    folder = os.path.dirname(path)
    entry = _entry(path, a["id"])
    if isinstance(bad, dict) and isinstance(bad["parts"], list) and bad["parts"] and bad["parts"][0]:
        bad["parts"][0]["id"] = entry["parts"][0]["id"]
    _edit_builds(path, lambda builds: builds.__setitem__(a["id"], bad))
    sentinel = os.path.join(os.path.dirname(folder), "x")
    with open(sentinel, "w") as fh:
        fh.write("outside")
    pyfeature.unregister_plugin("labelled")
    opened = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(file, *a, **k):
        opened.append(os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else file)
        return real_open(file, *a, **k)

    def spy_os_open(file, *a, **k):
        opened.append(os.fspath(file))
        return real_os_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    loaded = Document.load(path)
    monkeypatch.undo()
    assert loaded.errors == {a["id"]: 'stored build of "Labelled box 1" is damaged'}
    assert set(loaded.locked_builds()) == {b["id"]}
    blobs = os.path.realpath(os.path.join(folder, "blobs"))
    for p in opened:
        if not isinstance(p, str):
            continue
        rp = os.path.realpath(p)
        assert rp != os.path.realpath(sentinel)
        # shape_from_brep's own scratch folder (tempfile.TemporaryDirectory) for the valid feature's BREP
        rel = os.path.relpath(rp, os.path.realpath(tempfile.gettempdir()))
        scratch = rel.startswith("tmp") and not rel.startswith("tmp" + os.sep + "..")
        assert rp == os.path.realpath(path) or rp.startswith(blobs + os.sep) or scratch, p


@needs_symlink
def test_symlinked_blob_is_damaged(labelled, tmp_path):
    a, b, path = two_boxes_saved(tmp_path)
    blobs = os.path.join(os.path.dirname(path), "blobs")
    key = _entry(path, a["id"])["parts"][0]["brep"]
    outside = tmp_path / "outside_blob"
    shutil.move(os.path.join(blobs, key), outside)        # same bytes, so only the link is wrong
    os.symlink(outside, os.path.join(blobs, key))
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    assert loaded.errors == {a["id"]: 'stored build of "Labelled box 1" is damaged'}


@needs_symlink
def test_symlinked_blobs_folder_is_damaged(labelled, tmp_path, monkeypatch):
    a, b, path = two_boxes_saved(tmp_path)
    blobs = os.path.join(os.path.dirname(path), "blobs")
    outside = tmp_path / "outside_blobs"
    shutil.move(blobs, outside)                            # valid blobs, but reached through a link
    os.symlink(outside, blobs)
    pyfeature.unregister_plugin("labelled")
    opened = []
    real_os_open = os.open
    monkeypatch.setattr(os, "open", lambda f, *x, **k: (opened.append(os.fspath(f)), real_os_open(f, *x, **k))[1])
    loaded = Document.load(path)
    monkeypatch.undo()
    assert loaded.errors == {a["id"]: 'stored build of "Labelled box 1" is damaged',
                             b["id"]: 'stored build of "Labelled box 2" is damaged'}
    assert not any(os.path.realpath(p).startswith(os.path.realpath(outside)) for p in opened)


def test_oversize_blob_is_damaged(labelled, tmp_path, monkeypatch):
    a, b, path = two_boxes_saved(tmp_path)
    monkeypatch.setattr(host, "MAX_BLOB_BYTES", 64)
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    assert set(loaded.errors) == {a["id"], b["id"]}
    assert all(e.endswith("is damaged") for e in loaded.errors.values())


def test_names_blob_with_bad_index_is_damaged(labelled, tmp_path):
    a, b, path = two_boxes_saved(tmp_path)
    store = B.store_for(path)
    bad = store.put(B.dump_json({"face": {"99": "x;PYF"}, "edge": {}, "vertex": {}}))
    _edit_builds(path, lambda builds: builds[a["id"]]["parts"][0].__setitem__("names", bad))
    pyfeature.unregister_plugin("labelled")
    loaded = Document.load(path)
    assert loaded.errors == {a["id"]: 'stored build of "Labelled box 1" is damaged'}


# =========================================================================== a folder Document and git

def folder_doc(tmp_path):
    reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=reg)
    st = doc.add_tab("studio", "Gears")
    box, pid = studio_with_box(st.doc)
    doc.save()
    return reg, doc, st, box, pid


def _studio(folder):
    tab = next(t for t in folder.tabs if t.kind == "studio")
    assert not folder.errors, folder.errors
    return tab.doc


def test_folder_save_writes_blobs_and_open_locks(labelled, tmp_path):
    reg, doc, st, box, pid = folder_doc(tmp_path)
    before = snapshot(st.doc)
    keys = B.store_for(os.path.join(doc.path, st.file)).keys()
    assert len(keys) == 2
    pyfeature.unregister_plugin("labelled")
    back = _studio(DF.FolderDocument.open(doc.path))
    assert back.errors == {} and snapshot(back) == before
    assert set(back.locked_builds()) == {box["id"]}


@needs_git
def test_blobs_are_versioned_imported_and_opened_from_a_version(labelled, tmp_path):
    from freeshape import gitstore as G
    reg, doc, st, box, pid = folder_doc(tmp_path)
    before = snapshot(st.doc)
    doc.create_version("v1")
    tracked = G.run(["ls-files"], doc.path).stdout.split()
    keys = B.store_for(os.path.join(doc.path, st.file)).keys()
    assert all(f"blobs/{k}" in tracked for k in keys) and keys
    pyfeature.unregister_plugin("labelled")
    view = doc.open_version("v1")
    try:
        locked = _studio(view)
        assert locked.errors == {} and snapshot(locked) == before
    finally:
        view.close()
    copy = tmp_path / "received"
    shutil.copytree(doc.path, copy, symlinks=True)
    imported = DF.FolderDocument.open_document(str(copy), registry=reg, managed_root=str(tmp_path / "managed"))
    assert os.path.realpath(imported.path) != os.path.realpath(copy)
    got = _studio(imported)
    assert got.errors == {} and snapshot(got) == before
    assert set(got.locked_builds()) == {box["id"]}


@needs_symlink
def test_hostile_blobs_folder_in_a_folder_document_does_not_break_open(labelled, tmp_path):
    reg, doc, st, box, pid = folder_doc(tmp_path)
    blobs = os.path.join(doc.path, "blobs")
    outside = tmp_path / "elsewhere"
    shutil.move(blobs, outside)
    os.symlink(outside, blobs)
    pyfeature.unregister_plugin("labelled")
    back = DF.FolderDocument.open(doc.path)
    studio = _studio(back)
    assert studio.errors[box["id"]].endswith("is damaged")
    # the extrude on the Top plane does not depend on the box and still rebuilds
    assert list(studio.state.parts) == [f"{studio.features[-1]['id']}/1"]
