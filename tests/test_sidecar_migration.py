"""I4.9: one sidecar mechanism for binary data (ADR 0009). Assembly and Drawing files stop carrying base64
geometry, an Assembly's inline STEP files and a Part Studio's imported STEP file move to the blobs, and old files
still load: to the same geometry as before, with no store at all, and a save writes their blobs beside the file
or into the Document store. Versions, Workspaces, merge and safe import carry the blob files."""
import base64
import hashlib
import json
import os
import re
import shutil

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from conftest import EXAMPLES_DIR
from freeshape import assembly as A
from freeshape import blobstore
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import docstore as DS
from freeshape import drawing as DW
from freeshape import kernel as K
from freeshape import links as L
from freeshape import migrations as M
from freeshape.document import PartStudioDoc
from freeshape.engine.encode import Encoder

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

# Recorded on the commit before this card (83beadc), the same on OCP 7.9 and 8.0: the file size, and the geometry
# hash (`_asm_hash`, `_hash`) after loading the old file with no resolver (the instances show the copy the file
# carries) and, for an Assembly, with a LinkResolver that finds its Part Studio beside it.
BEFORE = {
    "examples/hinge.fasm": {
        "size": 7329,
        "fallback": "2d2c1ce1a8418d12630ec1322a76d95312c252b91a80e2b4d86693ffc42b8817",
        "resolved": "7991a3765f30755460e04c8a0e2c48b59e16df3c8ed9416bb8b645cfaad5de81",
        "parts": "examples/hinge_parts.fshape"},
    "examples/bolted_bracket.fasm": {
        "size": 6720,
        "fallback": "c728c7e9d7b683bd2a5c62d67cb3bbd7fd6277d56147f6cac50e7ce5b86d334b",
        "resolved": "c728c7e9d7b683bd2a5c62d67cb3bbd7fd6277d56147f6cac50e7ce5b86d334b",
        "parts": "examples/bolted_bracket_parts.fshape"},
}
# examples/ ships no Drawing; these are the old Drawing files the repo ships (tests/data). arm_v1.fdwg carries
# no geometry. `views` is which views had their exact result reused from the file right after the load.
BEFORE_DRAWINGS = {
    "tests/data/versions/v1.fdwg": {
        "size": 5676,
        "shape": "0463ba8d341671fadd6707a590359d523b75246253a4f2f85600e72beecbc1dc",
        "views": [True, True, True, False]},
}
BASE64_RUN = re.compile(r'"[A-Za-z0-9+/=]{200,}"')      # a long base64 string, as inline geometry was


def _hash(shape):
    return Encoder(meshes=False).brep(shape)[1]


def _asm_hash(a):
    rows = [[i["id"], _hash(a.parts[i["id"]].shape), [[round(x, 9) for x in r] for r in i["T"]]]
            for i in a.instances if i["id"] in a.parts]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _box(x, y, z, at=(0, 0, 0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*at), float(x), float(y), float(z)).Shape()


def _step_bytes(tmp_path, name, shapes):
    p = str(tmp_path / name)
    K.export_step(shapes, p)
    with open(p, "rb") as fh:
        return fh.read()


def _read(path):
    with open(path) as fh:
        return json.load(fh)


def _blob_files(folder):
    d = os.path.join(folder, "blobs")
    return set(os.listdir(d)) if os.path.isdir(d) else set()


# =========================================================================== shipped Assemblies and Drawings

@pytest.mark.parametrize("rel", sorted(BEFORE))
def test_example_assembly_loads_to_the_same_geometry_and_saves_without_base64(rel, tmp_path):
    want = BEFORE[rel]
    src = os.path.join(REPO, rel)
    assert os.path.getsize(src) == want["size"]                  # still the old file
    assert _read(src)["version"] < M.latest_version("assembly")
    old = A.AssemblyDoc.load(src)                                # no store, no resolver: the copies it carries
    assert old.parts and _asm_hash(old) == want["fallback"]
    assert not _blob_files(os.path.dirname(src))                 # a load writes nothing
    linked = A.AssemblyDoc.load(src, resolver=L.LinkResolver().resolve_source)
    assert not linked.errors and _asm_hash(linked) == want["resolved"]

    # a loose copy next to no blob folder: saved in place, it writes its blobs beside itself
    loose = str(tmp_path / os.path.basename(rel))
    shutil.copy(src, loose)
    a = A.AssemblyDoc.load(loose)
    a.save()
    text = open(loose).read()
    data = json.loads(text)
    assert data["version"] == M.latest_version("assembly") and "blobs" not in data
    assert '"_brep"' not in text and not BASE64_RUN.search(text)
    assert os.path.getsize(loose) < want["size"]
    keys = {i["_brep_blob"] for i in data["instances"]}
    assert keys and keys <= _blob_files(str(tmp_path))
    for k in keys:                                               # the same bytes the base64 held
        assert hashlib.sha256(open(tmp_path / "blobs" / k, "rb").read()).hexdigest() == k
    back = A.AssemblyDoc.load(loose)
    assert _asm_hash(back) == want["fallback"]
    assert back.to_data() == a.to_data()


