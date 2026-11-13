"""I3.8: Assembly and Drawing tabs follow the engine's Link graph: an edit to a tab changes what the tabs that
consume it display, with no tab switch, and a Variable Studio is a tab of the window.

One offscreen window with the process executor serves the module (every tab is a worker process, so the module
opens as few as it can): a Document with Variable Studio V, Part Studios A (extrude depth #w) and B (depth
#w * 2), Assembly C holding a part of each, and Drawing D of C. What a tab displays is read as hashes of the
shapes the window holds (`LiveTabs.displayed`) and compared with the hashes the headless in-process engine gives
for the same edit."""
import os
import signal

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import links as L
from freeshape.engine import InProcessEngine
from freeshape.engine import process as P
from freeshape.ui.partstudio_engine import geometry_hash

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

REF = "variable_studio_ref"


def _children():
    me, out = os.getpid(), []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as fh:
                    rest = fh.read().rsplit(")", 1)[1].split()
                with open(f"/proc/{d}/cmdline", "rb") as fh:
                    cmd = fh.read()
                if int(rest[1]) == me and rest[0] != "Z" and b"spawn_main" in cmd:
                    out.append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    return out


def _studio(fd, name, vtab, depth):
    """A Part Studio tab reading V's `w`: a 10 x 10 square extruded by `depth` (an expression)."""
    tab = fd.add_tab("studio", name)
    doc = tab.doc
    doc.var_inputs = lambda src: {"w": 20.0}               # only to build the file: the engine resolves V for real
    doc.add_feature({"id": doc.new_id(), "type": REF, "name": "Variables", "suppressed": False,
                     "params": {"source": {"kind": "variables", "tab": vtab.id, "name": vtab.name}}})
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=depth, op="new")
    doc.var_inputs = None
    return tab


def build_document(root):
    """V (w = 20 mm), A (depth #w), B (depth #w * 2), assembly C of one part of each, drawing D of C."""
    fd = DF.FolderDocument.create(str(root / "Live"), registry=R.default_registry())
    v = fd.add_tab("variables", "V")
    v.doc.add_row("w", "20 mm")
    a, b = _studio(fd, "A", v, "#w"), _studio(fd, "B", v, "#w * 2")
    c = fd.add_tab("assembly", "C")
    for tab in (a, b):
        c.doc.insert(L.studio_source(tab, next(iter(tab.doc.state.parts))), name=tab.name)
    c.doc.refresh(solve=False, notify=False)
    d = fd.add_tab("drawing", "D")
    d.doc.source = L.assembly_source(c)
    d.doc.refresh(notify=False)
    fd.save()
    return fd.path, {"V": v.id, "A": a.id, "B": b.id, "C": c.id, "D": d.id}


def headless(path, ids, edits=()):
    """The shown hashes from the in-process engine over the Document's files, after `edits` [(tab id, edit)]."""
    fd = DF.FolderDocument.open(path)
    eng = InProcessEngine()
    order = {"variables": 0, "studio": 1, "assembly": 2, "drawing": 3}
    for t in sorted(fd.tabs, key=lambda t: order[t.kind]):
        eng.open_tab(os.path.join(path, t.file), tab=t.id)
    for tab, edit in edits:
        eng.submit_edit(tab, edit)
    out = {}
    for t in fd.tabs:
        o = eng.outputs(t.id)
        if t.kind == "assembly":
            out[t.id] = {i.id: i.part for i in o.instances}
        elif t.kind == "drawing":
            out[t.id] = geometry_hash(eng._tabs[t.id].doc.shape)
        elif t.kind == "studio":
            out[t.id] = {p.id: p.hash for p in o.parts}
    eng.close()
    return out


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    root = tmp_path_factory.mktemp("live")
    mp = pytest.MonkeyPatch()
    mp.setenv("FREESHAPE_DATA_DIR", str(root / "appdata"))
    app = QApplication.instance() or QApplication([])
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    os.makedirs(root / "autosave", exist_ok=True)
    os.makedirs(root / "thumbs", exist_ok=True)
    mp.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(root / "autosave")))
    mp.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(root / "autosave")))
    mp.setattr(H, "thumbs_dir", lambda: str(root / "thumbs"))
    for name in ("warning", "critical", "information"):
        mp.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    path, ids = build_document(root)
    before = set(_children())
    w = MW.MainWindow(engine=True)
    from PySide6.QtCore import QSettings
    w.settings = QSettings(str(root / "settings.ini"), QSettings.IniFormat)
    w.open_document(path)
    assert w.studio_engine.settle(240)
    w.path, w.ids = path, ids
    w.doc_of = lambda key: w.folder.tab(ids[key]).doc
    yield w
    w.studio_engine.shutdown()
    P.SPARES.clear()
    left = sorted(set(_children()) - before)
    for p in left:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    mp.undo()
    assert not left, f"worker processes outlived the window: {left}"


