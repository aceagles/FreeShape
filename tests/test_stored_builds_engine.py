"""Stored builds reach the engine's copy of a Part Studio tab.

A feature that cannot build where it is opened shows the stored build saved with the file: (a) a Derived feature
whose source tab is gone (I1.3), (b) a `py:` Python feature whose plugin is not installed, and (c) a plugin feature
(`plugin:<id>`) whose plugin is not installed (I1.8). Each case is saved, its source or plugin removed, and the
file opened again through the engine three ways: the engine's own `open_tab`; the window's mirror (an empty tab
fed `plan_edits(snapshot)`, as `StudioEngine` does) on both executors; and a real `MainWindow(engine=True)`. Each
must show the part with the volume it had, no feature error, and, for (a) and (b), the stored-copy notice."""
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import docstore as DS
from freeshape import kernel as K
from freeshape import links as L
from freeshape import plugins as P
from freeshape import pyfeature
from freeshape.document import PartStudioDoc
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as EP

LABELLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pyplugins", "labelled")
GEAR = {"module": 1.5, "teeth": 14, "thickness": 4, "bore": 3}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """An empty user plugin folder here and in every worker spawned from now on (no warm spare started before);
    the py: plugins, the plugin registry, its load flag and copy memos as they were afterwards."""
    user = tmp_path / "user_plugins"
    user.mkdir()
    monkeypatch.setenv("FREESHAPE_PLUGIN_DIR", str(user))
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    before = {p.id for p in pyfeature.plugins()}
    saved = (dict(P.FEATURES), list(P.COMMANDS), list(P.PLUGINS), P._loaded[0], set(P.LAST_BODIES), set(P._RECORDS))
    EP.SPARES.clear()
    yield user
    EP.SPARES.clear()
    for p in pyfeature.plugins():
        if p.id not in before:
            pyfeature.unregister_plugin(p.id)
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


def _volume(parts):
    return sum(K.volume(s) for s in parts)


# =========================================================================== the three cases, saved and broken

