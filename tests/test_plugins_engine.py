"""I1.8: upstream-style plugin features (`plugin:<id>`, freeshape/plugins.py) built in the engine worker.

The worker loads the plugins folder ($FREESHAPE_PLUGINS) when a rebuild meets a plugin feature. The copy that
lets a file open without the plugin is a stored build: an entry in `python_builds` naming each body's BREP by
blob key, written by the process that built the feature and brought back with its tab data. Every engine test
runs on both executors; the workers are spawned after the environment is set, with no warm spare."""
import json
import math
import os
import shutil

import pytest

from freeshape import blobstore as B
from freeshape import docstore as DS
from freeshape import features as F
from freeshape import kernel as K
from freeshape import plugins as P
from freeshape.document import PartStudioDoc
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as EP

GEAR = {"module": 1.5, "teeth": 14, "thickness": 4, "bore": 3}

TOOLS = '''
"""Test tools: a block, a hole through a picked face, and a command that adds a gear."""
def register(api):
    fs = api.geom

    def block(ctx):
        return fs.box(ctx["x"], ctx["y"], ctx["z"])
    api.add_feature("block", "Block", block, fields=[
        api.number("x", "X", 40), api.number("y", "Y", 30), api.number("z", "Z", 10)])

    def hole(ctx):
        from freeshape import kernel as K
        faces = ctx.faces("face")
        if not faces:
            ctx.fail("Pick a face")
        c = K.face_info(faces[0])["c"]
        return fs.cylinder(ctx["d"] / 2, 100, at=(c[0], c[1], c[2] - 50))
    api.add_feature("hole", "Hole through", hole, fields=[
        api.select("face", "Face", kinds="face", multi=False), api.number("d", "Diameter", 6)])

    api.add_command("Add a gear", lambda ctx: ctx.add_feature(
        "spur_gear", module=1.5, teeth=14, thickness=4, bore=3))
'''


@pytest.fixture(autouse=True)
def plugdir(tmp_path, monkeypatch):
    """The example spur gear and the test tools in a plugins folder, here and in every worker started from now
    on; the registry, the load flag and the copy memos as they were afterwards."""
    d = tmp_path / "plugins"
    d.mkdir()
    shutil.copy(os.path.join(P.examples_dir(), "spur_gear.py"), str(d / "spur_gear.py"))
    (d / "tools.py").write_text(TOOLS)
    monkeypatch.setenv("FREESHAPE_PLUGINS", str(d))
    monkeypatch.delenv("FREESHAPE_NO_PLUGINS", raising=False)
    saved = (dict(P.FEATURES), list(P.COMMANDS), list(P.PLUGINS), P._loaded[0], set(P.LAST_BODIES), set(P._RECORDS))
    EP.SPARES.clear()
    P.load_all([str(d)])
    assert not [i.error for i in P.PLUGINS if i.error]
    yield d
    EP.SPARES.clear()
    P._unload()
    feats, cmds, infos, loaded, bodies, records = saved
    for spec in feats.values():
        P.FEATURES[spec.ftype] = spec
        P._register_feature_type(spec)
    P.COMMANDS[:], P.PLUGINS[:], P._loaded[0] = cmds, infos, loaded
    for memo, keep in ((P.LAST_BODIES, bodies), (P._RECORDS, records)):
        for k in set(memo) - keep:
            del memo[k]


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


def _feature(fid, ftype, **params):
    p = P.default_params(ftype)
    p.update(params)
    return {"id": fid, "type": ftype, "name": fid, "suppressed": False, "params": p}


def _volume(out):
    return sum(K.volume(K.shape_from_brep(p.brep)) for p in out.parts)


def _doc_volume(doc):
    return sum(K.volume(p.shape) for p in doc.state.parts.values())


def _in_process(*feats):
    doc = PartStudioDoc()
    for f in feats:
        doc.add_feature(json.loads(json.dumps(f)))
    assert not doc.errors, doc.errors
    return doc


