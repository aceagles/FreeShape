"""An old Context converts to instance ids at its first Update context (I6.2, ADR 0004).

A Context saved before I6.1 has no "ids" param: its bodies are `<fid>/<k>` by position in the capture stored in
the file. Nothing is converted at load. Update context maps each `<fid>/<k>` to `<fid>/<instance id>` from that
stored capture, renames every reference to it in the studio (feature params, part overrides, hidden parts) and
sets ids "instance", all in the one undoable edit; when the stored capture can't be read it refuses and changes
nothing."""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from freeshape import document as D  # noqa: E402
from freeshape import doc_folder as DF  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402
from test_sketch_inference import _sketcher  # noqa: E402
from tests.test_context_ids import _extrude_up_to_b, _folder, _zmin  # noqa: E402

RED = (0.9, 0.1, 0.1)
TOP = {"kind": "std", "name": "Top"}


class _Viewport:
    def __init__(self, state):
        self.part_shapes = {pid: p.shape for pid, p in state.parts.items()}


def _use_bottom_edge(doc, pid):
    """A sketch on Top holding one line projected (the sketcher's Use tool) from a bottom edge of part `pid`."""
    shape = doc.state.parts[pid].shape
    z0 = float(K.bbox(shape)[0][2])
    idx = next(i for i, e in enumerate(K.edges(shape))
               if (lambda i: i["type"] == int(K.GeomAbs_Line) and abs(i["c"][2] - z0) < 1e-6
                   and abs(abs(i["d"][0]) - 1) < 1e-6)(K.edge_info(e)))
    s = _sketcher()
    s.base_state = doc.state
    eid = s._use_edge(_Viewport(doc.state), Pick("edge", (pid, idx)))
    assert eid in s.sk.entities and s.sk.entities[eid]["src"]["part"] == pid
    assert s.sk.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.sk.to_dict())
    doc.add_feature(f)
    return f["id"], eid


def _old_studio(tmp_path, same):
    """`_folder` with an old Context (no ids key) and four references on its second body `<fid>/2` (B): a hidden
    flag, a colour override, an extrude up to B's bottom face and a sketch line used from a bottom edge of B.
    Returns (folder, plate tab, asm tab, context id, instance ids, extrude id, (sketch id, entity id))."""
    folder, plate, asm, cid, iids = _folder(tmp_path, same, ids=None)
    doc, b = plate.doc, f"{cid}/2"
    assert abs(_zmin(doc, b) - 40) < 1e-6                 # the second neighbour is B
    doc.hidden_parts = {b}
    doc.set_part_colors({b: RED})
    eid = _extrude_up_to_b(doc, cid, "2")
    used = _use_bottom_edge(doc, b)
    assert not doc.errors, doc.errors
    return folder, plate, asm, cid, iids, eid, used


def _refs(doc, cid, eid, used):
    """The part each of the four references names: (hidden, overrides, extrude up-to part, sketch Use part)."""
    sid, ent = used
    return (sorted(doc.hidden_parts), sorted(doc.part_overrides),
            doc.feature(eid)["params"]["up_to"]["ref"]["part"],
            doc.feature(sid)["params"]["sketch"]["entities"][ent]["src"]["part"])


def _bar_top(doc, eid):
    pid = next(p for p in doc.state.parts if p.startswith(eid + "/"))
    return float(K.bbox(doc.state.parts[pid].shape)[1][2])


def _on_b(doc, pid, eid, used):
    """All four references sit on the body `pid`, and that body is B (bottom face at z 40)."""
    sid, ent = used
    assert abs(_zmin(doc, pid) - 40) < 1e-6
    assert pid in doc.hidden_parts and tuple(doc.state.parts[pid].color) == RED
    assert abs(_bar_top(doc, eid) - 40) < 1e-6
    src = doc.feature(sid)["params"]["sketch"]["entities"][ent]["src"]
    got, edge = F.resolve(doc.state, src, "edge")
    assert got == pid and abs(float(K.bbox(edge)[0][2]) - 40) < 1e-6


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def _names_old(doc, old):
    """True when anything in the studio's saved data still names the part id `old` (or a piece of it)."""
    data = {"features": doc.features, "overrides": doc.part_overrides, "hidden": sorted(doc.hidden_parts)}
    return any(s == old or s.startswith(old + ".") for s in _strings(data))


