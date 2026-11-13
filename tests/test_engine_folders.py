"""I1.4: feature folders and part colours as engine edits. Each folder edit and the colour edit goes through
`submit_edit` on both executors (in-process, and one worker process per tab) and changes the tab's own document;
the window's mirror (partstudio_engine.plan_edits) sends the same edits."""
import json
import os

import pytest

from conftest import ROOT, add, block_doc
from freeshape import document as DOC
from freeshape.engine import EngineError, InProcessEngine, ProcessEngine

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}


@pytest.fixture(params=sorted(ENGINES))
def kind(request):
    return request.param


@pytest.fixture
def make_engine(kind):
    made = []

    def make():
        made.append(ENGINES[kind]())
        return made[-1]

    yield make
    for e in made:
        e.close()


@pytest.fixture
def studio(make_engine, tmp_path):
    """(engine, tab, variable ids, path): a block and six variables (cheap features to group), opened as a tab."""
    doc = block_doc()
    ids = [add(doc, "variable", name=f"v{k}", expr=str(k))["id"] for k in range(6)]
    path = str(tmp_path / "t.fshape")
    doc.save(path)
    e = make_engine()
    return e, e.open_tab(path), ids, path


def data(e, tab):
    return json.loads(bytes(e.tab_data(tab)).decode("utf-8"))


def folder_of(e, tab):
    d = data(e, tab)
    return {f["id"]: f.get("folder") for f in d["features"]}, d.get("folders", {})


def part_hashes(e, tab):
    return {p.id: p.hash for p in e.outputs(tab).parts}


def undo(e, tab):
    e.submit_edit(tab, {"op": "undo"})


def dirty(e, tab, kind):
    """The worker document's dirty flag (in-process); a worker process shows an edit as a new revision."""
    if kind == "inprocess":
        return e._tabs[tab].doc.dirty
    return e.outputs(tab).revision > 0


def test_make_folder_ungroup_and_undo(studio, kind):
    e, tab, v, _ = studio
    before = part_hashes(e, tab)
    e.submit_edit(tab, {"op": "save", "path": studio[3]})
    e.submit_edit(tab, {"op": "make_folder", "ids": v[1:4], "name": "Group"})
    members, folders = folder_of(e, tab)
    (fo, info), = folders.items()
    assert info == {"name": "Group", "open": True}
    assert [i for i, f in members.items() if f == fo] == v[1:4]
    assert dirty(e, tab, kind) and part_hashes(e, tab) == before
    undo(e, tab)
    assert folder_of(e, tab)[1] == {}
    e.submit_edit(tab, {"op": "redo"})
    e.submit_edit(tab, {"op": "ungroup_folder", "folder": fo})
    members, folders = folder_of(e, tab)
    assert folders == {} and not any(members.values())
    undo(e, tab)
    assert folder_of(e, tab)[1] == {fo: {"name": "Group", "open": True}}


def test_make_folder_refuses_a_split_selection_and_an_unknown_folder(studio):
    e, tab, v, _ = studio
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "make_folder", "ids": [v[0], v[5]]})
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "rename_folder", "folder": "D9", "name": "x"})
    assert folder_of(e, tab)[1] == {}


def test_make_folder_takes_the_id_it_is_given(studio):
    e, tab, v, _ = studio
    e.submit_edit(tab, {"op": "make_folder", "ids": v[:2], "name": "A", "folder": "D7"})
    assert set(folder_of(e, tab)[1]) == {"D7"}
    assert [i for i, f in folder_of(e, tab)[0].items() if f == "D7"] == v[:2]
    with pytest.raises(EngineError):
        e.submit_edit(tab, {"op": "make_folder", "ids": v[3:5], "folder": "D7"})


def test_rename_and_open_close(studio, kind):
    e, tab, v, _ = studio
    e.submit_edit(tab, {"op": "make_folder", "ids": v[:3], "name": "Group"})
    fo = next(iter(folder_of(e, tab)[1]))
    e.submit_edit(tab, {"op": "rename_folder", "folder": fo, "name": "Renamed"})
    assert folder_of(e, tab)[1][fo]["name"] == "Renamed"
    undo(e, tab)
    assert folder_of(e, tab)[1][fo]["name"] == "Group"
    e.submit_edit(tab, {"op": "set_folder_open", "folder": fo, "value": False})
    assert folder_of(e, tab)[1][fo]["open"] is False and dirty(e, tab, kind)
    e.submit_edit(tab, {"op": "set_folder_open", "folder": fo, "value": True})
    assert folder_of(e, tab)[1][fo]["open"] is True


