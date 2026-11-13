"""I1.1: a tab whose worker was restarted, or that was reopened after its worker died, keeps its link edges, so an
upstream edit still reaches it. Variable Studio V feeds Part Studio A.

  * on the process executor: kill A's worker, `restart`, edit V without touching A; A rebuilds once with V's value;
  * in an offscreen `MainWindow(engine=True)`: an edit to A after its worker died reopens A (`StudioEngine._job`);
    A's edge from V exists as soon as the tab is reopened, before any of A's edits is replayed.

Entry points that start a ProcessEngine need a `__main__` guard (workers import the test module again); pytest
supplies it."""
import os
import signal
import time

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape.document import Document
from freeshape.engine import ProcessEngine
from freeshape.engine import process as P
from freeshape.variable_studio import VariableStudioDoc

REF = "variable_studio_ref"


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def height(part):
    lo, hi = K.bbox(K.shape_from_brep(part.brep))
    return round(float(hi[2] - lo[2]), 6)


def set_w(value):
    return {"op": "set_row", "index": 0, "expression": value}


def make_studio(name, source, depth="#w"):
    a = Document()
    a.name = name
    a.var_inputs = lambda src: {"w": 20.0}              # only to build the file: the engine resolves it for real
    a.add_feature({"id": a.new_id(), "type": REF, "name": "Variables", "suppressed": False,
                   "params": {"source": source}})
    sid = sketch_feature(a, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(a, "extrude", regions=region_refs(a, sid), depth=depth, op="new")
    a.var_inputs = None
    return a


def kill_and_wait(engine, tab):
    os.kill(engine.pid(tab), signal.SIGKILL)
    end = time.monotonic() + 30
    while engine.errored(tab) is None and time.monotonic() < end:
        time.sleep(0.05)
    assert engine.errored(tab), "the worker was killed and the tab is not errored"


def test_restart_keeps_the_edge_so_an_upstream_edit_reaches_the_tab(tmp_path):
    v = VariableStudioDoc()
    v.add_row("w", "20 mm")
    vpath = str(tmp_path / "V.fvars")
    v.save(vpath)
    a = make_studio("A", {"kind": "variables", "path": vpath, "name": "V"})
    apath = str(tmp_path / "A.fshape")
    a.save(apath)
    eng = ProcessEngine()
    try:
        vt, at = eng.open_tab(vpath, tab="V"), eng.open_tab(apath, tab="A")
        assert height(eng.outputs(at).parts[0]) == 20 and ("A", "V") in eng.link_edges()
        kill_and_wait(eng, at)
        eng.links.forget_tab(at)                        # whatever dropped the edges while the worker was down
        assert ("A", "V") not in eng.link_edges()
        before = pid = eng.pid(at)
        eng.restart(at)
        assert eng.pid(at) != pid and ("A", "V") in eng.link_edges()
        count = eng.rebuild_count(at)
        eng.submit_edit(vt, set_w("35 mm"))             # A is not touched
        assert eng.rebuild_count(at) == count + 1
        assert height(eng.outputs(at).parts[0]) == 35 and before != eng.pid(at)
    finally:
        eng.close()


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    pytest.importorskip("PySide6.QtWidgets")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    root = tmp_path_factory.mktemp("restart")
    mp = pytest.MonkeyPatch()
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    mp.setenv("FREESHAPE_NO_RECOVERY", "1")
    mp.setenv("FREESHAPE_NO_HOME", "1")
    app = QApplication.instance() or QApplication([])      # noqa: F841 -- must stay alive
    os.makedirs(root / "autosave")
    os.makedirs(root / "thumbs")
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for name in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    fd = DF.FolderDocument.create(str(root / "Doc"), registry=R.default_registry())
    v = fd.add_tab("variables", "V")
    v.doc.add_row("w", "20 mm")
    a = fd.add_tab("studio", "A")
    made = make_studio("A", {"kind": "variables", "tab": v.id, "name": "V"})
    for f in made.features:
        a.doc.add_feature(f)
    fd.save()
    path, ids = fd.path, {"V": v.id, "A": a.id}
    fd.close()
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(path)
    assert w.studio_engine.settle(240) and not w.studio_engine.broken
    w.ids = ids
    yield w
    w.studio_engine.shutdown()
    P.SPARES.clear()
    mp.undo()


def test_reopened_studio_has_its_edge_before_any_edit_is_replayed(win, monkeypatch):
    se = win.studio_engine
    a_doc, v_doc = win.folder.tab(win.ids["A"]).doc, win.folder.tab(win.ids["V"]).doc
    a_id, v_id = win.ids["A"], win.ids["V"]
    assert (a_id, v_id) in se.live.link_edges(a_doc)
    pid = se.in_engine(a_doc, lambda e, tab: e.pid(tab))
    os.kill(pid, signal.SIGKILL)
    end = time.monotonic() + 30
    while se.in_engine(a_doc, lambda e, tab: e.errored(tab)) is None and time.monotonic() < end:
        time.sleep(0.05)

    seen = []                                           # A's edges at the moment each edit reaches the engine
    real = ProcessEngine.submit_edit

    def spy(self, tab, edit):
        if tab == a_id:
            seen.append((self.errored(tab), (a_id, v_id) in self.link_edges()))
        return real(self, tab, edit)

    monkeypatch.setattr(ProcessEngine, "submit_edit", spy)
    f = next(f for f in a_doc.features if f["type"] == "extrude")
    a_doc.replace_feature({**f, "params": {**f["params"], "depth": "#w + 5"}})
    assert se.settle(120) and not se.broken
    assert seen, "no edit reached A"
    assert all(edge for _, edge in seen), f"A lacked its edge from V when an edit arrived: {seen}"
    assert se.in_engine(a_doc, lambda e, tab: e.pid(tab)) != pid
    assert (a_id, v_id) in se.live.link_edges(a_doc)

    v_doc.set_row(0, expression="40 mm")                # an upstream edit still reaches the reopened A
    v_doc.notify()
    assert se.settle(120)
    out = se.in_engine(a_doc, lambda e, tab: e.outputs(tab))
    assert height(out.parts[0]) == 45
