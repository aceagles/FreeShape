"""I5.3: a headless Document session (freeshape/session.py). One Document is opened from a store, every tab goes to
a ProcessEngine by data under its manifest tab id, edits stay in the engine until `save()`, and nothing imports Qt.

Every worker process is closed by a fixture finaliser, so none survives a failing assertion. The acceptance scenario
runs on a FolderStore Document and on a MemoryStore Document; on the memory one tab-file opens are refused in this
process and in every worker (see test_engine_pathless.py), which proves no tab file is read from disk."""
import json
import os
import subprocess
import sys
import textwrap

import pytest
from conftest import TOP, add, rect_lines, region_refs, sketch_feature

from freeshape import doc_folder as DF
from freeshape import docstore as DS
from freeshape import kernel as K
from freeshape import links as L
from freeshape import pyfeature
from freeshape.engine import DrawingOutputs, StudioOutputs, VariablesOutputs
from freeshape.engine import process as P
from freeshape.engine import worker as W
from freeshape.session import DocumentSession, SessionError

LABELLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins", "labelled")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_plugins_no_spares(tmp_path, monkeypatch):
    """An empty user plugin folder in this process and every worker; no warm spare started before the
    environment was set."""
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


@pytest.fixture
def sessions(request):
    """Open sessions through `sessions(store)`; every one is closed when the test ends, pass or fail."""
    made = []

    def make(store, **kw):
        s = DocumentSession(store, spares=0, **kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()
    P.SPARES.clear()


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _children():
    """Pids of the live (not zombie) child processes of this process."""
    me, out = os.getpid(), set()
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                with open(f"/proc/{p}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(rest[1]) == me and rest[0] != "Z":
                out.add(int(p))
    return out


def _volumes(out):
    parts = out.parts.values() if isinstance(out.parts, dict) else out.parts
    return sorted(round(K.volume(K.shape_from_brep(p.brep)), 6) for p in parts)


def _box(studio, w, h, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, w, h), TOP)
    f = add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors
    return f["id"]


# =========================================================================== the Document under test

def _layout_document(store):
    """Variable Studio w = 20 mm; studios A (10 x 10 x #w) and B (20 x 10 x #w); an Assembly of both."""
    fd = DF.FolderDocument.create_on(store, name="session")
    var = fd.add_tab("variables", "Vars")
    var.doc.add_row("w", "20 mm")
    ids = {"vars": var.id}
    ext = {}
    for key, name, w in (("a", "Plate A", 10), ("b", "Plate B", 20)):
        st = fd.add_tab("studio", name)
        add(st.doc, "variable_studio_ref", source={"kind": "variables", "tab": var.id, "name": var.name})
        ext[key] = _box(st.doc, w, 10, "#w")
        ids[key] = st.id
    asm = fd.add_tab("assembly", "Asm")
    for key in ("a", "b"):
        st = fd.tab(ids[key])
        asm.doc.insert(L.studio_source(st, next(iter(st.doc.state.parts))), name=st.name)
    asm.doc.refresh(solve=False, notify=False)
    assert not asm.doc.errors, asm.doc.errors
    ids["asm"] = asm.id
    fd.save()
    return fd, ids, ext


@pytest.fixture(params=["folder", "memory"])
def layout(request, tmp_path, monkeypatch):
    """(store, ids, extrude feature ids, kind) of the layout Document on a FolderStore or a MemoryStore."""
    if request.param == "memory":
        monkeypatch.setenv("FREESHAPE_TEST_REFUSE_TAB_OPENS", "1")
        undo = W.refuse_tab_file_opens()
        request.addfinalizer(undo)
        store = DS.MemoryStore()
    else:
        store = DS.FolderStore(str(tmp_path / "Doc"))
    fd, ids, ext = _layout_document(store)
    return store, ids, ext, request.param


def _strip_brep(x):
    if isinstance(x, dict):
        return {k: _strip_brep(v) for k, v in x.items() if k != "_brep"}
    if isinstance(x, list):
        return [_strip_brep(v) for v in x]
    return x


def _same_as_saved(now, before, kinds):
    """`now` equals `before` byte for byte, except that an Assembly or Drawing carries the BREP of its parts as
    the engine serialised it (`_brep`: another text for the same shape, which I4.9 moves to blobs): those tabs
    are equal once that cache is set aside."""
    for tid, data in now.items():
        if kinds[tid] in ("assembly", "drawing"):
            assert _strip_brep(json.loads(data)) == _strip_brep(json.loads(before[tid])), tid
            assert data.decode().replace(" ", "").count("_brep") == before[tid].decode().replace(" ", "").count("_brep")
        else:
            assert data == before[tid], tid


def _saved(store, ids):
    return {t: store.read_tab(t) for t in ids.values()}


def test_acceptance_scenario_on_both_stores(layout, sessions):
    store, ids, ext, kind = layout
    original = _saved(store, ids)
    s = sessions(store)

    tabs = s.tabs()
    assert [(t["id"], t["kind"], t["name"], t["state"]) for t in tabs] == [
        (ids["vars"], "variables", "Vars", "ok"), (ids["a"], "studio", "Plate A", "ok"),
        (ids["b"], "studio", "Plate B", "ok"), (ids["asm"], "assembly", "Asm", "ok")]
    assert not any(t["dirty"] for t in tabs) and s.errors() == {}
    assert _volumes(s.outputs(ids["asm"])) == [2000.0, 4000.0]
    assert isinstance(s.outputs(ids["a"]), StudioOutputs) and isinstance(s.outputs(ids["vars"]), VariablesOutputs)

    # saved with no edit, each tab's bytes equal what FolderDocument.save wrote
    assert s.save() == list(ids.values())
    _same_as_saved(_saved(store, ids), original, {ids["vars"]: "variables", ids["a"]: "studio",
                                                  ids["b"]: "studio", ids["asm"]: "assembly"})

    # an edit to the Variable Studio rebuilds both studios and the Assembly, upstream first
    rebuilt = s.submit_edit(ids["vars"], {"op": "set_row", "index": 0, "expression": "30 mm"})
    assert rebuilt[0] == ids["vars"] and rebuilt[-1] == ids["asm"] and sorted(rebuilt) == sorted(ids.values())
    assert _volumes(s.outputs(ids["asm"])) == [3000.0, 6000.0]
    assert s.outputs(ids["vars"]).variables == {"w": 30.0}

    # a set_param edit on one studio rebuilds that studio and the Assembly, and the Assembly shows it
    rebuilt = s.submit_edit(ids["a"], {"op": "set_param", "feature": ext["a"], "key": "depth", "value": 5})
    assert rebuilt == [ids["a"], ids["asm"]]
    assert _volumes(s.outputs(ids["a"])) == [500.0]
    assert _volumes(s.outputs(ids["asm"])) == [500.0, 6000.0]
    assert {t["id"] for t in s.tabs() if t["dirty"]} == {ids["vars"], ids["a"]}

    # without save() a fresh session does not see the edits
    _same_as_saved(_saved(store, ids), original, {ids["vars"]: "variables", ids["a"]: "studio",
                                                  ids["b"]: "studio", ids["asm"]: "assembly"})
    fresh = sessions(store)
    assert _volumes(fresh.outputs(ids["asm"])) == [2000.0, 4000.0]
    fresh.close()

    # after save() it does, and what is saved reopens to the same state
    assert s.save() == list(ids.values())
    assert not any(t["dirty"] for t in s.tabs())
    assert _saved(store, ids)[ids["a"]] != original[ids["a"]]
    assert json.loads(store.read_tab(ids["a"]))["_tab_id"] == ids["a"]
    again = sessions(store)
    assert _volumes(again.outputs(ids["asm"])) == [500.0, 6000.0]
    assert again.outputs(ids["vars"]).variables == {"w": 30.0}
    assert again.errors() == {}

    if kind == "memory":      # the guard was live in this process and in every worker, and nothing it covers opened
        assert W.refused_opens() == []
        for e in (s, again):
            for tid in ids.values():
                assert e._eng.run_in_worker(tid, W.refused_opens) == []
        assert s._eng.run_in_worker(ids["a"], os.path.exists, os.path.join(os.sep, "nowhere")) is False
        assert [t[3] for t in s._eng.tabs()] == [None] * 4


# =========================================================================== no Qt

def test_importing_the_session_loads_no_qt(tmp_path):
    store = DS.FolderStore(str(tmp_path / "Doc"))
    _fd, ids, ext = _layout_document(store)
    script = tmp_path / "run_session.py"
    script.write_text(textwrap.dedent("""
        import json, sys

        def main(path, vars_id, a_id, asm_id, feature):
            from freeshape.session import DocumentSession
            out = {}
            with DocumentSession(path, spares=0) as s:
                out["tabs"] = [t["state"] for t in s.tabs()]
                out["rebuilt"] = s.submit_edit(a_id, {"op": "set_param", "feature": feature, "key": "depth", "value": 5})
                out["asm_parts"] = len(s.outputs(asm_id).parts)
            out["qt"] = sorted(m for m in sys.modules if m.startswith(("PySide6", "PyQt", "shiboken")))
            print("RESULT " + json.dumps(out))

        if __name__ == "__main__":
            main(*sys.argv[1:])
    """))
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, str(script), store.folder, ids["vars"], ids["a"], ids["asm"], ext["a"]],
                          capture_output=True, text=True, timeout=240, cwd=str(tmp_path), env=env)
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, (done.stdout, done.stderr[-2000:])
    out = json.loads(line[0][7:])
    assert out["qt"] == []
    assert out["tabs"] == ["ok"] * 4 and out["rebuilt"] == [ids["a"], ids["asm"]] and out["asm_parts"] == 2