def _without_plugins(monkeypatch):
    """As a machine without the plugins: FREESHAPE_NO_PLUGINS here and in workers started from now on."""
    monkeypatch.setenv("FREESHAPE_NO_PLUGINS", "1")
    P._unload()
    EP.SPARES.clear()


# =========================================================================== building in the worker

def test_the_example_spur_gear_builds_in_the_engine_like_in_process(make_engine):
    gear = _feature("F1", "plugin:spur_gear", **GEAR)
    want = _doc_volume(_in_process(gear))
    engine = make_engine()
    tab = engine.new_tab("studio", "Gears")
    engine.submit_edit(tab, {"op": "add_feature", "feature": gear})
    out = engine.outputs(tab)
    assert out.errors == {} and len(out.parts) == 1
    assert _volume(out) == pytest.approx(want, rel=1e-9)


def test_a_worker_build_saves_its_copy_as_blobs_and_reopens_without_the_plugin(make_engine, monkeypatch):
    gear = _feature("F1", "plugin:spur_gear", **GEAR)
    engine = make_engine()
    tab = engine.new_tab("studio", "Gears")
    engine.submit_edit(tab, {"op": "add_feature", "feature": gear})
    built = engine.outputs(tab)
    assert built.errors == {}
    store = DS.MemoryStore()
    data = engine.tab_data(tab, blobs=store.blobs("S1"))
    d = json.loads(data)
    assert "plugin_copies" not in d                                   # nothing inline
    entry = d["python_builds"]["F1"]
    assert P.is_copy(entry) and entry["plugin"] == "spur_gear" and len(entry["breps"]) == 1
    assert all(store.has_blob(k, "S1") for k in entry["breps"])       # the copy is a blob

    _without_plugins(monkeypatch)
    other = make_engine()
    again = other.open_tab_data("studio", data, "S1", "Gears", blobs=store.blobs("S1"))
    out = other.outputs(again)
    assert out.errors == {}, out.errors
    shapes = lambda o: [B.geometry_key(K.shape_from_brep(p.brep)) for p in o.parts]  # noqa: E731
    assert shapes(out) == shapes(built)                                    # unchanged
    assert _volume(out) == pytest.approx(_volume(built), rel=1e-12)


def test_a_face_selection_and_a_subtract_into_an_existing_part_build_in_the_worker(make_engine):
    block = _feature("F1", "plugin:block")
    base = _in_process(block)
    pid, part = next(iter(base.state.parts.items()))
    top = max(K.faces(part.shape), key=lambda f: K.face_info(f)["c"][2])
    ref = F.make_ref(pid, top, "face", part.shape, base.state.named(pid))
    hole = _feature("F2", "plugin:hole", face=[["face", ref]], d=6, op="remove")
    want = 40 * 30 * 10 - math.pi * 9 * 10
    assert _doc_volume(_in_process(block, hole)) == pytest.approx(want, rel=1e-6)
    engine = make_engine()
    tab = engine.new_tab("studio", "Holes")
    for i, f in enumerate((block, hole)):
        engine.submit_edit(tab, {"op": "add_feature", "feature": f, "index": i})
    out = engine.outputs(tab)
    assert out.errors == {}, out.errors
    assert len(out.parts) == 1 and _volume(out) == pytest.approx(want, rel=1e-6)


