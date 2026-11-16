"""I3.1: a Part Studio's outputs carry the feature tree: the features in file order (id, type, label, name,
suppressed, error, folder, hidden), the folders, the rollback position and whether undo / redo are available.
The browser client reads the tree from here; the desktop window ignores it. The fields are presentation only:
they never enter a Link content hash, so a rename or a folder never rebuilds a dependent tab.

Every test sets up its own tab and passes alone. The edit tests run on both executors (in-process and one worker
process per tab)."""
import json
import os
import shutil

import pytest

from conftest import EXAMPLES_DIR, add, block_doc, try_add
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import links as L
from freeshape.engine import FeatureOut, FolderOut, InProcessEngine, ProcessEngine, StudioOutputs, contract, wire
from freeshape.engine import process as P

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}
BRACKET = os.path.join(EXAMPLES_DIR, "documents", "bracket", "bracket.fshape")


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    e = ENGINES[request.param]()
    yield e
    e.close()


def bracket_tab(engine, tmp_path):
    path = str(tmp_path / "bracket.fshape")
    shutil.copy(BRACKET, path)
    return engine.open_tab(path)


def rows(out):
    return [(f.id, f.type, f.name) for f in out.features]


def by_id(out, fid):
    return next(f for f in out.features if f.id == fid)


# =========================================================================== the list
def test_bracket_lists_its_features_in_file_order_with_types_and_names(engine, tmp_path):
    tab = bracket_tab(engine, tmp_path)
    out = engine.outputs(tab)
    with open(BRACKET) as fh:
        stored = json.load(fh)["features"]
    assert rows(out) == [(f["id"], f["type"], f["name"]) for f in stored]
    assert [t for _, t, _ in rows(out)] == ["sketch", "extrude", "fillet", "sketch", "hole", "sketch", "extrude",
                                             "chamfer"]
    assert [n for _, _, n in rows(out)][:3] == ["Bracket profile", "Extrude 1", "Fillet 1"]
    assert all(isinstance(f, FeatureOut) for f in out.features)
    assert [f.label for f in out.features][:3] == ["Sketch", "Extrude", "Fillet"]
    assert not any(f.suppressed or f.error or f.folder for f in out.features)
    assert out.folders == [] and out.rollback == len(out.features) == 8


def test_label_is_the_one_the_desktop_names_new_features_from():
    from freeshape import features as F
    from freeshape.engine.encode import feature_label
    for ftype, label in F.FEATURE_LABELS.items():
        assert feature_label({"type": ftype, "params": {}}) == label
    boundary = {"type": "loft", "params": {"front": "boundary_surface"}}
    assert feature_label(boundary) == "Boundary surface"
    assert feature_label({"type": "plugin:x.y", "params": {}}) == "Plugin:X.Y"      # a type with no label


# =========================================================================== edits
def test_set_suppressed_shows_in_the_list(engine, tmp_path):
    tab = bracket_tab(engine, tmp_path)
    engine.submit_edit(tab, {"op": "set_suppressed", "feature": "F3", "value": True})
    out = engine.outputs(tab)
    assert by_id(out, "F3").suppressed is True
    assert [f.id for f in out.features if f.suppressed] == ["F3"]
    engine.submit_edit(tab, {"op": "set_suppressed", "feature": "F3", "value": False})
    assert not any(f.suppressed for f in engine.outputs(tab).features)


def test_set_rollback_moves_the_rollback_position(engine, tmp_path):
    tab = bracket_tab(engine, tmp_path)
    assert engine.outputs(tab).rollback == 8
    engine.submit_edit(tab, {"op": "set_rollback", "index": 3})
    out = engine.outputs(tab)
    assert out.rollback == 3 and len(out.features) == 8          # rolled-back features stay in the list
    assert len(out.parts) == 1                                   # the fillet is the last feature that builds
    engine.submit_edit(tab, {"op": "set_rollback", "index": None})
    assert engine.outputs(tab).rollback == 8
    engine.submit_edit(tab, {"op": "set_rollback", "index": 0})
    assert engine.outputs(tab).rollback == 0


def failing_studio(tmp_path):
    doc = block_doc()
    f, err = try_add(doc, "fillet")                              # no edges chosen: it cannot build
    assert err
    path = str(tmp_path / "failing.fshape")
    doc.save(path)
    return path, f["id"], err