# =========================================================================== Python feature, Drawing, a tab that will not open

class World2:
    pass


@pytest.fixture(scope="module")
def world2(tmp_path_factory):
    """A MemoryStore Document: a studio with a Python feature whose build is stored, an Assembly of it, a Drawing of
    the Assembly, and a Variable Studio whose data is garbage. The plugin is gone when the session opens it."""
    root = tmp_path_factory.mktemp("world2")
    user = root / "user_plugins"
    user.mkdir()
    mp = pytest.MonkeyPatch()
    mp.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    mp.setenv("FREESHAPE_TEST_REFUSE_TAB_OPENS", "1")
    P.SPARES.clear()
    undo = W.refuse_tab_file_opens()
    before = {p.id for p in pyfeature.plugins()}
    w = World2()
    sess = []

    def finish():
        for s in sess:
            s.close()
        P.SPARES.clear()
        for p in pyfeature.plugins():
            if p.id not in before:
                pyfeature.unregister_plugin(p.id)
        undo()
        mp.undo()
    try:
        assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
        w.store = DS.MemoryStore()
        fd = DF.FolderDocument.create_on(w.store, name="world2")
        st = fd.add_tab("studio", "Py")
        f = st.doc.make_feature("py:labelled/box")
        st.doc.add_feature(f)
        assert st.doc.errors == {}, st.doc.errors
        w.want = sorted(round(K.volume(p.shape), 6) for p in st.doc.state.parts.values())
        asm = fd.add_tab("assembly", "Asm")
        asm.doc.insert(L.studio_source(st, next(iter(st.doc.state.parts))), name="Py")
        asm.doc.refresh(solve=False, notify=False)
        dwg = fd.add_tab("drawing", "Dwg")
        dwg.doc.source = L.assembly_source(asm)
        dwg.doc.refresh(notify=False)
        bad = fd.add_tab("variables", "Bad")
        fd.save()
        w.store.write_tab(bad.id, b"{ not json")
        pyfeature.unregister_plugin("labelled")
        w.ids = {"studio": st.id, "asm": asm.id, "dwg": dwg.id, "bad": bad.id}
        w.feature = f["id"]
        w.session = DocumentSession(w.store, spares=0)
        sess.append(w.session)
        undo()                          # workers have their guard; later tests in this process and their workers do not
        mp.undo()
    except BaseException:
        finish()
        raise
    yield w
    finish()