def _derived(tmp_path, monkeypatch):
    """A Document: Src (a 20 x 10 x 5 box) and Use, which derives it; saved, then Src removed and saved again."""
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())
    src = fd.add_tab("studio", "Src")
    sid = sketch_feature(src.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    add(src.doc, "extrude", regions=region_refs(src.doc, sid), depth=5, op="new")
    use = fd.add_tab("studio", "Use")
    der = add(use.doc, "derived", source=L.studio_source(src), parts=["F2/1"], sketches=[], connectors=[])
    pid = f"{der['id']}/1"
    vol = K.volume(use.doc.state.parts[pid].shape)
    fd.save()
    fd.remove_tab(src.id)
    fd.save()
    path = fd.path
    fd.close()
    return SimpleNamespace(path=path, folder=True, tab=use.id, fid=der["id"], volume=vol,
                           notice=("missing", "stale"))


def _python(tmp_path, monkeypatch):
    """A Part Studio file with a labelled box (a py: feature) built and saved; the plugin then unregistered (it is
    in no worker's plugin folder either)."""
    assert pyfeature.register_plugin(LABELLED) is not None, pyfeature.problems()
    doc = PartStudioDoc()
    f = doc.make_feature("py:labelled/box")
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    vol = _volume(p.shape for p in doc.state.parts.values())
    os.makedirs(tmp_path / "py")
    path = str(tmp_path / "py" / "py.fshape")
    doc.save(path)
    assert json.load(open(path))["python_builds"][f["id"]]
    pyfeature.unregister_plugin("labelled")
    return SimpleNamespace(path=path, folder=False, tab="S1", fid=f["id"], volume=vol, notice=("locked",))


def _plugin(tmp_path, monkeypatch):
    """A Part Studio file with the example spur gear (a plugin: feature) built and saved; then no plugins, here and
    in every worker started from now on."""
    d = tmp_path / "plugins"
    d.mkdir()
    shutil.copy(os.path.join(P.examples_dir(), "spur_gear.py"), str(d / "spur_gear.py"))
    monkeypatch.setenv("FREESHAPE_PLUGINS", str(d))
    monkeypatch.delenv("FREESHAPE_NO_PLUGINS", raising=False)
    P.load_all([str(d)])
    p = P.default_params("plugin:spur_gear")
    p.update(GEAR)
    doc = PartStudioDoc()
    doc.add_feature({"id": "F1", "type": "plugin:spur_gear", "name": "Gear", "suppressed": False, "params": p})
    assert not doc.errors, doc.errors
    vol = _volume(p.shape for p in doc.state.parts.values())
    os.makedirs(tmp_path / "gear")
    path = str(tmp_path / "gear" / "gear.fshape")
    doc.save(path)
    assert P.is_copy(json.load(open(path))["python_builds"]["F1"])
    monkeypatch.setenv("FREESHAPE_NO_PLUGINS", "1")
    P._unload()
    EP.SPARES.clear()
    return SimpleNamespace(path=path, folder=False, tab="S1", fid="F1", volume=vol, notice=None)


CASES = {"derived": _derived, "python": _python, "plugin": _plugin}


@pytest.fixture(params=sorted(CASES))
def case(request, tmp_path, monkeypatch):
    c = CASES[request.param](tmp_path, monkeypatch)
    c.name = request.param
    return c


def _reopen(case):
    """(the Part Studio as the window loads it, the FolderDocument or None)."""
    if case.folder:
        fd = DF.FolderDocument.open(case.path, registry=R.default_registry())
        return fd.tab(case.tab).doc, fd
    return PartStudioDoc.load(case.path), None


def _check_notice(case, notices):
    if case.notice:
        note = notices.get(case.fid) or ""
        assert all(w in note for w in case.notice), notices


# =========================================================================== the engine API

def _mirror(engine, doc, tab):
    """Feed an empty engine tab the window's Document as StudioEngine does: the snapshot's blobs into the tab's
    lookup, then plan_edits from an empty tab."""
    from freeshape.ui.partstudio_engine import _EMPTY, plan_edits, snapshot
    snap, lookup = snapshot(doc), DS.HeldBlobs({})
    for data in snap["blobs"].values():
        lookup.put(data)
    tab = engine.new_tab("studio", name=doc.name, tab=tab, blobs=lookup)
    for edit in plan_edits(_EMPTY, snap):
        engine.submit_edit(tab, edit)
    return tab


@pytest.mark.parametrize("route", ["open", "mirror"])
def test_a_reopened_stored_build_shows_in_the_engine(case, make_engine, route):
    doc, fd = _reopen(case)
    try:
        assert not doc.errors, doc.errors                  # the window's own load shows the stored build
        _check_notice(case, doc.notices())
        engine = make_engine()
        engine.use_document(getattr(fd, "id", None), getattr(fd, "registry", None))
        if route == "open":
            tab = engine.open_tab(doc.path, tab=case.tab)
        else:
            tab = _mirror(engine, doc, case.tab)
        out = engine.outputs(tab)
        assert out.errors == {}, out.errors
        assert _volume(K.shape_from_brep(p.brep) for p in out.parts) == pytest.approx(case.volume, rel=1e-9)
        if not make_engine.process:
            _check_notice(case, engine._tabs[tab].doc.notices())
    finally:
        if fd is not None:
            fd.close()


# =========================================================================== a real window

class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


@pytest.fixture
def window(tmp_path, monkeypatch):
    """make() -> a MainWindow with the engine on, its autosave and thumbnails under tmp_path; each one closed (and
    its workers ended) afterwards."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    made = []

    def make():
        EP.SPARES.clear()                   # its spare starts with the environment as it is now
        w = MW.MainWindow(engine=True)
        w.settings = _Settings()
        made.append(w)
        return w

    yield make
    monkeypatch.setattr(MW.MainWindow, "_confirm_discard", lambda self, d: True)
    for w in made:
        w.close()
        app.processEvents()
        if w.studio_engine is not None:
            w.studio_engine.shutdown()
        w.deleteLater()
        app.processEvents()
    EP.SPARES.clear()


def test_a_reopened_stored_build_shows_in_a_window_with_the_engine_on(case, window):
    w = window()
    w.open_any(case.path)
    eng = w.studio_engine
    assert eng.settle(180) and eng.broken is None, eng.broken
    doc = w.folder.tab(case.tab).doc if case.folder else w.doc
    assert eng.attached(doc)
    worker = eng.in_engine(doc, lambda e, t: e.outputs(t))
    assert worker.errors == {}, worker.errors
    assert not doc.errors, doc.errors
    assert _volume(p.shape for p in doc.state.parts.values()) == pytest.approx(case.volume, rel=1e-9)
    _check_notice(case, doc.notices())


# =========================================================================== nothing extra, no ping-pong

def test_plan_edits_sends_the_stored_builds_that_differ_first():
    from freeshape.ui.partstudio_engine import _EMPTY, plan_edits
    feat = {"id": "F1", "type": "plugin:x", "name": "X", "params": {}}
    entry = {"stamp": "s", "plugin": "x", "numeric": [], "breps": ["a" * 64]}
    plain = dict(_EMPTY, features=[feat])
    held = dict(plain, python_builds={"F1": entry})
    assert [e["op"] for e in plan_edits(_EMPTY, plain)] == ["add_feature"]          # no builds: nothing extra
    assert plan_edits(_EMPTY, held)[0] == {"op": "set_stored_build", "feature": "F1", "build": entry}
    assert plan_edits(held, held) == [] and plan_edits(plain, plain) == []
    assert plan_edits(held, plain) == [{"op": "set_stored_build", "feature": "F1", "build": None}]


def _worker_ops(eng, doc):
    """The ops of every edit the window sent the tab's worker (the process executor's edit log)."""
    return eng.in_engine(doc, lambda e, t: [x["op"] for x in e._tabs[t].edits])


def test_the_window_sends_no_stored_build_back_to_the_worker_that_made_it(window, tmp_path, monkeypatch):
    d = tmp_path / "plugins"
    d.mkdir()
    shutil.copy(os.path.join(P.examples_dir(), "spur_gear.py"), str(d / "spur_gear.py"))
    monkeypatch.setenv("FREESHAPE_PLUGINS", str(d))
    monkeypatch.delenv("FREESHAPE_NO_PLUGINS", raising=False)
    P.load_all([str(d)])
    w = window()
    doc = PartStudioDoc()
    w._add_tab(doc)
    eng = w.studio_engine
    assert eng.settle(180) and eng.attached(doc)
    add(doc, "variable", name="v", expr="1")
    assert eng.settle(180) and "set_stored_build" not in _worker_ops(eng, doc)      # no builds: nothing extra

    gear = doc.make_feature("plugin:spur_gear")
    gear["params"].update(GEAR)
    name = gear["name"]
    doc.add_feature(gear)
    assert eng.settle(180) and not doc.errors, doc.errors
    assert P.is_copy(doc.python_builds.get(gear["id"]))                  # the worker's copy, taken by the window
    shown = eng.displayed_parts(doc)
    doc.rename_feature(gear["id"], "Gear")                               # an edit after it: the copy stays put
    assert eng.settle(180)
    doc.save(str(tmp_path / "g.fshape"))
    doc.rename_feature(gear["id"], "Gear 2")                             # and after a save
    assert eng.settle(180)
    assert "set_stored_build" not in _worker_ops(eng, doc)
    assert eng.displayed_parts(doc) == shown
    doc.undo()
    doc.undo()
    assert eng.settle(180) and doc.feature(gear["id"])["name"] == name
    assert "set_stored_build" not in _worker_ops(eng, doc) and eng.displayed_parts(doc) == shown