def shown(w, key):
    return w.studio_engine.live.displayed(w.doc_of(key))


def current(w, key):
    w.tabs.setCurrentIndex(w._tab_index(w.doc_of(key)))
    w.studio_engine.settle(60)


def test_the_document_opens_with_every_tab_and_the_graph_is_wired(win):
    w = win
    assert w.tabs.count() == 5 and not w.studio_engine.broken
    ids = w.ids
    edges = set(w.studio_engine.live.link_edges(w.doc_of("C")))
    assert {(ids["A"], ids["V"]), (ids["B"], ids["V"]), (ids["C"], ids["A"]), (ids["C"], ids["B"]),
            (ids["D"], ids["C"])} <= edges
    first = headless(w.path, ids)
    for key in "AB":
        assert shown(w, key) == first[ids[key]]
    assert shown(w, "C") == first[ids["C"]] and shown(w, "D") == first[ids["D"]]


def test_an_edit_to_a_studio_updates_the_assembly_and_drawing_without_a_tab_switch(win):
    w = win
    current(w, "C")
    ids, doc = w.ids, w.doc_of("A")
    before = {k: shown(w, k) for k in "CD"}
    index = w.tabs.currentIndex()
    f = next(f for f in doc.features if f["type"] == "extrude")
    f = {**f, "params": {**f["params"], "depth": "45"}}
    doc.replace_feature(f)                                  # what a committed feature dialog does
    assert w.studio_engine.settle(120)
    assert w.tabs.currentIndex() == index and w.doc is w.doc_of("C")      # no tab switch
    expect = headless(w.path, ids, [(ids["A"], {"op": "replace_feature", "feature": f})])
    after = {k: shown(w, k) for k in "CD"}
    assert after["C"] != before["C"] and after["D"] != before["D"]
    assert after["C"] == expect[ids["C"]] and after["D"] == expect[ids["D"]]
    assert shown(w, "A") == expect[ids["A"]]
    doc.undo()
    assert w.studio_engine.settle(120)
    assert {k: shown(w, k) for k in "CD"} == before


def test_switching_tabs_refreshes_no_assembly_and_no_drawing(win, monkeypatch):
    from freeshape.assembly import AssemblyDoc
    from freeshape.drawing import DrawingDoc
    w = win
    calls = []
    monkeypatch.setattr(AssemblyDoc, "refresh", lambda self, *a, **k: calls.append(("assembly", self.name)))
    monkeypatch.setattr(DrawingDoc, "refresh", lambda self, *a, **k: calls.append(("drawing", self.name)))
    for key in ("C", "D", "A", "V", "B", "D", "C"):
        current(w, key)
        assert w.doc is w.doc_of(key)
    assert calls == []


def test_a_variable_studio_tab_shows_its_table_and_one_edit_updates_both_studios_once(win):
    """V is a tab with the table panel; editing a cell there updates the assembly (and the drawing) with no
    tab switch; C consumes A and B, which both read V, and is rebuilt exactly once for the one edit."""
    w = win
    ids, v = w.ids, w.doc_of("V")
    current(w, "V")
    panel = w.var_tabs.panel_for(v)
    assert w.doc is v and w.var_tabs.stack.currentWidget() is panel and panel.isVisibleTo(w)
    assert w.view_stack.currentWidget() is w.var_tabs.stack
    assert w.tabs.tabText(w._tab_index(v)).startswith("V")
    assert panel.table.rowCount() == 1 and panel.table.item(0, 1).text() == "20 mm"

    live = w.studio_engine.live
    counts = lambda: w.studio_engine.in_engine(v, lambda e, tab: e.rebuild_counts())      # noqa: E731
    before = {k: shown(w, k) for k in "ABCD"}
    n0 = counts().get(ids["C"], 0)
    index = w.tabs.currentIndex()
    panel.table.item(0, 1).setText("35 mm")                 # the person types in the cell
    assert w.studio_engine.settle(120)
    assert w.tabs.currentIndex() == index and w.doc is v    # no tab switch
    expect = headless(w.path, ids, [(ids["V"], {"op": "set_row", "index": 0, "expression": "35 mm"})])
    for key in "ABCD":
        assert shown(w, key) != before[key], key
        assert shown(w, key) == expect[ids[key]], key
    after = counts()
    assert after[ids["C"]] - n0 == 1                        # one edit of V: the assembly rebuilt once
    assert after[ids["A"]] >= 1 and after[ids["B"]] >= 1
    assert v.dirty and w.tabs.tabText(w._tab_index(v)).endswith("•")
    assert live.cycle is None