# =========================================================================== load and save convert nothing

@pytest.mark.parametrize("same", [False, True], ids=["different_parts", "same_part"])
def test_old_studio_loads_and_saves_with_its_positional_ids(tmp_path, same):
    folder, plate, _asm, cid, _iids, eid, used = _old_studio(tmp_path, same)
    b = f"{cid}/2"
    want = ([b], [b], b, b)
    assert _refs(plate.doc, cid, eid, used) == want
    folder.save()
    again = DF.FolderDocument.open(folder.path)
    doc = again.tab(plate.id).doc
    assert "ids" not in doc.feature(cid)["params"] and not doc.errors, doc.errors
    assert _refs(doc, cid, eid, used) == want
    _on_b(doc, b, eid, used)                               # the same body, B, as before this card
    again.save()                                           # saving without Update context changes no id
    third = DF.FolderDocument.open(folder.path)
    doc3 = third.tab(plate.id).doc
    assert "ids" not in doc3.feature(cid)["params"]
    assert _refs(doc3, cid, eid, used) == want


# =========================================================================== Update context converts

@pytest.mark.parametrize("same", [False, True], ids=["different_parts", "same_part"])
def test_update_after_deleting_the_first_neighbour_keeps_all_four_on_b(tmp_path, same):
    folder, plate, asm, cid, iids, eid, used = _old_studio(tmp_path, same)
    doc, old = plate.doc, f"{cid}/2"
    asm.doc.delete_instances([iids["A"]])
    assert folder.context_status(plate.id, cid) == "out of date"
    ids = folder.update_context(plate.id, cid)
    assert ids == {f"{cid}/{k}": f"{cid}/{iids[n]}" for k, n in ((1, "A"), (2, "B"), (3, "C"))}
    assert folder.context_status(plate.id, cid) == "current" and not doc.errors, doc.errors
    b = f"{cid}/{iids['B']}"
    assert doc.feature(cid)["params"]["ids"] == "instance"
    assert _refs(doc, cid, eid, used) == ([b], [b], b, b)
    _on_b(doc, b, eid, used)
    assert not _names_old(doc, old) and old not in doc.state.parts
    c = f"{cid}/{iids['C']}"
    assert c not in doc.hidden_parts and tuple(doc.state.parts[c].color) != RED
    folder.save()
    again = DF.FolderDocument.open(folder.path)
    d2 = again.tab(plate.id).doc
    assert not d2.errors and _refs(d2, cid, eid, used) == ([b], [b], b, b)
    _on_b(d2, b, eid, used)


def test_a_second_update_converts_nothing_more(tmp_path):
    folder, plate, asm, cid, iids, eid, used = _old_studio(tmp_path, False)
    assert folder.update_context(plate.id, cid)
    before = json.loads(json.dumps(plate.doc.features))
    assert folder.update_context(plate.id, cid) == {}        # ids "instance" now: a plain recapture
    after = json.loads(json.dumps(plate.doc.features))
    assert [f for f in after if f["id"] != cid] == [f for f in before if f["id"] != cid]
    assert plate.doc.feature(cid)["params"]["ids"] == "instance"


