"""I2.2: the engine matches and mirrors a source of another Document. A source with a `document` that is not this
Document's id never matches a local tab, even when its tab id is one (every Document's first tab is T1); the parent
resolves it through the LinkResolver at the pinned Version and hands the package to the consumer's worker, so the
worker opens no file of the other Document; the Link graph records it as a pinned input with a digest.

Entry points that start a ProcessEngine need a `__main__` guard (workers import the test module again); pytest
supplies it."""
import os
import shutil

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature, TOP
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import links as L
from freeshape.engine import ProcessEngine
from freeshape.engine import process as P
from freeshape.engine.encode import Encoder
from freeshape.engine.inputs import external_id

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def _box(studio, w, depth):
    sid = sketch_feature(studio, lambda s: rect_lines(s, 0, 0, w, w), TOP)
    add(studio, "extrude", regions=region_refs(studio, sid), depth=depth, op="new")
    assert not studio.errors, studio.errors


def _hash(part):
    return Encoder(meshes=False).brep(part.shape)[1]


class World:
    """Library (studio T1, a 20 mm box, Versions v1 and v2 with a taller box) and Project (its own studio T1, a
    7 mm box, and an assembly of one instance linked to Library's T1 at v1)."""

    def __init__(self, tmp_path, data=None):
        self.reg = R.DocRegistry(str(data or tmp_path / "data" / "documents.json"))
        lib = self.lib = DF.FolderDocument.create(str(tmp_path / "Library"), name="Library", registry=self.reg)
        st = lib.add_tab("studio", "Plate")
        _box(st.doc, 20.0, 10.0)
        self.pid = next(iter(st.doc.state.parts))
        self.v1 = _hash(st.doc.state.parts[self.pid])
        lib.create_version("v1", "first")
        ext = next(f for f in st.doc.features if f["type"] == "extrude")
        ext["params"]["depth"] = 25.0
        st.doc.replace_feature(ext)
        assert not st.doc.errors, st.doc.errors
        self.v2 = _hash(st.doc.state.parts[self.pid])
        lib.create_version("v2", "taller")
        proj = self.proj = DF.FolderDocument.create(str(tmp_path / "Project"), name="Project", registry=self.reg)
        mine = self.mine = proj.add_tab("studio", "Mine")
        _box(mine.doc, 7.0, 3.0)
        self.mine_pid = next(iter(mine.doc.state.parts))
        self.mine_hash = _hash(mine.doc.state.parts[self.mine_pid])
        asm = self.asm = proj.add_tab("assembly", "Asm")
        asm.doc.insert(L.studio_source(st, self.pid, document=lib.id, version="v1"), name="Plate")
        asm.doc.refresh(solve=False, notify=False)
        assert not asm.doc.errors, asm.doc.errors
        proj.save()
        assert mine.id == st.id == "T1"            # the ids collide: what makes the match go wrong
        self.src = asm.doc.instances[0]["source"]

    def engine(self):
        eng = ProcessEngine()
        eng.use_document(self.proj.id, self.reg)
        eng.open_tab(os.path.join(self.proj.path, self.mine.file), tab=self.mine.id)
        return eng, eng.open_tab(os.path.join(self.proj.path, self.asm.file), tab=self.asm.id)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_a_cross_document_source_is_not_the_local_tab_with_the_same_id(world):
    eng, asm = world.engine()
    try:
        assert world.src["tab"] == "T1" and world.src["document"] == world.lib.id
        assert eng._match(world.src) is None                     # not Project's T1
        out = eng.outputs(asm)
        assert not out.errors, out.errors
        assert [i.part for i in out.instances] == [world.v1] and world.v1 != world.mine_hash
    finally:
        eng.close()


def test_the_same_source_without_a_document_is_the_local_tab(world):
    eng, asm = world.engine()
    try:
        local = {k: v for k, v in world.src.items() if k not in ("document", "version")}
        assert eng._match(local) is eng._tabs["T1"]
        own = dict(world.src, document=world.proj.id)            # a pin to its own Document is local
        assert eng._match(own) is eng._tabs["T1"]
    finally:
        eng.close()


