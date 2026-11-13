"""I1.8: a Derived feature follows its source live on the process executor. Studios Skeleton, Base and Lid:
Base derives Skeleton's Layout sketch and extrudes it into a plate; Lid derives the Layout sketch and Base's
plate. One unsaved edit to Skeleton reaches Base and Lid in dependency order, each rebuilt once, whatever
order the tabs were opened in. A loop turns its Derived features red and returns."""
import copy
import os
import time

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import links as L
from freeshape.engine import LinkCycleError, ProcessEngine
from freeshape.engine import process as P
from freeshape.sketch import Sketch

LAYOUT = "F1"


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def width_edit(w):
    s = Sketch()
    rect_lines(s, 0, 0, w, 10)
    s.solve()
    return {"op": "set_param", "feature": LAYOUT, "key": "sketch", "value": s.to_dict()}


def build(tmp_path):
    """Skeleton (Layout sketch + a block), Base (derived Layout -> plate), Lid (derived Layout + Base's plate)."""
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())
    sk = fd.add_tab("studio", "Skeleton")
    sid = sketch_feature(sk.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    assert sid == LAYOUT
    add(sk.doc, "extrude", regions=region_refs(sk.doc, sid), depth=5, op="new")
    base = fd.add_tab("studio", "Base")
    d = add(base.doc, "derived", source=L.studio_source(sk), parts=[], sketches=[LAYOUT], connectors=[])
    add(base.doc, "extrude", regions=region_refs(base.doc, f"{d['id']}:{LAYOUT}"), depth=3, op="new")
    plate = next(iter(base.doc.state.parts))
    lid = fd.add_tab("studio", "Lid")
    add(lid.doc, "derived", source=L.studio_source(sk), parts=[], sketches=[LAYOUT], connectors=[])
    add(lid.doc, "derived", source=L.studio_source(base), parts=[plate], sketches=[], connectors=[])
    assert not (sk.doc.errors or base.doc.errors or lid.doc.errors)
    fd.save()
    return fd, {"Skeleton": sk, "Base": base, "Lid": lid}


def open_all(fd, order):
    eng = ProcessEngine()
    for name in order:
        t = next(t for t in fd.tabs if t.name == name)
        eng.open_tab(os.path.join(fd.path, t.file), tab=t.id)
    return eng


def volumes(eng, tab):
    return sorted(round(K.volume(K.shape_from_brep(p.brep)), 6) for p in eng.outputs(tab).parts)


def worker_opens():
    import builtins
    seen, real = [], builtins.open

    def spy(file, *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            seen.append(os.fsdecode(os.path.realpath(file)))
        return real(file, *a, **k)

    builtins.open = spy
    from freeshape.engine import worker as W
    W.current().opened = seen


def opened():
    from freeshape.engine import worker as W
    return list(W.current().opened)


@pytest.mark.parametrize("order", [["Skeleton", "Base", "Lid"], ["Skeleton", "Lid", "Base"]])
def test_one_edit_to_skeleton_rebuilds_base_and_lid_once_in_order(tmp_path, order):
    fd, tabs = build(tmp_path)
    ids = {n: t.id for n, t in tabs.items()}
    eng = open_all(fd, order)
    try:
        for n in ("Base", "Lid"):
            assert not eng.outputs(ids[n]).errors, (order, n, eng.outputs(ids[n]).errors)
        plate = volumes(eng, ids["Base"])
        assert plate == [round(20 * 10 * 3, 6)]
        assert (ids["Lid"], ids["Base"]) in eng.link_edges() and (ids["Base"], ids["Skeleton"]) in eng.link_edges()
        for n in ("Base", "Lid"):
            eng.run_in_worker(ids[n], worker_opens)
        before = eng.rebuild_counts()
        eng.submit_edit(ids["Skeleton"], width_edit(30))
        after = eng.rebuild_counts()
        delta = {n: after.get(ids[n], 0) - before.get(ids[n], 0) for n in ("Base", "Lid")}
        assert delta == {"Base": 1, "Lid": 1}
        assert eng.rebuild_order() == [ids["Base"], ids["Lid"]]
        wide = volumes(eng, ids["Base"])
        assert wide == [round(30 * 10 * 3, 6)] and wide[0] in volumes(eng, ids["Lid"]) and wide != plate
        for n in ("Skeleton", "Base", "Lid"):
            errs = eng.outputs(ids[n]).errors
            assert not errs and "ircular" not in str(errs), (n, errs)
        for n in ("Base", "Lid"):
            files = eng.run_in_worker(ids[n], opened)
            assert not [f for f in files if f.endswith(tabs["Skeleton"].file)], (n, files)
    finally:
        eng.close()


def test_an_edit_that_changes_nothing_base_consumes_leaves_it_alone(tmp_path):
    fd, tabs = build(tmp_path)
    ids = {n: t.id for n, t in tabs.items()}
    eng = open_all(fd, ["Skeleton", "Base", "Lid"])
    try:
        before = eng.rebuild_counts()
        eng.submit_edit(ids["Skeleton"], {"op": "rename_feature", "feature": "F2", "name": "Block"})
        assert eng.rebuild_counts() == before and eng.rebuild_order() == []
    finally:
        eng.close()


def derived_feature(fid, tab):
    return {"id": fid, "type": "derived", "name": "Derived", "suppressed": False,
            "params": {"source": {"kind": "studio", "tab": tab, "part": None, "studio_name": tab},
                       "parts": [], "sketches": [], "connectors": []}}


def test_two_studios_deriving_each_other_are_red_and_the_call_returns(tmp_path):
    eng = ProcessEngine()
    try:
        a, b = eng.new_tab("studio", "A", tab="A"), eng.new_tab("studio", "B", tab="B")
        eng.submit_edit(a, {"op": "add_feature", "feature": derived_feature("DA", "B")})
        start = time.monotonic()
        with pytest.raises(LinkCycleError):
            eng.submit_edit(b, {"op": "add_feature", "feature": derived_feature("DB", "A")})
        assert time.monotonic() - start < 10
        for tab, fid in ((a, "DA"), (b, "DB")):
            msg = eng.outputs(tab).errors.get(fid, "")
            assert "Circular link" in msg and "A" in msg and "B" in msg, (tab, eng.outputs(tab).errors)
    finally:
        eng.close()


def test_a_source_that_turns_red_makes_its_derived_red_and_fixing_it_clears(tmp_path):
    fd, tabs = build(tmp_path)
    ids = {n: t.id for n, t in tabs.items()}
    eng = open_all(fd, ["Skeleton", "Base"])
    try:
        assert not eng.outputs(ids["Base"]).errors
        before = eng.rebuild_counts().get(ids["Base"], 0)
        eng.submit_edit(ids["Skeleton"], {"op": "set_param", "feature": "F2", "key": "depth", "value": "nope("})
        assert eng.outputs(ids["Skeleton"]).errors
        assert eng.rebuild_counts().get(ids["Base"], 0) == before + 1
        assert any("in error" in m for m in eng.outputs(ids["Base"]).errors.values())
        eng.submit_edit(ids["Skeleton"], {"op": "set_param", "feature": "F2", "key": "depth", "value": 5})
        assert not eng.outputs(ids["Skeleton"]).errors and not eng.outputs(ids["Base"]).errors
    finally:
        eng.close()


# ------------------------------------------------------------------ the window
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_HOME"] = "1"


def test_an_assembly_of_base_and_lid_follows_a_skeleton_edit_with_its_tab_current(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    fd, tabs = build(tmp_path)
    asm = fd.add_tab("assembly", "Asm")
    base, lid = tabs["Base"], tabs["Lid"]
    asm.doc.insert(L.studio_source(base, next(iter(base.doc.state.parts))), name="Base")
    asm.doc.insert(L.studio_source(lid, next(iter(lid.doc.state.parts))), name="Lid")
    asm.doc.refresh(solve=False, notify=False)
    fd.save()
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    for d in ("autosave", "thumbs"):
        os.makedirs(tmp_path / d, exist_ok=True)
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    QApplication.instance() or QApplication([])
    w = MW.MainWindow(engine=True)
    try:
        w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        w.open_document(fd.path)
        assert w.studio_engine.settle(240)
        doc_of = lambda name: w.folder.tab(next(t.id for t in fd.tabs if t.name == name)).doc    # noqa: E731
        shown = lambda name: w.studio_engine.live.displayed(doc_of(name))                       # noqa: E731
        w.tabs.setCurrentIndex(w._tab_index(doc_of("Asm")))
        assert w.studio_engine.settle(60)
        before = shown("Asm")
        assert before and not w.studio_engine.live.cycle
        sk = doc_of("Skeleton")
        f = copy.deepcopy(sk.feature(LAYOUT))
        f["params"]["sketch"] = width_edit(30)["value"]
        sk.replace_feature(f)
        assert w.studio_engine.settle(120)
        assert w.doc is doc_of("Asm")
        assert shown("Asm") != before
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