def test_a_feature_that_fails_carries_its_message(engine, tmp_path):
    path, fid, err = failing_studio(tmp_path)
    tab = engine.open_tab(path)
    out = engine.outputs(tab)
    bad = by_id(out, fid)
    assert bad.error == err == out.errors[fid]                   # one source of truth: doc.errors
    assert [f.id for f in out.features if f.error] == [fid]
    assert all(f.error is None for f in out.features if f.id != fid)


def test_rename_and_folders(engine, tmp_path):
    tab = bracket_tab(engine, tmp_path)
    engine.submit_edit(tab, {"op": "rename_feature", "feature": "F2", "name": "Body"})
    assert by_id(engine.outputs(tab), "F2").name == "Body"
    engine.submit_edit(tab, {"op": "make_folder", "ids": ["F4", "F5"], "name": "Holes"})
    out = engine.outputs(tab)
    fo = by_id(out, "F4").folder
    assert fo and by_id(out, "F5").folder == fo and by_id(out, "F6").folder is None
    assert out.folders == [FolderOut(id=fo, name="Holes", open=True, suppressed=False, failed=0)]
    engine.submit_edit(tab, {"op": "set_folder_open", "folder": fo, "value": False})
    engine.submit_edit(tab, {"op": "set_folder_suppressed", "folder": fo, "value": True})
    out = engine.outputs(tab)
    assert out.folders[0].open is False and out.folders[0].suppressed is True
    assert by_id(out, "F4").suppressed and by_id(out, "F5").suppressed and not by_id(out, "F6").suppressed
    engine.submit_edit(tab, {"op": "ungroup_folder", "folder": fo})
    out = engine.outputs(tab)
    assert out.folders == [] and by_id(out, "F4").folder is None


def test_a_folder_counts_its_failed_members(engine, tmp_path):
    path, fid, _ = failing_studio(tmp_path)
    tab = engine.open_tab(path)
    engine.submit_edit(tab, {"op": "make_folder", "ids": [fid], "name": "Broken"})
    out = engine.outputs(tab)
    assert out.folders[0].failed == 1 and by_id(out, fid).folder == out.folders[0].id


# =========================================================================== undo and redo
def test_can_undo_is_false_on_open_true_after_an_edit_and_can_redo_after_undo(engine, tmp_path):
    tab = bracket_tab(engine, tmp_path)
    out = engine.outputs(tab)
    assert out.can_undo is False and out.can_redo is False
    engine.submit_edit(tab, {"op": "rename_feature", "feature": "F2", "name": "Body"})
    out = engine.outputs(tab)
    assert out.can_undo is True and out.can_redo is False
    engine.submit_edit(tab, {"op": "undo"})
    out = engine.outputs(tab)
    assert out.can_undo is False and out.can_redo is True
    assert by_id(out, "F2").name == "Extrude 1"
    engine.submit_edit(tab, {"op": "redo"})
    out = engine.outputs(tab)
    assert out.can_undo is True and out.can_redo is False and by_id(out, "F2").name == "Body"


# =========================================================================== the wire
def test_the_new_fields_round_trip_the_wire():
    path_doc = block_doc()
    f, _ = try_add(path_doc, "fillet")
    eng = InProcessEngine()
    try:
        tab = eng.open_tab_data("studio", path_doc.to_data())
        eng.submit_edit(tab, {"op": "make_folder", "ids": [f["id"]], "name": "Broken"})
        out = eng.outputs(tab)
    finally:
        eng.close()
    back = wire.from_wire(wire.to_wire(out))
    assert back.features == out.features and back.folders == out.folders
    assert (back.rollback, back.can_undo, back.can_redo) == (out.rollback, out.can_undo, out.can_redo)
    assert out.features[-1].error and out.folders[0].failed == 1 and out.can_undo


def test_a_payload_without_the_new_fields_decodes_with_their_defaults():
    """What an older server (or a recorded fixture) sent: no features, folders, rollback, can_undo, can_redo."""
    new = {"features", "folders", "rollback", "can_undo", "can_redo"}
    old = contract.StudioOutputs(tab="T1", kind="studio", name="n", revision=0, errors={}, consumes=())
    data = wire.to_wire(old)
    header = json.loads(data[8:8 + int.from_bytes(data[4:8], "little")])
    fields = header["root"]["f"]
    assert new <= set(fields)
    for k in new:
        del fields[k]
    text = json.dumps(header, separators=(",", ":")).encode()
    text += b" " * (-(8 + len(text)) % 8)
    stripped = data[:4] + len(text).to_bytes(4, "little") + text
    back = wire.from_wire(stripped)
    assert isinstance(back, StudioOutputs)
    assert (back.features, back.folders, back.rollback, back.can_undo, back.can_redo) == ([], [], 0, False, False)