def test_undo_restores_positional_ids_and_old_capture_and_redo_converts_again(tmp_path):
    folder, plate, asm, cid, iids, eid, used = _old_studio(tmp_path, True)
    doc, old = plate.doc, f"{cid}/2"
    old_key = doc.feature(cid)["params"]["capture"]
    snap = doc._snapshot()
    asm.doc.delete_instances([iids["A"]])
    folder.update_context(plate.id, cid)
    converted = doc._snapshot()
    b = f"{cid}/{iids['B']}"
    doc.undo()                                             # one edit: the conversion and the recapture together
    assert doc._snapshot() == snap and not doc.errors
    assert "ids" not in doc.feature(cid)["params"] and doc.feature(cid)["params"]["capture"] == old_key
    assert _refs(doc, cid, eid, used) == ([old], [old], old, old)
    _on_b(doc, old, eid, used)
    doc.redo()
    assert doc._snapshot() == converted and not doc.errors
    assert doc.feature(cid)["params"]["ids"] == "instance" and _refs(doc, cid, eid, used) == ([b], [b], b, b)
    _on_b(doc, b, eid, used)


@pytest.mark.parametrize("how", ["missing", "damaged"])
def test_unreadable_listing_refuses_and_changes_nothing(tmp_path, how):
    folder, plate, asm, cid, iids, eid, used = _old_studio(tmp_path, False)
    doc = plate.doc
    key = doc.feature(cid)["params"]["capture"]
    if how == "missing":
        doc._blob_bytes.pop(key)                          # never saved: no store holds it either
    else:
        doc._blob_bytes[key] = b"not a listing"
    asm.doc.delete_instances([iids["A"]])
    snap, undo, held = doc._snapshot(), len(doc._undo), set(doc._blob_bytes)
    with pytest.raises(K.KernelError) as e:
        folder.update_context(plate.id, cid)
    assert str(e.value) == D.CONTEXT_UNREADABLE == ("This Context's stored capture can't be read, so its bodies "
                                                    "can't be re-identified. Delete the Context and use Edit in "
                                                    "context again.")
    assert doc._snapshot() == snap and len(doc._undo) == undo and set(doc._blob_bytes) == held


# =========================================================================== the reference walker

IDS = {"F7/1": "F7/I1", "F7/2": "F7/I3"}
R = {"part": "F7/2", "name": "F7/2:face"}                 # a face/edge reference (its name is not an id)
NR = {"part": "F7/I3", "name": "F7/2:face"}