def test_a_resolved_example_saves_copies_of_its_live_parts(tmp_path):
    rel = "examples/hinge.fasm"
    for name in (rel, BEFORE[rel]["parts"]):
        shutil.copy(os.path.join(REPO, name), tmp_path / os.path.basename(name))
    loose = str(tmp_path / "hinge.fasm")
    a = A.AssemblyDoc.load(loose, resolver=L.LinkResolver().resolve_source)
    assert not a.errors and _asm_hash(a) == BEFORE[rel]["resolved"]
    a.save()
    os.remove(tmp_path / "hinge_parts.fshape")                   # the studio is gone: the saved copies show
    back = A.AssemblyDoc.load(loose, resolver=L.LinkResolver().resolve_source)
    assert set(back.errors) == {i["id"] for i in back.instances}
    assert all("showing the copy saved" in m for m in back.errors.values())
    # a copy read back from its BREP bytes, as the old file's base64 copy of the same parts read back
    assert _asm_hash(back) == BEFORE[rel]["fallback"]


@pytest.mark.parametrize("rel", sorted(BEFORE_DRAWINGS))
def test_shipped_drawing_loads_to_the_same_geometry_and_saves_without_base64(rel, tmp_path):
    want = BEFORE_DRAWINGS[rel]
    src = os.path.join(REPO, rel)
    assert os.path.getsize(src) == want["size"]
    g = DW.DrawingDoc.load(src)
    assert g.shape is not None and _hash(g.shape) == want["shape"]
    assert [K.hlr_cached(g.shape, v["d"], v["up"]) is not None for v in g.views] == want["views"]
    loose = str(tmp_path / os.path.basename(rel))
    shutil.copy(src, loose)
    g = DW.DrawingDoc.load(loose)
    g.save()
    text = open(loose).read()
    data = json.loads(text)
    assert data["version"] == M.latest_version("drawing")
    assert "_brep" not in data and "_views" not in data and not BASE64_RUN.search(text)
    assert os.path.getsize(loose) < want["size"]
    assert {data["_brep_blob"], data["_views_blob"]} <= _blob_files(str(tmp_path))
    back = DW.DrawingDoc.load(loose)
    assert _hash(back.shape) == want["shape"]
    assert [K.hlr_cached(back.shape, v["d"], v["up"]) is not None for v in back.views] == want["views"]


# =========================================================================== the previous loader refuses new files

def _saved(tmp_path, kind):
    if kind == "assembly":
        a = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"))
        a.save(str(tmp_path / "a.fasm"))
        return _read(tmp_path / "a.fasm")
    if kind == "drawing":
        g = DW.DrawingDoc.load(os.path.join(TESTS, "data", "versions", "v1.fdwg"))
        g.save(str(tmp_path / "d.fdwg"))
        return _read(tmp_path / "d.fdwg")
    s = PartStudioDoc.load(os.path.join(EXAMPLES_DIR, "bracket.fshape"))
    s.save(str(tmp_path / "s.fshape"))
    return _read(tmp_path / "s.fshape")


@pytest.mark.parametrize("fmt, kind, step", [("assembly", "assembly", "_move_geometry_to_blobs"),
                                             ("drawing", "drawing", "_move_geometry_to_blobs"),
                                             ("document", "studio", "_move_import_data_to_blobs")])
