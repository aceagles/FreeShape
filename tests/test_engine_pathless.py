"""I5.2: an engine that needs no file paths. A tab is opened from its bytes (`open_tab_data`) with a blob lookup
instead of a folder, and saved without the engine writing a file (`tab_data`); `open_tab(path)` is a wrapper
over the same code. Every test runs on both executors.

"File opens refused" is real in both processes: the parent installs `worker.refuse_tab_file_opens` itself, and a
worker installs it at start-up when FREESHAPE_TEST_REFUSE_TAB_OPENS is set (the workers here are spawned after
it is, with no warm spare). It refuses opening a tab file, `document.json` or a blob file, and records each try."""
import json
import os
import shutil

import numpy as np
import pytest
from conftest import TOP, add, rect_lines, region_refs, sketch_feature

from freeshape import doc_folder as DF
from freeshape import docstore as DS
from freeshape import kernel as K
from freeshape import links as L
from freeshape import pyfeature
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine import worker as W

LABELLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins", "labelled")


@pytest.fixture(autouse=True)
def _no_plugins_no_spares(tmp_path, monkeypatch):
    """An empty user plugin folder, in this process and in every worker spawned from here; no warm spare
    started before the environment was set."""
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    before = {p.id for p in pyfeature.plugins()}
    P.SPARES.clear()
    yield user
    P.SPARES.clear()
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)


@pytest.fixture(params=["inprocess", "process"])
def make_engine(request):
    made = []

    def make():
        e = InProcessEngine() if request.param == "inprocess" else ProcessEngine(spares=0)
        made.append(e)
        return e

    make.process = request.param == "process"
    yield make
    for e in made:
        e.close()


@pytest.fixture
def refuse_opens(monkeypatch):
    """Refuse tab-file, manifest and blob opens here and in every worker started from now on."""
    monkeypatch.setenv("FREESHAPE_TEST_REFUSE_TAB_OPENS", "1")
    undo = W.refuse_tab_file_opens()
    yield
    undo()


def _volumes(out):
    return sorted(round(K.volume(K.shape_from_brep(p.brep)), 6) for p in out.parts)


def _asm_volumes(out):
    return sorted(round(K.volume(K.shape_from_brep(p.brep)), 6) for p in out.parts.values())


def _try_open(path):
    try:
        open(path, "rb").close()
    except PermissionError:
        return "refused"
    except OSError:
        return "opened"
    return "opened"


# =========================================================================== a Document on a MemoryStore

def _memory_document():
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store, name="pathless")
    var = fd.add_tab("variables", "Vars")
    var.doc.add_row("w", "20 mm")
    st = fd.add_tab("studio", "Studio")
    add(st.doc, "variable_studio_ref", source={"kind": "variables", "tab": var.id, "name": var.name})
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth="#w", op="new")
    assert not st.doc.errors, st.doc.errors
    pid = next(iter(st.doc.state.parts))
    asm = fd.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, pid), name="Plate")
    asm.doc.refresh(solve=False, notify=False)
    assert not asm.doc.errors, asm.doc.errors
    fd.save()
    return store, fd, {"variables": var, "studio": st, "assembly": asm}


def _open_from_store(engine, store, fd, tabs):
    engine.use_document(fd.id)
    for kind in ("variables", "studio", "assembly"):
        t = tabs[kind]
        assert engine.open_tab_data(t.kind, store.read_tab(t.id), t.id, t.name, blobs=store.blobs(t.id)) == t.id
    return {k: t.id for k, t in tabs.items()}