# (what, feature type, params before, params after): every param shape the walker rewrites, and ones it leaves
WALKER_TABLE = [
    ("face and edge references", "fillet", {"edges": [R, {"part": "P1/1"}]}, {"edges": [NR, {"part": "P1/1"}]}),
    ("a severed piece", "fillet", {"edges": [{"part": "F7/2.3.1"}]}, {"edges": [{"part": "F7/I3.3.1"}]}),
    ("an extrude's scope", "extrude", {"scope": ["F7/1", "F3/1"]}, {"scope": ["F7/I1", "F3/1"]}),
    ("an extrude up to a face", "extrude", {"up_to": {"kind": "face", "ref": R}},
     {"up_to": {"kind": "face", "ref": NR}}),
    ("an extrude up to a part", "extrude", {"up_to": {"kind": "part", "id": "F7/2"}},
     {"up_to": {"kind": "part", "id": "F7/I3"}}),
    ("a boolean's tools and targets", "boolean", {"op": "subtract", "tools": ["F7/2"], "targets": ["F7/1", "F2/1"]},
     {"op": "subtract", "tools": ["F7/I3"], "targets": ["F7/I1", "F2/1"]}),
    ("a mirror's parts", "mirror", {"parts": ["F7/2"], "plane": {"kind": "face", **R}},
     {"parts": ["F7/I3"], "plane": {"kind": "face", **NR}}),
    ("a linear pattern's parts", "linear_pattern", {"parts": ["F7/1"]}, {"parts": ["F7/I1"]}),
    ("a circular pattern's parts", "circular_pattern", {"parts": ["F7/2"]}, {"parts": ["F7/I3"]}),
    ("a split's parts and tools", "split",
     {"parts": ["F7/1"], "tools": [{"kind": "face", "ref": R, "both": True}, {"kind": "plane", "ref": TOP}]},
     {"parts": ["F7/I1"], "tools": [{"kind": "face", "ref": NR, "both": True}, {"kind": "plane", "ref": TOP}]}),
    ("a mate connector's ref and owner", "mate_connector", {"ref": R, "owner": "F7/2"},
     {"ref": NR, "owner": "F7/I3"}),
    ("a mate connector owned by a sketch", "mate_connector", {"ref": {"origin": True}, "owner": "F5"},
     {"ref": {"origin": True}, "owner": "F5"}),
    ("plane references", "plane", {"refs": [R, {"kind": "std", "name": "Top"}]},
     {"refs": [NR, {"kind": "std", "name": "Top"}]}),
    ("a sweep path", "sweep", {"path": [R]}, {"path": [NR]}),
    ("delete, move and rib parts", "move", {"parts": ["F7/2", "F7/1.2"]}, {"parts": ["F7/I3", "F7/I1.2"]}),
    ("a sketch entity's Use source", "sketch",
     {"sketch": {"entities": {"e1": {"type": "line", "src": R}}, "points": {"p1": [0, 0]}}},
     {"sketch": {"entities": {"e1": {"type": "line", "src": NR}}, "points": {"p1": [0, 0]}}}),
    ("not ids: other fids, near misses, expressions", "extrude",
     {"scope": ["F7/20", "F7/2x", "F77/2"], "depth": "F7/2", "name": "F7/2"},
     {"scope": ["F7/20", "F7/2x", "F77/2"], "depth": "F7/2", "name": "F7/2"}),
    ("a Derived names its source's ids", "derived", {"source": {"part": "F7/2"}, "parts": ["F7/2"]},
     {"source": {"part": "F7/2"}, "parts": ["F7/2"]}),
    ("a Context names an instance", "context", {"instance": "F7/2", "capture": "k"},
     {"instance": "F7/2", "capture": "k"}),
]


@pytest.mark.parametrize("what,ftype,before,after", WALKER_TABLE, ids=[t[0] for t in WALKER_TABLE])
def test_walker_rewrites_each_param_shape(what, ftype, before, after):
    f = {"id": "F9", "type": ftype, "name": "x", "params": before, "folder": "D1"}
    frozen = json.dumps(f, sort_keys=True)
    got = F.rename_part_refs(f, IDS)
    assert got["params"] == after
    assert {k: v for k, v in got.items() if k != "params"} == {k: v for k, v in f.items() if k != "params"}
    assert json.dumps(f, sort_keys=True) == frozen        # the input feature is not changed


def test_walker_table_covers_every_part_id_key():
    covered = {k for _w, _t, before, _a in WALKER_TABLE for k in _strings(before) if k in F.PART_ID_KEYS}
    assert covered == set(F.PART_ID_KEYS)
    assert {"boolean", "linear_pattern", "circular_pattern", "split", "mate_connector"} <= {t for _w, t, _b, _a
                                                                                          in WALKER_TABLE}


# =========================================================================== the window

@pytest.fixture
def window(tmp_path, monkeypatch):
    from tests.test_context_ui import make_rig
    r = make_rig(tmp_path, monkeypatch)
    yield r
    r.w.folder = None
    r.w.deleteLater()