def test_a_file_written_now_is_refused_by_the_previous_loader(fmt, kind, step, tmp_path, monkeypatch):
    data = _saved(tmp_path, kind)
    latest = M.latest_version(fmt)
    names = [s.__name__ for s in M._CHAINS[fmt]]
    assert data["version"] == latest and step in names
    # the build before this card: the chain up to, and without, the step that moved the data to blobs (later
    # steps, such as the upstream 2.0 merge's, did not exist either)
    cut = names.index(step)
    monkeypatch.setitem(M._CHAINS, fmt, M._CHAINS[fmt][:cut])
    with pytest.raises(K.KernelError, match=f"saved with file version {latest}, but this build of FreeShape only "
                                            f"supports up to version {cut + 1}. Update FreeShape to open it."):
        M.migrate(fmt, data, "x")


# =========================================================================== STEP data, inline in old files

def _old_step_assembly(tmp_path, raw):
    """An Assembly file as version 7 wrote it: the STEP file base64 in `blobs`, keyed by the sha1 of the text."""
    text = base64.b64encode(raw).decode()
    key = hashlib.sha1(text.encode()).hexdigest()[:16]
    insts = [{"id": f"I{i + 1}", "name": f"Part <{i + 1}>", "source": {"kind": "step", "blob": key, "index": i,
                                                                      "filename": "two.step"},
              "T": np.eye(4).tolist(), "fixed": i == 0, "hidden": False} for i in range(2)]
    d = {"format": A.FILE_FORMAT, "version": 7, "name": "Old", "uid": "uid-old", "instances": insts, "mates": [],
         "groups": [], "relations": [], "blobs": {key: text}}
    path = str(tmp_path / "old" / "old.fasm")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as fh:
        json.dump(d, fh)
    return path


def test_a_loose_assembly_with_inline_step_data_loads_and_saves_its_blobs_beside_itself(tmp_path):
    raw = _step_bytes(tmp_path, "two.step", [_box(10, 10, 10), _box(5, 5, 5, (20, 0, 0))])
    path = _old_step_assembly(tmp_path, raw)
    folder = os.path.dirname(path)
    a = A.AssemblyDoc.load(path)
    assert len(a.parts) == 2 and not a.errors
    assert not _blob_files(folder)                     # the bytes are held in memory until a save
    want = _asm_hash(a)
    a.save()
    key = hashlib.sha256(raw).hexdigest()
    data = _read(path)
    assert "blobs" not in data and {i["source"]["blob"] for i in data["instances"]} == {key}
    assert key in _blob_files(folder)
    back = A.AssemblyDoc.load(path)
    assert len(back.parts) == 2 and not back.errors and _asm_hash(back) == want
    # saved elsewhere, the STEP file goes along
    other = str(tmp_path / "elsewhere" / "copy.fasm")
    os.makedirs(os.path.dirname(other))
    back.save(other)
    assert key in _blob_files(os.path.dirname(other))
    # a missing blob does not stop the load: the instances are red, naming the blob
    os.remove(os.path.join(folder, "blobs", key))
    gone = A.AssemblyDoc.load(path)
    assert set(gone.errors) == {"I1", "I2"} and all(key in m for m in gone.errors.values())
    assert gone.parts == {}


def test_a_missing_part_copy_is_named_and_a_missing_drawing_copy_too(tmp_path):
    loose = str(tmp_path / "hinge.fasm")
    shutil.copy(os.path.join(EXAMPLES_DIR, "hinge.fasm"), loose)
    A.AssemblyDoc.load(loose).save()
    data = _read(loose)
    lost = data["instances"][0]["_brep_blob"]
    os.remove(tmp_path / "blobs" / lost)
    a = A.AssemblyDoc.load(loose)
    iid = data["instances"][0]["id"]
    assert iid not in a.parts and lost in a.errors[iid] and len(a.parts) == len(data["instances"]) - 1
    g = DW.DrawingDoc.load(os.path.join(TESTS, "data", "versions", "v1.fdwg"))
    g.save(str(tmp_path / "d.fdwg"))
    os.remove(tmp_path / "blobs" / _read(tmp_path / "d.fdwg")["_brep_blob"])
    back = DW.DrawingDoc.load(str(tmp_path / "d.fdwg"))
    assert back.shape is None and _read(tmp_path / "d.fdwg")["_brep_blob"] in back.error