def test_a_document_from_a_memory_store_opens_and_rebuilds_with_every_file_open_refused(make_engine, refuse_opens):
    store, fd, tabs = _memory_document()
    engine = make_engine()
    ids = _open_from_store(engine, store, fd, tabs)
    for tab in ids.values():
        out = engine.outputs(tab)
        assert not out.errors, (tab, out.errors)
    assert _volumes(engine.outputs(ids["studio"])) == [2000.0]
    assert _asm_volumes(engine.outputs(ids["assembly"])) == [2000.0]
    assert {(ids["studio"], ids["variables"]), (ids["assembly"], ids["studio"])} <= set(engine.link_edges())
    assert [t[3] for t in engine.tabs()] == [None, None, None]          # no tab has a path

    before = engine.rebuild_counts()
    engine.submit_edit(ids["variables"], {"op": "set_row", "index": 0, "expression": "30 mm"})
    after = engine.rebuild_counts()
    assert {k: after.get(t, 0) - before.get(t, 0) for k, t in ids.items()} == \
        {"variables": 0, "studio": 1, "assembly": 1}
    assert _volumes(engine.outputs(ids["studio"])) == [3000.0]
    asm = engine.outputs(ids["assembly"])
    assert _asm_volumes(asm) == [3000.0] and not asm.errors
    assert set(asm.parts) == {p.hash for p in engine.outputs(ids["studio"]).parts}

    # saved without the engine writing a file: the store takes the bytes and they reopen to the same result
    for kind in ("variables", "studio", "assembly"):
        store.write_tab(ids[kind], engine.tab_data(ids[kind]))
    again = make_engine()
    ids2 = _open_from_store(again, store, fd, tabs)
    assert _volumes(again.outputs(ids2["studio"])) == [3000.0]
    assert set(again.outputs(ids2["assembly"]).parts) == set(asm.parts)

    # the guard is live in both processes, and nothing it covers was opened
    assert _try_open(os.path.join(os.sep, "nowhere", "x.fshape")) == "refused"
    refused = [p for p in W.refused_opens() if not p.endswith(os.path.join("nowhere", "x.fshape"))]
    assert refused == []
    if make_engine.process:
        for e in (engine, again):
            for tab in ids.values():
                assert e.run_in_worker(tab, W.refused_opens) == []
        assert engine.run_in_worker(ids["studio"], _try_open, os.path.join(os.sep, "nowhere", "x.fshape")) == "refused"


def test_a_source_naming_a_tab_id_never_falls_back_to_its_path(make_engine, tmp_path):
    """The Assembly's source names the studio by tab id and also carries a path to a real studio file. With the
    studio not open, a tab opened by data must not open that file to satisfy the link."""
    store, fd, tabs = _memory_document()
    elsewhere = str(tmp_path / "elsewhere.fshape")
    tabs["studio"].doc.save(elsewhere)
    data = json.loads(store.read_tab(tabs["assembly"].id))
    for inst in data["instances"]:
        inst["source"]["path"] = elsewhere
    engine = make_engine()
    engine.use_document(fd.id)
    asm = engine.open_tab_data("assembly", json.dumps(data).encode(), tabs["assembly"].id, "Asm")
    assert [t[0] for t in engine.tabs()] == [asm]                        # nothing opened from `elsewhere`
    assert engine.outputs(asm).errors


# =========================================================================== Python feature stored builds

def _labelled_box(doc):
    f = doc.make_feature("py:labelled/box")
    doc.add_feature(f)
    assert doc.errors == {}, doc.errors
    return f


def _stored_build_studio():
    """A MemoryStore Document whose studio holds a labelled box with its build stored in the store's blobs; the
    plugin is then unregistered here (and is in no worker's plugin folder)."""
    assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store, name="pystore")
    st = fd.add_tab("studio", "Py")
    box = _labelled_box(st.doc)
    want = _volumes_doc(st.doc)
    fd.save()
    assert json.loads(store.read_tab(st.id))["python_builds"]
    pyfeature.unregister_plugin("labelled")
    return store, fd, st, box, want


def _volumes_doc(doc):
    return sorted(round(K.volume(p.shape), 6) for p in doc.state.parts.values())


def test_a_missing_plugin_shows_its_stored_build_when_opened_by_data(make_engine, refuse_opens):
    store, fd, st, box, want = _stored_build_studio()
    engine = make_engine()
    tab = engine.open_tab_data("studio", store.read_tab(st.id), st.id, st.name, blobs=store.blobs(st.id))
    out = engine.outputs(tab)
    assert out.errors == {} and _volumes(out) == want
    assert [p.id for p in out.parts] == [f"{box['id']}/1"]


