"""I1.5: configurations in the engine and in link packages. A Part Studio's configuration table and its active row
are engine edits (`set_configs`, `set_active_config`) that the worker applies and the window's mirror
(partstudio_engine.plan_edits) sends; a link source that names a configuration ("config") reads the studio in that
configuration, through a package of its own (links.StudioPackage.config, a link key "<part>@<configuration>"), live,
across Documents and at a pinned Version. A source with no configuration reads what it always did.

Entry points that start a ProcessEngine need a `__main__` guard (workers import the test module again); pytest
supplies it."""
import json
import os
import shutil

import pytest

from conftest import TOP, add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape import links as L
from freeshape.document import Document
from freeshape.engine import EngineError, InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine.inputs import external_id
from freeshape.engine.linkgraph import key_configs, lean_package, link_key, live_package, package_digest

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}
AREA = 200.0                                     # the spacer's 20 x 10 profile
ROWS = [{"name": "Short", "vars": {"len": "10"}}, {"name": "Long", "vars": {"len": "20"}}]
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


@pytest.fixture(params=sorted(ENGINES))
def kind(request):
    return request.param


@pytest.fixture
def engines():
    made = []

    def make(kind, document=None, registry=None):
        made.append(ENGINES[kind]())
        if document is not None:
            made[-1].use_document(document, registry)
        return made[-1]

    yield make
    for e in made:
        e.close()


def spacer(doc):
    """A 20 x 10 block extruded by the variable #len (40)."""
    add(doc, "variable", name="len", expr="40")
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10), TOP)
    add(doc, "extrude", regions=region_refs(doc, sid), depth="#len", op="new")
    assert not doc.errors, doc.errors
    return next(iter(doc.state.parts))


def studio_volume(e, tab):
    return sum(K.volume(K.shape_from_brep(p.brep)) for p in e.outputs(tab).parts)


def instance_volumes(e, asm):
    out = e.outputs(asm)
    assert not out.errors, out.errors
    return [round(K.volume(K.shape_from_brep(out.parts[i.part].brep))) for i in out.instances]


def instance_parts(e, asm):
    return {i.source.get("config"): i.part for i in e.outputs(asm).instances}


def engine_digest(e, kind, tab, key):
    """The digest of what a link reading `key` takes of studio `tab`, as the engine works it out."""
    if kind == "inprocess":
        return package_digest("studio", live_package("studio", e._get(tab).doc, e.encoder, [key]), [key])
    return package_digest("studio", lean_package(e._lean(e._tabs[tab], (), key_configs([key]))[1]), [key])


class World:
    """A Document with a Part Studio (the spacer) and an Assembly holding one instance of its part per entry of
    `configs` (None: no configuration named)."""

    def __init__(self, tmp_path, configs, rows=ROWS):
        os.makedirs(tmp_path, exist_ok=True)
        self.reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
        doc = self.doc = DF.FolderDocument.create(str(tmp_path / "Project"), name="Project", registry=self.reg)
        st = self.st = doc.add_tab("studio", "Spacer")
        self.pid = spacer(st.doc)
        if rows:
            st.doc.set_configs(rows, 0)
        self.asm = doc.add_tab("assembly", "Asm")
        self.srcs = [self.source(c) for c in configs]
        for s in self.srcs:
            self.asm.doc.insert(s, name="Spacer", checkpoint=False)
        doc.save()

    def source(self, config=None, **pin):
        src = L.studio_source(self.st, self.pid)
        src.update(pin)
        if config:
            src["config"] = config
        return src

    def open(self, e, studio=True):
        """(studio tab, assembly tab) opened from the Document's files under their tab ids."""
        st = e.open_tab(os.path.join(self.doc.path, self.st.file), tab=self.st.id) if studio else None
        return st, e.open_tab(os.path.join(self.doc.path, self.asm.file), tab=self.asm.id)


# =========================================================================== the engine edits

def test_switching_the_active_configuration_rebuilds_and_one_undo_restores(engines, kind, tmp_path):
    doc = Document()
    spacer(doc)
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    e = engines(kind)
    tab = e.open_tab(path)
    assert abs(studio_volume(e, tab) - AREA * 40) < 1e-6
    e.submit_edit(tab, {"op": "set_configs", "rows": ROWS, "active": 0})
    assert abs(studio_volume(e, tab) - AREA * 10) < 1e-6
    e.submit_edit(tab, {"op": "set_active_config", "config": "Long"})
    assert abs(studio_volume(e, tab) - AREA * 20) < 1e-6
    assert e.outputs(tab).variables["len"] == 20
    e.submit_edit(tab, {"op": "undo"})
    assert abs(studio_volume(e, tab) - AREA * 10) < 1e-6
    e.submit_edit(tab, {"op": "set_active_config", "config": 1})
    assert abs(studio_volume(e, tab) - AREA * 20) < 1e-6
    saved = json.loads(bytes(e.tab_data(tab)).decode("utf-8"))["configs"]
    assert [r["name"] for r in saved["rows"]] == ["Short", "Long"] and saved["active"] == 1
    e.submit_edit(tab, {"op": "undo"})
    e.submit_edit(tab, {"op": "undo"})                      # the table itself was one step
    assert abs(studio_volume(e, tab) - AREA * 40) < 1e-6