def test_remove_from_folder_keeps_it_one_run_and_one_undo(studio):
    e, tab, v, _ = studio
    e.submit_edit(tab, {"op": "make_folder", "ids": v[:4], "name": "Group"})
    fo = next(iter(folder_of(e, tab)[1]))
    e.submit_edit(tab, {"op": "remove_from_folder", "ids": [v[1]]})      # from the middle: moves after the folder
    members, _ = folder_of(e, tab)
    assert [i for i, f in members.items() if f == fo] == [v[0], v[2], v[3]] and not members[v[1]]
    undo(e, tab)
    assert [i for i, f in folder_of(e, tab)[0].items() if f == fo] == v[:4]


def test_set_folder_suppressed_suppresses_the_features_and_rebuilds(studio):
    e, tab, v, _ = studio
    e.submit_edit(tab, {"op": "make_folder", "ids": v[:2], "name": "Group"})
    fo = next(iter(folder_of(e, tab)[1]))
    e.submit_edit(tab, {"op": "set_folder_suppressed", "folder": fo, "value": True})
    sup = {f["id"]: bool(f.get("suppressed")) for f in data(e, tab)["features"]}
    assert sup[v[0]] and sup[v[1]] and not sup[v[2]]
    undo(e, tab)
    assert not any(f.get("suppressed") for f in data(e, tab)["features"])


def test_folder_edits_other_than_suppress_rebuild_nothing(make_engine, tmp_path, monkeypatch):
    """Counted where the build runs: in this process."""
    doc = block_doc()
    v = [add(doc, "variable", name=f"v{k}", expr=str(k))["id"] for k in range(4)]
    path = str(tmp_path / "t.fshape")
    doc.save(path)
    e = InProcessEngine()
    try:
        tab = e.open_tab(path)
        n = []
        real = DOC.PartStudioDoc.compute
        monkeypatch.setattr(DOC.PartStudioDoc, "compute", lambda self, *a, **k: n.append(1) or real(self, *a, **k))
        e.submit_edit(tab, {"op": "make_folder", "ids": v[:2], "name": "A"})
        fo = next(iter(folder_of(e, tab)[1]))
        e.submit_edit(tab, {"op": "rename_folder", "folder": fo, "name": "B"})
        e.submit_edit(tab, {"op": "set_folder_open", "folder": fo, "value": False})
        e.submit_edit(tab, {"op": "ungroup_folder", "folder": fo})
        assert n == []
        e.submit_edit(tab, {"op": "make_folder", "ids": v[:2], "name": "A"})
        fo = next(iter(folder_of(e, tab)[1]))
        e.submit_edit(tab, {"op": "set_folder_suppressed", "folder": fo, "value": True})
        assert len(n) == 1
    finally:
        e.close()


def test_auto_colour_sets_the_overrides_in_one_undo_and_survives_save_and_reload(studio, make_engine, tmp_path):
    e, tab, v, _ = studio
    pid = e.outputs(tab).parts[0].id
    base = tuple(e.outputs(tab).parts[0].color)
    e.submit_edit(tab, {"op": "set_part_override", "colors": {pid: (0.1, 0.2, 0.3)}})
    assert data(e, tab).get("overrides", {}) == {pid: {"color": [0.1, 0.2, 0.3]}}
    assert tuple(e.outputs(tab).parts[0].color) == pytest.approx((0.1, 0.2, 0.3))
    out = str(tmp_path / "saved.fshape")
    e.submit_edit(tab, {"op": "save", "path": out})
    again = make_engine()
    t2 = again.open_tab(out)
    assert tuple(again.outputs(t2).parts[0].color) == pytest.approx((0.1, 0.2, 0.3))
    undo(e, tab)
    assert data(e, tab).get("overrides", {}) == {} and tuple(e.outputs(tab).parts[0].color) == pytest.approx(base)


def test_a_name_or_colour_override_can_be_put_back(studio):
    e, tab, v, _ = studio
    pid = e.outputs(tab).parts[0].id
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "name": "Lid", "color": [1, 0, 0]})
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "color": None})
    assert data(e, tab).get("overrides", {}) == {pid: {"name": "Lid"}}
    e.submit_edit(tab, {"op": "set_part_override", "part": pid, "name": None})
    assert data(e, tab).get("overrides", {}) == {}