def test_a_tab_that_fails_to_open_does_not_stop_the_others(world2):
    s, ids = world2.session, world2.ids
    by_id = {t["id"]: t for t in s.tabs()}
    assert by_id[ids["bad"]]["state"] == "failed" and "EngineError" in by_id[ids["bad"]]["error"]
    assert [by_id[ids[k]]["state"] for k in ("studio", "asm", "dwg")] == ["ok", "ok", "ok"]
    assert set(s.errors()) == {ids["bad"]}
    with pytest.raises(SessionError, match="did not open"):
        s.outputs(ids["bad"])
    with pytest.raises(SessionError, match="did not open"):
        s.submit_edit(ids["bad"], {"op": "add_row", "name": "x", "expression": "1"})
    assert _volumes(s.outputs(ids["asm"])) == world2.want


def test_a_python_feature_build_comes_from_the_stores_blobs(world2):
    out = world2.session.outputs(world2.ids["studio"])
    assert out.errors == {} and _volumes(out) == world2.want


def test_a_drawing_tab_opens_and_reports_outputs(world2):
    s, ids = world2.session, world2.ids
    out = s.outputs(ids["dwg"])
    assert isinstance(out, DrawingOutputs) and out.errors == {}
    assert out.source is not None and out.consumes == (ids["asm"],)
    assert next(t for t in s.tabs() if t["id"] == ids["dwg"])["state"] == "ok"
    assert s.outputs_wire(ids["dwg"])[:4] == b"FSW1"