def test_configuration_edits_refuse_what_is_not_there(engines, tmp_path):
    doc = Document()
    spacer(doc)
    path = str(tmp_path / "s.fshape")
    doc.save(path)
    e = engines("inprocess")
    tab = e.open_tab(path)
    e.submit_edit(tab, {"op": "set_configs", "rows": ROWS})
    for bad in ({"op": "set_active_config", "config": "Huge"}, {"op": "set_active_config", "config": 2},
                {"op": "set_configs", "rows": ROWS, "active": 2},
                {"op": "set_configs", "rows": [{"vars": {}}]},
                {"op": "set_configs", "rows": [ROWS[0], ROWS[0]]}):
        with pytest.raises(EngineError):
            e.submit_edit(tab, bad)
    assert abs(studio_volume(e, tab) - AREA * 10) < 1e-6


# =========================================================================== links in a configuration

def test_two_instances_one_per_configuration_resolve_two_volumes(engines, kind, tmp_path):
    w = World(tmp_path, ["Short", "Long"])
    e = engines(kind, w.doc.id, w.reg)
    st, asm = w.open(e)
    assert instance_volumes(e, asm) == [round(AREA * 10), round(AREA * 20)]
    # two packages, two digests: in the links ...
    short, long_ = (w.doc.resolver.resolve_studio(s) for s in w.srcs)
    plain = w.doc.resolver.resolve_studio(w.source())
    assert (short.config, long_.config, plain.config) == ("Short", "Long", "")
    assert len({short.digest, long_.digest, plain.digest}) == 3
    # ... and in the engine, which records one edge reading the part in both configurations
    edge = e.links.graph.inputs(asm)[st]
    assert sorted(edge.keys) == sorted(link_key(s) for s in w.srcs)
    assert engine_digest(e, kind, st, link_key(w.srcs[0])) != engine_digest(e, kind, st, link_key(w.srcs[1]))
    # each configuration is invalidated on its own: changing Long leaves Short's part as it was
    parts, n = instance_parts(e, asm), e.rebuild_count(asm)
    e.submit_edit(st, {"op": "set_configs", "rows": [ROWS[0], {"name": "Long", "vars": {"len": "30"}}]})
    assert instance_volumes(e, asm) == [round(AREA * 10), round(AREA * 30)]
    after = instance_parts(e, asm)
    assert after["Short"] == parts["Short"] and after["Long"] != parts["Long"]
    assert e.rebuild_count(asm) == n + 1
    # switching the studio's active row changes neither configuration: the assembly is not rebuilt
    e.submit_edit(st, {"op": "set_active_config", "config": "Long"})
    assert instance_parts(e, asm) == after and e.rebuild_count(asm) == n + 1


def test_a_source_with_no_configuration_keeps_its_digest(engines, kind, tmp_path):
    w = World(tmp_path, [None], rows=None)
    src = w.srcs[0]
    assert "config" not in src and link_key(src) == w.pid
    before = w.doc.resolver.resolve_studio(src).digest           # the default state, before any configuration
    e = engines(kind, w.doc.id, w.reg)
    st, asm = w.open(e)
    edge_before = e.links.graph.inputs(asm)[st].digest
    view = (lean_package(e._lean(e._tabs[st], ())[1]) if kind == "process"
            else live_package("studio", e._get(st).doc, e.encoder))
    assert "configs" not in view and edge_before == package_digest("studio", view, [w.pid])
    n = e.rebuild_count(asm)
    # a table whose active row is the default values: what a source with no configuration reads is unchanged
    rows = [{"name": "Default", "vars": {"len": "40"}}, {"name": "Long", "vars": {"len": "80"}}]
    e.submit_edit(st, {"op": "set_configs", "rows": rows})
    e.outputs(asm)
    assert e.links.graph.inputs(asm)[st].digest == edge_before and e.rebuild_count(asm) == n
    w.st.doc.set_configs(rows, 0)
    pkg = w.doc.resolver.resolve_studio(src)
    assert pkg.config == "" and pkg.digest == before
    assert instance_volumes(e, asm) == [round(AREA * 40)]
    # ... and it still follows the studio: once the active row changes the part, the digest moves with it
    e.submit_edit(st, {"op": "set_active_config", "config": "Long"})
    assert instance_volumes(e, asm) == [round(AREA * 80)] and e.links.graph.inputs(asm)[st].digest != edge_before
    w.st.doc.set_active_config("Long")
    assert w.doc.resolver.resolve_studio(src).digest != before