def test_a_part_studio_import_moves_its_step_file_to_the_blobs(tmp_path):
    raw = _step_bytes(tmp_path, "cyl.step", [BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),
                                                                      4.0, 9.0).Shape()])
    s = PartStudioDoc()
    f = s.make_feature("import")
    f["params"] = {"data": base64.b64encode(raw).decode(), "filename": "cyl.step"}
    s.add_feature(f)
    assert not s.errors and s.state.parts
    want = {p: _hash(part.shape) for p, part in s.state.parts.items()}
    # an old file: version 2, the STEP file inline
    old = json.loads(s.to_json())
    old["version"] = 2
    old["features"][0]["params"] = {"data": base64.b64encode(raw).decode(), "filename": "cyl.step"}
    path = str(tmp_path / "old.fshape")
    with open(path, "w") as fh:
        json.dump(old, fh)
    back = PartStudioDoc.load(path)
    assert not back.errors and {p: _hash(x.shape) for p, x in back.state.parts.items()} == want
    assert not _blob_files(str(tmp_path))
    back.save()
    key = hashlib.sha256(raw).hexdigest()
    saved = _read(path)
    assert saved["features"][0]["params"] == {"blob": key, "filename": "cyl.step"}
    assert key in _blob_files(str(tmp_path)) and not BASE64_RUN.search(open(path).read())
    again = PartStudioDoc.load(path)
    assert not again.errors and {p: _hash(x.shape) for p, x in again.state.parts.items()} == want
    # the in-memory feature made by the Import command saves the same way
    os.makedirs(tmp_path / "new")
    s.save(str(tmp_path / "new" / "s.fshape"))
    assert _read(tmp_path / "new" / "s.fshape")["features"][0]["params"]["blob"] == key
    assert key in _blob_files(str(tmp_path / "new"))
    os.remove(tmp_path / "blobs" / key)
    gone = PartStudioDoc.load(path)
    fid = saved["features"][0]["id"]
    assert key in gone.errors[fid] and not gone.state.parts


# =========================================================================== MemoryStore

def test_on_a_memory_store_the_same_data_lands_in_the_store_blobs(tmp_path):
    raw = _step_bytes(tmp_path, "two.step", [_box(10, 10, 10), _box(5, 5, 5, (20, 0, 0))])
    old_step = _old_step_assembly(tmp_path, raw)
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store)
    hinge = A.AssemblyDoc.load(os.path.join(EXAMPLES_DIR, "hinge.fasm"))
    step = A.AssemblyDoc.load(old_step)
    dwg = DW.DrawingDoc.load(os.path.join(TESTS, "data", "versions", "v1.fdwg"))
    tabs = {k: fd.adopt_tab(d, kind) for k, d, kind in (("hinge", hinge, "assembly"), ("step", step, "assembly"),
                                                         ("dwg", dwg, "drawing"))}
    want = {"hinge": _asm_hash(hinge), "step": _asm_hash(step), "dwg": _hash(dwg.shape)}
    for t in tabs.values():
        t.doc.path = None
        t.doc.dirty = True
    fd.save()
    for name, t in tabs.items():
        data = json.loads(store.read_tab(t.id))
        assert not BASE64_RUN.search(store.read_tab(t.id).decode())
        keys = ({i.get("_brep_blob") for i in data["instances"]} | {i["source"].get("blob") for i in data["instances"]}
                if name != "dwg" else {data["_brep_blob"], data["_views_blob"]}) - {None}
        assert keys and all(store.has_blob(k, t.id) for k in keys), name
    assert store.has_blob(hashlib.sha256(raw).hexdigest(), tabs["step"].id)
    back = DF.FolderDocument.open_on(store)
    assert not back.errors
    assert _asm_hash(back.tab(tabs["hinge"].id).doc) == want["hinge"]
    assert _asm_hash(back.tab(tabs["step"].id).doc) == want["step"]
    assert _hash(back.tab(tabs["dwg"].id).doc.shape) == want["dwg"]


# =========================================================================== git: Versions, Workspaces, merge, import

@pytest.fixture
def registry(tmp_path):
    return R.DocRegistry(str(tmp_path / "app" / "documents.json"))


def _import_into_studio(doc, raw, name):
    f = doc.make_feature("import")
    f["params"] = {"data": base64.b64encode(raw).decode(), "filename": name}
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return f["id"]