def _spy_opens(paths):
    import builtins as b
    real = b.open

    def spy(file, *a, **k):
        paths.append(os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else file)
        return real(file, *a, **k)
    b.open = spy


def _opens_seen():
    return list(_OPENED)


_OPENED = []


def _install_spy():
    _spy_opens(_OPENED)


def test_the_consumers_worker_opens_no_file_of_the_other_document(world):
    eng, asm = world.engine()
    try:
        eng.run_in_worker(asm, _install_spy)
        eng.submit_edit(asm, {"op": "refresh"})                  # the worker resolves the link again
        out = eng.outputs(asm)
        assert [i.part for i in out.instances] == [world.v1]
        seen = eng.run_in_worker(asm, _opens_seen)
        assert not [p for p in seen if isinstance(p, str) and os.path.realpath(world.lib.path) in os.path.realpath(p)]
    finally:
        eng.close()


def test_the_link_graph_records_a_pinned_input_and_no_live_edge(world):
    eng, asm = world.engine()
    try:
        eng.outputs(asm)
        pin = external_id(world.src)
        assert pin == f"{world.lib.id}@v1:T1"
        edges = eng.link_edges()
        assert (asm, pin) in edges and (asm, "T1") not in edges
        edge = eng.links.graph.inputs(asm)[pin]
        assert edge.kind == "studio" and edge.digest
        assert edge.keys == (world.pid,)
        assert eng.rebuild_dependents("T1") == []                # editing Project's T1 does not touch the pin
        assert eng.rebuild_count(asm) == 0
    finally:
        eng.close()


def test_update_link_to_v2_gives_the_consumer_v2_and_a_pin_to_v2(world):
    eng, asm = world.engine()
    try:
        assert [i.part for i in eng.outputs(asm).instances] == [world.v1]
        assert L.update_cross_links(world.proj) == 1 and world.src["version"] == "v2"
        world.proj.save()
        eng.close_tab(asm)                                        # what LiveTabs does when a tab's links change
        asm = eng.open_tab(os.path.join(world.proj.path, world.asm.file), tab=world.asm.id)
        out = eng.outputs(asm)
        assert not out.errors, out.errors
        assert [i.part for i in out.instances] == [world.v2] and world.v1 != world.v2
        assert [u for c, u in eng.link_edges() if c == asm] == [f"{world.lib.id}@v2:T1"]
        assert eng.rebuild_count(asm) == 0                        # the new worker built once, from the new pin
    finally:
        eng.close()


def test_a_source_of_a_document_the_registry_does_not_know_is_the_consumers_error(world):
    eng, asm = world.engine()
    try:
        bad = dict(world.src, document="no-such-document")
        assert eng._external(bad) is None and eng._match(bad) is None
    finally:
        eng.close()


def test_the_window_engine_pins_the_input_and_update_link_moves_it_to_v2(tmp_path, monkeypatch):
    """The window's engine copy of the consumer (LiveTabs): its Link graph lists the pin, and one update_link
    re-pins it, the engine copy follows and the pin to v2 replaces the pin to v1."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("FREESHAPE_NO_RECOVERY", "1")
    monkeypatch.setenv("FREESHAPE_NO_HOME", "1")
    monkeypatch.setenv("FREESHAPE_DATA_DIR", str(tmp_path / "appdata"))
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
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
    world = World(tmp_path, data=R.default_registry().path)
    w = MW.MainWindow(engine=True)
    w.settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    try:
        w.open_document(world.proj.path)
        assert w.studio_engine.settle(240) and not w.studio_engine.broken
        doc = w.folder.tab(world.asm.id).doc
        live = w.studio_engine.live
        pin = f"{world.lib.id}@v1:T1"
        edges = live.link_edges(doc)
        assert (world.asm.id, pin) in edges and (world.asm.id, "T1") not in edges
        src = doc.instances[0]["source"]
        assert w.versions_ui.update_links() == 1 and src["version"] == "v2"
        assert w.studio_engine.settle(240)
        edges = live.link_edges(doc)
        assert (world.asm.id, f"{world.lib.id}@v2:T1") in edges and (world.asm.id, pin) not in edges
        assert [i.part for i in w.studio_engine.in_engine(doc, lambda e, tab: e.outputs(tab)).instances] == [world.v2]
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