def test_no_direct_set_part_colors_call_in_the_ui():
    bad = []
    for dp, _d, files in os.walk(os.path.join(ROOT, "freeshape", "ui")):
        for fn in files:
            if fn.endswith(".py") and "set_part_colors" in open(os.path.join(dp, fn)).read():
                bad.append(fn)
    assert not bad


# =========================================================================== the window's mirror

def _f(i, folder=None, **p):
    f = {"id": f"F{i}", "type": "variable", "name": f"v{i}", "params": p}
    if folder:
        f["folder"] = folder
    return f


def _snap(feats, folders=None, overrides=None):
    return {"features": feats, "rollback": None, "folders": folders or {}, "overrides": overrides or {}}


def test_plan_edits_moves_between_folders_without_replacing_a_feature():
    from freeshape.ui.partstudio_engine import plan_edits
    feats = [_f(i) for i in range(1, 7)]
    have = _snap([dict(f, folder="D1") if f["id"] in ("F1", "F2", "F3") else dict(f) for f in feats],
                 {"D1": {"name": "A", "open": True}})
    want = _snap([dict(f, folder="D1") if f["id"] in ("F1", "F2") else
                  dict(f, folder="D2") if f["id"] in ("F3", "F4") else dict(f) for f in feats],
                 {"D1": {"name": "A", "open": True}, "D2": {"name": "B", "open": False}})
    edits = plan_edits(have, want)
    assert [e["op"] for e in edits] == ["ungroup_folder", "make_folder", "make_folder", "set_folder_open"]
    assert not {"replace_feature", "add_feature", "delete_features"} & {e["op"] for e in edits}


def test_plan_edits_sends_folders_colours_and_names():
    from freeshape.ui.partstudio_engine import plan_edits
    feats = [_f(i, "D3" if i in (2, 3) else None) for i in range(1, 5)]
    want = _snap(feats, {"D3": {"name": "G", "open": True}}, {"P1": {"name": "Lid", "color": (1.0, 0, 0)}})
    edits = plan_edits(_snap([_f(i) for i in range(1, 5)]), want)
    assert edits == [{"op": "make_folder", "ids": ["F2", "F3"], "name": "G", "folder": "D3"},
                     {"op": "set_part_override", "part": "P1", "name": "Lid", "color": [1.0, 0, 0]}]
    assert plan_edits(want, want) == []
    gone = plan_edits(want, _snap(feats, {"D3": {"name": "G", "open": True}}, {"P1": {"name": "Lid"}}))
    assert gone == [{"op": "set_part_override", "part": "P1", "color": None}]
    renamed = plan_edits(want, _snap(feats, {"D3": {"name": "H", "open": False}}, want["overrides"]))
    assert renamed == [{"op": "rename_folder", "folder": "D3", "name": "H"},
                       {"op": "set_folder_open", "folder": "D3", "value": False}]


def test_the_window_sends_folders_and_auto_colour_to_the_worker(tmp_path):
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.engine import process as P
    from freeshape.ui.mainwindow import MainWindow
    doc0 = block_doc()
    v = [add(doc0, "variable", name=f"v{k}", expr=str(k))["id"] for k in range(4)]
    path = str(tmp_path / "w.fshape")
    doc0.save(path)
    w = MainWindow(engine=True)
    try:
        w.open_any(path)
        eng, doc = w.studio_engine, w.doc
        assert eng.settle(180)

        def worker():
            return json.loads(bytes(eng.in_engine(doc, lambda e, t: e.tab_data(t))).decode("utf-8"))

        fo = doc.make_folder(v[:2], "Group")
        assert eng.settle(60)
        d = worker()
        assert d["folders"] == {fo: {"name": "Group", "open": True}}
        assert [f["id"] for f in d["features"] if f.get("folder") == fo] == v[:2]
        doc.set_folder_open(fo, False)
        assert eng.settle(60) and worker()["folders"][fo]["open"] is False
        w._auto_color(list(doc.state.parts))
        assert eng.settle(60)
        assert set(worker()["overrides"]) == set(doc.state.parts)
        doc.undo()
        assert eng.settle(60) and worker().get("overrides", {}) == {}
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