def _studio_hashes(doc):
    return sorted(_hash(p.shape) for p in doc.state.parts.values())


@needs_git
def test_a_version_shows_an_imported_step_part_after_an_edit(tmp_path, registry):
    raw = _step_bytes(tmp_path, "box.step", [_box(10, 20, 30)])
    raw2 = _step_bytes(tmp_path, "two.step", [_box(4, 4, 4), _box(6, 6, 6, (10, 0, 0))])
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=registry)
    st = doc.add_tab("studio", "Imported")
    fid = _import_into_studio(st.doc, raw, "box.step")
    asm = doc.add_tab("assembly", "Asm")
    key = asm.doc.add_step_blob(base64.b64encode(raw2).decode())
    for i in range(2):
        asm.doc.insert({"kind": "step", "blob": key, "index": i, "filename": "two.step"}, T=np.eye(4))
    want_st, want_asm = _studio_hashes(st.doc), _asm_hash(asm.doc)
    doc.save()
    assert {hashlib.sha256(raw).hexdigest(), key} <= _blob_files(doc.path)
    doc.create_version("v1")
    st.doc.delete_features({fid})                      # the edit: the import is gone from the Workspace
    asm.doc.delete_instances({asm.doc.instances[0]["id"]})
    doc.save()
    view = doc.open_version("v1")
    try:
        assert view.read_only and not view.errors
        vst, vasm = view.tab(st.id).doc, view.tab(asm.id).doc
        assert not vst.errors and _studio_hashes(vst) == want_st
        assert not vasm.errors and _asm_hash(vasm) == want_asm
    finally:
        view.close()


@needs_git
def test_a_merge_of_two_workspaces_that_each_imported_a_step_resolves_both(tmp_path, registry):
    raw_a = _step_bytes(tmp_path, "a.step", [_box(10, 10, 10)])
    raw_b = _step_bytes(tmp_path, "b.step", [_box(3, 7, 11)])
    doc = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=registry)
    sa, sb = doc.add_tab("studio", "A"), doc.add_tab("assembly", "B")
    doc.save()
    doc.create_version("v1")
    doc.create_workspace("other", "v1")             # a switch reloads every tab: look them up again
    asm = doc.tab(sb.id).doc
    key_b = asm.add_step_blob(raw_b)
    asm.insert({"kind": "step", "blob": key_b, "index": 0, "filename": "b.step"}, T=np.eye(4))
    want_b = _asm_hash(asm)
    doc.save()
    doc.switch_workspace("main")
    _import_into_studio(doc.tab(sa.id).doc, raw_a, "a.step")
    want_a = _studio_hashes(doc.tab(sa.id).doc)
    doc.save()
    assert hashlib.sha256(raw_b).hexdigest() not in _blob_files(doc.path)     # theirs only, so far
    assert doc.merge_workspace("other")
    assert {hashlib.sha256(raw_a).hexdigest(), key_b} <= _blob_files(doc.path)
    merged = DF.FolderDocument.open(doc.path, registry=registry)
    assert not merged.errors and not merged.warnings
    assert not merged.tab(sa.id).doc.errors and _studio_hashes(merged.tab(sa.id).doc) == want_a
    assert not merged.tab(sb.id).doc.errors and _asm_hash(merged.tab(sb.id).doc) == want_b


@needs_git
def test_safe_import_of_a_received_document_carries_its_blobs(tmp_path, registry):
    raw = _step_bytes(tmp_path, "box.step", [_box(8, 9, 10)])
    sender = DF.FolderDocument.create(str(tmp_path / "Sent"), registry=R.DocRegistry(str(tmp_path / "s.json")))
    st = sender.add_tab("studio", "Imported")
    _import_into_studio(st.doc, raw, "box.step")
    want = _studio_hashes(st.doc)
    sender.save()
    sender.create_version("v1")
    got = DF.FolderDocument.open_document(sender.path, registry=registry,
                                          managed_root=str(tmp_path / "app" / "documents"))
    assert os.path.realpath(got.path) != os.path.realpath(sender.path)          # a clone, not the received folder
    assert hashlib.sha256(raw).hexdigest() in _blob_files(got.path)
    assert not got.errors and _studio_hashes(got.tab(st.id).doc) == want