def test_an_upstream_file_with_inline_copies_opens_without_the_plugin_and_resaves_them_as_blobs(tmp_path,
                                                                                                monkeypatch):
    import base64
    doc = _in_process(_feature("F1", "plugin:spur_gear", **GEAR))
    v0 = _doc_volume(doc)
    path = str(tmp_path / "up.fshape")
    doc.save(path)
    with open(path) as fh:
        d = json.load(fh)
    entry = d.pop("python_builds")["F1"]                 # as upstream FreeShape 2.0 writes the copy: inline
    store = B.store_for(path)
    inline = [base64.b64encode(K.shape_to_brep(B.shape_from_brep(store.get(k)))).decode() for k in entry["breps"]]
    d["plugin_copies"] = {"F1": dict(entry, breps=inline)}
    other = tmp_path / "other"
    other.mkdir()
    path = str(other / "up.fshape")
    with open(path, "w") as fh:
        json.dump(d, fh)

    _without_plugins(monkeypatch)
    again = PartStudioDoc.load(path)
    assert not again.errors, again.errors
    assert _doc_volume(again) == pytest.approx(v0, rel=1e-9)
    again.save(path)
    with open(path) as fh:
        d = json.load(fh)
    assert "plugin_copies" not in d and P.is_copy(d["python_builds"]["F1"])
    assert all(B.store_for(path).has(k) for k in d["python_builds"]["F1"]["breps"])
    assert _doc_volume(PartStudioDoc.load(path)) == pytest.approx(v0, rel=1e-9)


# =========================================================================== a plugin command in the window

class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


@pytest.fixture
def engine_window(monkeypatch):
    """A window whose Part Studio tabs rebuild in the engine, with one empty Part Studio; (window, its doc)."""
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow(engine=True)
    try:
        w.settings = _Settings()
        doc = PartStudioDoc()
        w._add_tab(doc)
        assert w.studio_engine.settle(180) and w.doc is doc
        yield w, doc
    finally:
        w.studio_engine.shutdown()
        EP.SPARES.clear()
        w.deleteLater()


def _worker_features(w, doc):
    return w.studio_engine.in_engine(doc, lambda e, tab: [f["id"] for f in json.loads(
        e.tab_data(tab, blobs=DS.HeldBlobs({})))["features"]])


def test_a_plugin_command_adds_through_the_engine_and_the_window_saves_the_workers_copy(engine_window, tmp_path,
                                                                                         monkeypatch):
    from freeshape.ui import plugin_ui
    w, doc = engine_window
    eng = w.studio_engine
    cmd = next(c for c in P.COMMANDS if c.label == "Add a gear")
    plugin_ui.run_command(w, cmd)
    assert eng.settle(180) and eng.broken is None
    fid = doc.features[-1]["id"]
    assert fid in _worker_features(w, doc)                    # the worker's document has the feature
    assert not doc.errors and len(doc.state.parts) == 1
    v0 = _doc_volume(doc)
    assert v0 == pytest.approx(_doc_volume(_in_process(_feature(fid, "plugin:spur_gear", **GEAR))), rel=1e-9)

    path = str(tmp_path / "gear.fshape")
    doc.save(path)                                             # the window's save, after a worker build
    with open(path) as fh:
        d = json.load(fh)
    assert "plugin_copies" not in d
    entry = d["python_builds"][fid]
    assert all(os.path.isfile(os.path.join(str(tmp_path), "blobs", k)) for k in entry["breps"])

    doc.undo()                                                 # one undo removes it, in the worker too
    assert eng.settle(180)
    assert fid not in _worker_features(w, doc) and not doc.state.parts

    _without_plugins(monkeypatch)
    again = PartStudioDoc.load(path)
    assert not again.errors, again.errors
    assert _doc_volume(again) == pytest.approx(v0, rel=1e-9)


def test_reloading_plugins_rebuilds_the_part_studio_in_its_worker_with_the_new_code(engine_window, plugdir):
    from freeshape.ui.plugin_ui import PluginsDialog
    w, doc = engine_window
    f = doc.make_feature("plugin:block")
    doc.add_feature(f)
    assert w.studio_engine.settle(180) and not doc.errors
    assert _doc_volume(doc) == pytest.approx(40 * 30 * 10)
    tools = plugdir / "tools.py"
    tools.write_text(TOOLS.replace('fs.box(ctx["x"], ctx["y"], ctx["z"])', 'fs.box(ctx["x"], ctx["y"], 2 * ctx["z"])'))
    dlg = PluginsDialog(w)
    dlg.reload()
    assert w.studio_engine.settle(180) and w.studio_engine.broken is None
    assert not doc.errors and _doc_volume(doc) == pytest.approx(40 * 30 * 20)
    dlg.deleteLater()