# =========================================================================== Link hashes
def build_link(tmp_path):
    """Skeleton (a sketch and a block) and Base, which derives Skeleton's sketch: Base consumes Skeleton."""
    from conftest import rect_lines, region_refs, sketch_feature
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())
    sk = fd.add_tab("studio", "Skeleton")
    sid = sketch_feature(sk.doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    add(sk.doc, "extrude", regions=region_refs(sk.doc, sid), depth=5, op="new")
    base = fd.add_tab("studio", "Base")
    d = add(base.doc, "derived", source=L.studio_source(sk), parts=[], sketches=[sid], connectors=[])
    add(base.doc, "extrude", regions=region_refs(base.doc, f"{d['id']}:{sid}"), depth=3, op="new")
    fd.save()
    return fd, sk.id, base.id


def test_tree_presentation_edits_neither_change_the_link_digest_nor_rebuild_dependents(tmp_path):
    fd, sk, base = build_link(tmp_path)
    eng = ProcessEngine()
    try:
        for t in fd.tabs:
            eng.open_tab(os.path.join(fd.path, t.file), tab=t.id)
        eng.outputs(base)
        edge = eng.links.graph.inputs(base)[sk]
        assert eng._digest_now(sk, edge) == edge.digest
        counts, tree0 = eng.rebuild_counts(), eng.outputs(sk)
        for edit in ({"op": "rename_feature", "feature": "F2", "name": "Block"},
                     {"op": "make_folder", "ids": ["F1", "F2"], "name": "Base"},
                     {"op": "set_folder_open", "folder": "D1", "value": False},
                     {"op": "rename_folder", "folder": "D1", "name": "Layout"},
                     {"op": "undo"}, {"op": "redo"}):
            eng.submit_edit(sk, edit)
            assert eng._digest_now(sk, edge) == edge.digest, edit
            assert eng.rebuild_counts() == counts and eng.rebuild_order() == [], edit
        tree1 = eng.outputs(sk)
        assert (tree1.features, tree1.folders, tree1.can_undo) != (tree0.features, tree0.folders, tree0.can_undo)
        assert [p.hash for p in tree1.parts] == [p.hash for p in tree0.parts]
    finally:
        eng.close()


def test_the_link_package_does_not_read_the_tree_fields():
    """The digest is computed from the document (linkgraph.live_package / lean_package), never from the
    StudioOutputs, so adding fields to the outputs cannot move it."""
    import inspect
    from freeshape.engine import linkgraph
    src = inspect.getsource(linkgraph)
    for name in ("StudioOutputs", "FeatureOut", "FolderOut", "can_undo", "can_redo", ".features", "rollback"):
        assert name not in src, name


# =========================================================================== the desktop
def test_the_desktop_window_in_engine_mode_ignores_the_tree_fields(tmp_path, monkeypatch):
    """The window gets the same StudioOutputs from its worker, tree included, and builds, edits and lists its
    features exactly as before: its feature panel is filled from its own Document."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    start = block_doc()
    path = str(tmp_path / "block.fshape")
    start.save(path)                                         # a plain file: no Document, so no dialog on open
    w = MainWindow(engine=True)
    try:
        w.open_any(path)
        eng, doc = w.studio_engine, w.doc
        assert eng.settle(180)
        out = eng.in_engine(doc, lambda e, t: e.outputs(t))
        assert isinstance(out, StudioOutputs) and [f.id for f in out.features] == [f["id"] for f in doc.features]
        names = {f["name"] for f in doc.features}
        shown = [w.left.features.item(i).text() for i in range(w.left.features.count())]
        assert [f["name"] for f in doc.features] == [t for t in shown if t in names]
        doc.set_suppressed("F2", True)                       # the window's own edit reaches the worker as before
        assert eng.settle(60)
        out = eng.in_engine(doc, lambda e, t: e.outputs(t))
        assert [f.id for f in out.features if f.suppressed] == ["F2"] and out.can_undo
        assert not doc.state.parts and not doc.errors
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
        w.deleteLater()