def test_blobs_survive_save_and_a_fresh_session(world2, sessions):
    ids, store = world2.ids, world2.store
    s = world2.session
    before = {k: store.read_tab(v) for k, v in ids.items()}
    written = s.save()
    assert ids["bad"] not in written and set(written) == {ids["studio"], ids["asm"], ids["dwg"]}
    _same_as_saved({v: store.read_tab(v) for k, v in ids.items() if k != "bad"},
                   {v: before[k] for k, v in ids.items()}, {ids["studio"]: "studio", ids["asm"]: "assembly",
                                                            ids["dwg"]: "drawing"})
    assert store.read_tab(ids["bad"]) == b"{ not json"            # a tab that never opened is left alone
    builds = json.loads(store.read_tab(ids["studio"]))["python_builds"]
    keys = {k for rec in builds[world2.feature]["parts"] for k in (rec["brep"], rec["names"])}
    assert keys and all(store.blobs(ids["studio"]).has(k) for k in keys)
    fresh = sessions(store)
    assert _volumes(fresh.outputs(ids["studio"])) == world2.want
    assert fresh.outputs(ids["studio"]).errors == {}


def test_a_bad_call_raises_a_clear_error_and_the_session_carries_on(world2):
    s, ids = world2.session, world2.ids
    with pytest.raises(SessionError, match="no tab 'nope'"):
        s.submit_edit("nope", {"op": "undo"})
    with pytest.raises(SessionError, match="no tab 'nope'"):
        s.outputs("nope")
    with pytest.raises(SessionError, match="no edit 'explode'"):
        s.submit_edit(ids["studio"], {"op": "explode"})
    with pytest.raises(SessionError, match="'op'"):
        s.submit_edit(ids["studio"], "set_param")
    with pytest.raises(SessionError, match="needs 'key'"):
        s.submit_edit(ids["studio"], {"op": "set_param", "feature": world2.feature})
    with pytest.raises(SessionError, match="malformed|no feature"):
        s.submit_edit(ids["studio"], {"op": "set_param", "feature": "nope", "key": "x", "value": 1})
    assert next(t for t in s.tabs() if t["id"] == ids["studio"])["state"] == "ok"
    assert not any(t["dirty"] for t in s.tabs())
    assert _volumes(s.outputs(ids["asm"])) == world2.want


# =========================================================================== lifecycle

def test_close_leaves_no_worker_and_is_safe_twice(tmp_path):
    store = DS.FolderStore(str(tmp_path / "Doc"))
    fd = DF.FolderDocument.create_on(store, name="tiny")
    var = fd.add_tab("variables", "Vars")
    var.doc.add_row("w", "1 mm")
    fd.save()
    before = _children()
    with DocumentSession(store, spares=1) as s:
        pids = s.pids()
        assert list(pids) == [var.id] and _alive(pids[var.id])
        assert s.submit_edit(var.id, {"op": "set_row", "index": 0, "expression": "2 mm"}) == [var.id]
    assert not _alive(pids[var.id])
    s.close()                                                    # twice: no error
    with pytest.raises(SessionError, match="closed"):
        s.outputs(var.id)
    with pytest.raises(SessionError, match="closed"):
        s.save()
    assert s.pids() == {}
    assert _children() == before