def test_a_missing_plugin_shows_its_stored_build_when_the_file_changed_since_open(make_engine, tmp_path):
    """The old temporary-copy case: the file on disk no longer holds the bytes the parent opened, so a worker
    started now (a restart) must not look for the stored build anywhere but beside the tab's own file."""
    assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
    path = str(tmp_path / "doc" / "py.fshape")
    os.makedirs(os.path.dirname(path))
    from freeshape.document import PartStudioDoc
    doc = PartStudioDoc()
    box = _labelled_box(doc)
    want = _volumes_doc(doc)
    doc.save(path)
    pyfeature.unregister_plugin("labelled")
    engine = make_engine()
    tab = engine.open_tab(path)
    assert engine.outputs(tab).errors == {}
    with open(path) as fh:
        d = json.load(fh)
    d["name"] = "Changed on disk"
    with open(path, "w") as fh:
        json.dump(d, fh)
    if make_engine.process:
        out = engine.restart(tab)
    else:
        engine.close_tab(tab)
        out = engine.outputs(engine.open_tab(path))
    assert out.errors == {}, out.errors
    assert _volumes(out) == want and [p.id for p in out.parts] == [f"{box['id']}/1"]


def test_a_build_the_worker_stores_comes_back_to_the_callers_store(make_engine, _no_plugins_no_spares):
    """The plugin is installed while the studio is built and saved: the stored build is written by the tab's
    process, and `tab_data` puts its blobs into the caller's lookup. Reopened with the plugin gone, the
    studio shows that build."""
    shutil.copytree(LABELLED, str(_no_plugins_no_spares / "labelled"))
    assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
    store = DS.MemoryStore()
    engine = make_engine()
    tab = engine.new_tab("studio", "Py")
    engine.submit_edit(tab, {"op": "add_feature", "feature": {"id": "F1", "type": "py:labelled/box",
                                                                 "name": "Box", "params": {}}})
    built = engine.outputs(tab)
    assert built.errors == {}
    data = engine.tab_data(tab, blobs=store.blobs("S1"))
    builds = json.loads(data)["python_builds"]
    keys = {k for rec in builds["F1"]["parts"] for k in (rec["brep"], rec["names"])}
    assert keys and all(store.has_blob(k, "S1") for k in keys)

    pyfeature.unregister_plugin("labelled")
    shutil.rmtree(str(_no_plugins_no_spares / "labelled"))
    other = make_engine()
    again = other.open_tab_data("studio", data, "S1", "Py", blobs=store.blobs("S1"))
    out = other.outputs(again)
    assert out.errors == {} and _volumes(out) == _volumes(built)


# =========================================================================== tab_data and save(path)

def test_tab_data_equals_what_save_writes(make_engine, tmp_path):
    assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"))
    var = fd.add_tab("variables", "Vars")
    var.doc.add_row("w", "20 mm")
    st = fd.add_tab("studio", "Studio")
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth=4, op="new")
    _labelled_box(st.doc)
    pid = next(iter(st.doc.state.parts))
    asm = fd.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, pid), name="Plate")
    dwg = fd.add_tab("drawing", "Dwg")
    dwg.doc.source = L.assembly_source(asm)
    fd.save()
    pyfeature.unregister_plugin("labelled")
    engine = make_engine()
    engine.use_document(fd.id)
    for t in (var, st, asm, dwg):
        path = os.path.join(fd.path, t.file)
        tab = engine.open_tab(path, tab=t.id)
        sink = DS.MemoryStore()
        data = engine.tab_data(tab, blobs=sink.blobs())
        assert isinstance(data, bytes)
        engine.submit_edit(tab, {"op": "save", "path": path})
        with open(path, "rb") as fh:
            assert fh.read() == data, t.kind
        assert engine.tab_data(tab) == data
        if t.kind == "studio":
            keys = {k for rec in json.loads(data)["python_builds"].values() for p in rec["parts"]
                    for k in (p["brep"], p["names"])}
            assert keys and all(sink.has_blob(k) for k in keys)


# =========================================================================== Assemblies and Drawings: their blobs