def test_edit_in_context_on_an_old_context_converts_it_and_the_session_transparent_set(window):
    """Edit in context on an instance that already has an old Context updates it (assembly_ui), which converts it:
    the window's transparent set and the viewport's Context body ids (I6.4's pick rule) follow."""
    from conftest import add
    from freeshape import assembly as A
    r = window
    a, studio = r.asm.doc, r.plate.doc
    cid = add(studio, "context", assembly=a.uid, instance=r.ip,
              capture=studio.store_context(A.capture_context(a, r.ip)))["id"]
    order = [n["id"] for n in F.context_listing(studio._blob_get, studio.feature(cid)["params"]["capture"])
             ["neighbours"]]
    assert order == [r.ia, r.ib, r.ic]
    r.w._transparent[studio] = {f"{cid}/2"}
    studio.hidden_parts = {f"{cid}/3"}
    assert r.w.asm.edit_in_context() is None
    assert r.w.doc is studio and studio.feature(cid)["params"]["ids"] == "instance"
    assert r.w._transparent[studio] == {f"{cid}/{r.ib}"} and studio.hidden_parts == {f"{cid}/{r.ic}"}
    assert f"{cid}/{r.ib}" in r.w.viewport.transparent_ids
    assert {f"{cid}/{i}" for i in (r.ia, r.ib)} <= r.w.viewport.reference_ids     # C is hidden, so not drawn
    assert not _names_old(studio, f"{cid}/2") and not _names_old(studio, f"{cid}/3")


# =========================================================================== the per-tab engine follows

def test_window_engine_ends_with_the_same_rewritten_params(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from freeshape.engine import process as P
    from freeshape.ui import home as H
    from freeshape.ui import mainwindow as MW
    app = QApplication.instance() or QApplication([])
    os.makedirs(tmp_path / "autosave", exist_ok=True)
    os.makedirs(tmp_path / "thumbs", exist_ok=True)
    monkeypatch.setattr(MW.MainWindow, "autosave_root", staticmethod(lambda: str(tmp_path / "autosave")))
    monkeypatch.setattr(MW.MainWindow, "autosave_dir", classmethod(lambda cls: str(tmp_path / "autosave")))
    monkeypatch.setattr(H, "thumbs_dir", lambda: str(tmp_path / "thumbs"))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
    folder, plate, asm, cid, iids, eid, used = _old_studio(tmp_path / "src", False)
    folder.save()
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    try:
        w.open_document(folder.path)
        eng = w.studio_engine
        assert eng.settle(240) and not eng.broken
        doc, adoc = w.folder.tab(plate.id).doc, w.folder.tab(asm.id).doc
        if w._tab_index(doc) < 0:
            w._add_tab(doc)
        w.tabs.setCurrentIndex(w._tab_index(doc))
        app.processEvents()
        assert w.doc is doc
        w._transparent[doc] = {f"{cid}/2", f"{cid}/3"}
        adoc.delete_instances([iids["A"]])
        w.update_context(cid)
        assert eng.settle(240)
        print("studio_engine.broken =", eng.broken)
        assert not eng.broken
        b, c = f"{cid}/{iids['B']}", f"{cid}/{iids['C']}"
        assert doc.feature(cid)["params"]["ids"] == "instance" and _refs(doc, cid, eid, used) == ([b], [b], b, b)
        assert w._transparent[doc] == {b, c} and w.viewport.transparent_ids >= {b, c}
        mine = {p for p in w.viewport.reference_ids if p.startswith(f"{cid}/")}
        assert mine == {c}                                 # B is hidden, so only C is drawn, under its new id
        assert not any(p in w.viewport.reference_ids for p in (f"{cid}/1", f"{cid}/2", f"{cid}/3"))
        from freeshape import docstore
        sink = docstore.HeldBlobs({})                      # as partstudio_engine reads tab_data
        worker = json.loads(bytes(eng.in_engine(doc, lambda e, t: e.tab_data(t, blobs=sink))).decode("utf-8"))
        assert doc.feature(cid)["params"]["capture"] in sink.held     # the new capture reached the worker
        bare = json.loads(json.dumps([{k: v for k, v in f.items() if k != "folder"} for f in doc.features]))
        assert [{k: v for k, v in f.items() if k != "folder"} for f in worker["features"]] == bare
        assert set(worker.get("overrides", {})) == {b}
        assert not _names_old(doc, f"{cid}/2")
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