@needs_git
def test_a_cross_document_link_reads_each_configuration_at_its_version(engines, kind, tmp_path):
    lib = World(tmp_path / "lib", [], rows=ROWS)
    lib.doc.create_version("v1", "two lengths")
    lib.st.doc.set_configs([{"name": "Short", "vars": {"len": "5"}}, {"name": "Long", "vars": {"len": "50"}}])
    lib.doc.save()                                               # the live Library moved on; v1 did not
    proj = DF.FolderDocument.create(str(tmp_path / "Project2"), name="Project2", registry=lib.reg)
    asm = proj.add_tab("assembly", "Asm")
    srcs = [lib.source(c, document=lib.doc.id, version="v1") for c in ("Short", "Long")]
    for s in srcs:
        asm.doc.insert(s, name="Spacer", checkpoint=False)
    proj.save()
    e = engines(kind, proj.id, lib.reg)
    tab = e.open_tab(os.path.join(proj.path, asm.file), tab=asm.id)
    assert instance_volumes(e, tab) == [round(AREA * 10), round(AREA * 20)]
    pkgs = [proj.resolver.resolve_studio(s) for s in srcs]
    assert [p.config for p in pkgs] == ["Short", "Long"] and pkgs[0].digest != pkgs[1].digest
    assert external_id(srcs[0]) != external_id(srcs[1])
    if kind == "process":
        (i0, p0, _), (i1, p1, _) = e._external(srcs[0]), e._external(srcs[1])
        assert (i0, i1) == (external_id(srcs[0]), external_id(srcs[1]))
        assert package_digest("studio", lean_package(p0), [link_key(srcs[0])]) != \
            package_digest("studio", lean_package(p1), [link_key(srcs[1])])


@needs_git
def test_a_pinned_version_of_the_document_reads_each_configuration(engines, kind, tmp_path):
    w = World(tmp_path, [])
    w.doc.create_version("v1", "two lengths")
    w.st.doc.set_configs([{"name": "Short", "vars": {"len": "5"}}, {"name": "Long", "vars": {"len": "50"}}])
    srcs = [w.source(c, version="v1") for c in ("Short", "Long")]
    for s in srcs:
        w.asm.doc.insert(s, name="Spacer", checkpoint=False)
    w.asm.doc.dirty = True                                       # insert without a checkpoint leaves it clean
    w.doc.save()
    e = engines(kind, w.doc.id, w.reg)
    _st, asm = w.open(e)
    assert instance_volumes(e, asm) == [round(AREA * 10), round(AREA * 20)]   # v1, not the live 5 / 50
    pkgs = [w.doc.resolver.resolve_studio(s) for s in srcs]
    assert pkgs[0].digest != pkgs[1].digest


# =========================================================================== the window's mirror

def _snap(configs):
    return {"features": [], "rollback": None, "folders": {}, "overrides": {}, "configs": configs}


def test_plan_edits_sends_the_table_or_the_active_row():
    from freeshape.ui.partstudio_engine import plan_edits
    none = _snap({"rows": [], "active": 0})
    two = _snap({"rows": ROWS, "active": 0})
    assert plan_edits(none, none) == [] and plan_edits(two, two) == []
    assert plan_edits(none, two) == [{"op": "set_configs", "rows": ROWS, "active": 0}]
    assert plan_edits(two, _snap({"rows": ROWS, "active": 1})) == [{"op": "set_active_config", "config": 1}]
    assert plan_edits(two, none) == [{"op": "set_configs", "rows": [], "active": 0}]
    assert plan_edits(none, _snap({"rows": ROWS, "active": 7}))[0]["active"] == 1     # read as config_row reads it


def test_the_window_sends_its_configurations_to_the_worker(tmp_path):
    os.environ.setdefault("FREESHAPE_NO_RECOVERY", "1")
    os.environ.setdefault("FREESHAPE_NO_HOME", "1")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from freeshape.ui.mainwindow import MainWindow
    doc0 = Document()
    spacer(doc0)
    path = str(tmp_path / "w.fshape")
    doc0.save(path)
    w = MainWindow(engine=True)
    try:
        w.open_any(path)
        eng, doc = w.studio_engine, w.doc
        assert eng.settle(180)

        def worker_volume():
            return eng.in_engine(doc, studio_volume)

        def window_volume():
            return sum(K.volume(p.shape) for p in doc.state.parts.values())

        doc.set_configs(ROWS, 0)
        assert eng.settle(60) and abs(worker_volume() - AREA * 10) < 1e-6
        doc.set_active_config(1)                                # what the toolbar's configuration box does
        assert eng.settle(60) and abs(worker_volume() - AREA * 20) < 1e-6
        assert abs(window_volume() - AREA * 20) < 1e-6 and doc.state.vars["len"] == 20
        doc.undo()
        assert eng.settle(60) and abs(worker_volume() - AREA * 10) < 1e-6
        assert doc.active_config() == "Short" and doc.state.vars["len"] == 10
    finally:
        w.studio_engine.shutdown()
        P.SPARES.clear()