def _step_document(tmp_path):
    """A MemoryStore Document: a studio with a box, an Assembly of that box and of the two solids of an imported
    STEP file, and a Drawing of the Assembly, saved by FolderDocument.save."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    p = str(tmp_path / "two.step")
    K.export_step([BRepPrimAPI_MakeBox(gp_Pnt(0, 40, 0), 10.0, 10.0, 10.0).Shape(),
                   BRepPrimAPI_MakeBox(gp_Pnt(20, 40, 0), 5.0, 5.0, 5.0).Shape()], p)
    with open(p, "rb") as fh:
        raw = fh.read()
    store = DS.MemoryStore()
    fd = DF.FolderDocument.create_on(store, name="step")
    st = fd.add_tab("studio", "Studio")
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 10, 10), TOP)
    ext = add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth=20, op="new")["id"]
    asm = fd.add_tab("assembly", "Asm")
    asm.doc.insert(L.studio_source(st, next(iter(st.doc.state.parts))), name="Plate")
    key = asm.doc.add_step_blob(raw)
    for i in range(2):
        asm.doc.insert({"kind": "step", "blob": key, "index": i, "filename": "two.step"}, T=np.eye(4))
    asm.doc.refresh(solve=False, notify=False)
    assert len(asm.doc.parts) == 3 and not asm.doc.errors, asm.doc.errors
    dwg = fd.add_tab("drawing", "Dwg")
    dwg.doc.source = L.assembly_source(asm)
    dwg.doc.refresh(notify=False)
    assert dwg.doc.shape is not None
    fd.save()
    return store, fd, {"studio": st, "assembly": asm, "drawing": dwg}, key, ext


def _all_blobs(store):
    return {(t, k) for t, held in store._blobs.items() for k in held}


def test_an_unchanged_assembly_and_drawing_save_with_the_blob_keys_folderdocument_wrote(make_engine, refuse_opens,
                                                                                        tmp_path):
    """The engine's part shapes are parsed from the BREP its studio sent, another text for the same shape than
    FolderDocument.save wrote; an unchanged Assembly or Drawing keeps the copy it was loaded with, so its bytes and
    blob keys are the same, and saving again adds no blob. The STEP file and the copies went to the worker by
    data (every blob-folder open is refused)."""
    store, fd, tabs, key, _ext = _step_document(tmp_path)
    refused = len(W.refused_opens())                     # what earlier tests in this process probed
    saved = {k: store.read_tab(t.id) for k, t in tabs.items()}
    blobs = _all_blobs(store)
    assert (tabs["assembly"].id, key) in blobs
    assert sum(1 for i in json.loads(saved["assembly"])["instances"] if i.get("_brep_blob")) == 1
    assert json.loads(saved["drawing"]).get("_brep_blob")
    engine = make_engine()
    engine.use_document(fd.id)
    for kind in ("studio", "assembly", "drawing"):
        t = tabs[kind]
        engine.open_tab_data(t.kind, store.read_tab(t.id), t.id, t.name, blobs=store.blobs(t.id))
    for t in tabs.values():
        assert not engine.outputs(t.id).errors, t.kind
    assert len(engine.outputs(tabs["assembly"].id).instances) == 3
    for _ in range(2):
        for kind, t in tabs.items():
            d = json.loads(engine.tab_data(t.id))
            d["_tab_id"] = t.id
            assert json.dumps(d, indent=1).encode("utf-8") == saved[kind], kind
        assert _all_blobs(store) == blobs
    assert W.refused_opens()[refused:] == []


def test_an_assembly_with_an_imported_step_instance_is_edited_saved_and_reopened_by_a_session(refuse_opens,
                                                                                             tmp_path):
    from freeshape.session import DocumentSession
    store, fd, tabs, key, ext = _step_document(tmp_path)
    ids = {k: t.id for k, t in tabs.items()}
    with DocumentSession(store, spares=0) as s:
        assert s.errors() == {}
        assert s.submit_edit(ids["studio"], {"op": "set_param", "feature": ext, "key": "depth", "value": 5}) == \
            [ids["studio"], ids["assembly"], ids["drawing"]]
        asm = s.outputs(ids["assembly"])
        want = (sorted(asm.parts), _asm_volumes(asm), s.outputs(ids["drawing"]).model)
        assert want[1] == [125.0, 500.0, 1000.0]
        assert s.save() == list(ids.values())
    saved = json.loads(store.read_tab(ids["assembly"]))
    copy = next(i["_brep_blob"] for i in saved["instances"] if i["source"]["kind"] == "studio")
    assert store.has_blob(copy, ids["assembly"]) and store.has_blob(key, ids["assembly"])
    with DocumentSession(store, spares=0) as again:
        assert again.errors() == {}
        asm = again.outputs(ids["assembly"])
        assert (sorted(asm.parts), _asm_volumes(asm), again.outputs(ids["drawing"]).model) == want
        for tid in ids.values():
            assert again._eng.run_in_worker(tid, W.refused_opens) == []
