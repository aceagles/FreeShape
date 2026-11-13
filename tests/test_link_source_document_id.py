"""I4.5: a source that names another Document never matches a local tab, even when its tab id is one (every
Document's first tab is T1). Document A has a Part Studio T1 (a 20 mm box) and a Variable Studio T2 (w = 20); Document B has
its own T1 (a 7 mm box) and T2 (w = 99), and consumers of A's tabs pinned to a Version: an Assembly instance, a
Drawing source, a Derived feature and a Variable Studio reference. Each consumer shows A's, never B's, on both
executors (`inprocess` and `process`). A source with no `document`, or the open Document's own id, is local.

Entry points that start a ProcessEngine need a `__main__` guard (workers import the test module again); pytest
supplies it."""
import json
import os
import shutil

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature, TOP
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import links as L
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine import process as P
from freeshape.engine.encode import Encoder

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}
A_W, B_W = 20.0, 7.0


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


def _var_source(tab, **pin):
    return {"kind": "variables", "tab": tab.id, "name": tab.name, **pin}


class World:
    """A: studio T1 (box 20 x 20 x 10), Variable Studio T2 (w = 20 mm), Version v1. B: studio T1 (box 7 x 7 x 3),
    Variable Studio T2 (w = 99 mm), and consumers of A's T1 / T2 at v1, each saved as its own tab file."""

    def __init__(self, tmp_path):
        self.reg = R.DocRegistry(str(tmp_path / "data" / "documents.json"))
        a = self.a = DF.FolderDocument.create(str(tmp_path / "A"), name="A", registry=self.reg)
        self.a_st = a.add_tab("studio", "Plate")
        _box(self.a_st.doc, A_W, 10.0)
        self.a_pid = next(iter(self.a_st.doc.state.parts))
        self.a_hash = _hash(self.a_st.doc.state.parts[self.a_pid])
        self.a_vars = a.add_tab("variables", "Dims")
        self.a_vars.doc.add_row("w", f"{A_W:g} mm")
        a.create_version("v1", "first")
        b = self.b = DF.FolderDocument.create(str(tmp_path / "B"), name="B", registry=self.reg)
        self.b_st = b.add_tab("studio", "Mine")
        _box(self.b_st.doc, B_W, 3.0)
        self.b_pid = next(iter(self.b_st.doc.state.parts))
        self.b_hash = _hash(self.b_st.doc.state.parts[self.b_pid])
        self.b_vars = b.add_tab("variables", "Dims")
        self.b_vars.doc.add_row("w", "99 mm")
        assert (self.a_st.id, self.a_vars.id) == (self.b_st.id, self.b_vars.id) == ("T1", "T2")
        assert self.a_hash != self.b_hash
        self.asm = b.add_tab("assembly", "Asm")
        self.asm.doc.insert(self.cross(), name="Plate")
        self.asm.doc.refresh(solve=False, notify=False)
        assert not self.asm.doc.errors, self.asm.doc.errors
        self.dwg = b.add_tab("drawing", "Dwg")
        self.dwg.doc.source = self.cross()
        self.use = b.add_tab("studio", "Use")             # a Derived of A's T1 and a reference to A's T2
        d = self.use.doc.make_feature("derived")
        d["params"].update(source=self.cross(), parts=[self.a_pid], sketches=[], connectors=[])
        self.use.doc.add_feature(d)
        self.derived_id = d["id"]
        r = self.use.doc.make_feature("variable_studio_ref")
        r["params"]["source"] = _var_source(self.a_vars, document=a.id, version="v1")
        self.use.doc.add_feature(r)
        self.local = {}           # saved assemblies: B's own T1 by no document, by its own id; A gone
        for name in ("none", "own", "gone", "gone-copy"):
            tab = self.local[name] = b.add_tab("assembly", name)
            src = self.cross() if name.startswith("gone") else L.studio_source(self.b_st, self.b_pid)
            if name == "own":
                src.update(document=b.id)
            tab.doc.insert(src, name="Mine")
            tab.doc.refresh(solve=False, notify=False)
            assert not tab.doc.errors, tab.doc.errors
        for name in ("gone", "gone-copy"):
            self.local[name].doc.instances[0]["source"] = self.cross(document="no-such-document-id")
        b.save()
        with open(self.path(self.local["gone"])) as fh:               # "gone" keeps no saved copy of the part
            data = json.load(fh)
        for inst in data["instances"]:
            inst.pop("_brep_blob", None)
        with open(self.path(self.local["gone"]), "w") as fh:
            json.dump(data, fh)

    def cross(self, document=None, version="v1", **over):
        """A's T1 as a source of B: tab id, part, and A's id with the pinned Version."""
        src = L.studio_source(self.a_st, self.a_pid, document=document or self.a.id, version=version)
        src.update(over)
        return src

    def path(self, tab):
        return os.path.join(self.b.path, tab.file)

    def engine(self, kind, tabs):
        eng = ENGINES[kind]()
        if hasattr(eng, "use_document"):
            eng.use_document(self.b.id, self.reg)
        return eng, {t.id: eng.open_tab(self.path(t), tab=t.id) for t in tabs}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture(params=sorted(ENGINES))
def kind(request):
    return request.param


def test_the_assembly_instance_shows_a_plate_not_bs(world, kind):
    eng, tabs = world.engine(kind, [world.b_st, world.asm])
    try:
        out = eng.outputs(tabs["T3"])
        assert not out.errors, out.errors
        assert [i.part for i in out.instances] == [world.a_hash] and world.a_hash != world.b_hash
    finally:
        eng.close()


def test_the_drawing_shows_as_plate_not_bs(world, kind):
    eng, tabs = world.engine(kind, [world.b_st, world.dwg])
    try:
        out = eng.outputs(tabs["T4"])
        assert not out.errors, out.errors
        assert out.model == world.a_hash
    finally:
        eng.close()


def test_the_derived_brings_as_plate_not_bs(world, kind):
    eng, tabs = world.engine(kind, [world.b_st, world.use])
    try:
        out = eng.outputs(tabs["T5"])
        derived = [p for p in out.parts if p.id.startswith(f"{world.derived_id}/")]
        assert [p.hash for p in derived] == [world.a_hash], out.errors
        assert not out.errors, out.errors
    finally:
        eng.close()


def test_the_variable_reference_reads_as_w_not_bs(world, kind):
    eng, tabs = world.engine(kind, [world.b_vars, world.use])
    try:
        out = eng.outputs(tabs["T5"])
        assert out.variables.get("w") == A_W, (out.variables, out.errors)
    finally:
        eng.close()


def test_a_missing_document_is_an_error_naming_it_not_bs_part(world, kind):
    eng, tabs = world.engine(kind, [world.b_st, world.local["gone"]])
    try:
        out = eng.outputs(tabs[world.local["gone"].id])
        assert [i.part for i in out.instances] == [None]            # no part at all: not B's T1, which shares the tab id
        assert "no-such-document-id" in out.errors["I1"], out.errors
    finally:
        eng.close()


def test_a_missing_document_with_a_saved_copy_shows_the_copy_not_bs_part(world, kind):
    eng, tabs = world.engine(kind, [world.b_st, world.local["gone-copy"]])
    try:
        out = eng.outputs(tabs[world.local["gone-copy"].id])
        assert [i.part for i in out.instances] == [world.a_hash]        # the copy saved from A
        assert out.errors["I1"], out.errors                             # and the instance is in error
    finally:
        eng.close()


@pytest.mark.parametrize("name", ["none", "own"])
def test_a_source_without_a_document_or_with_its_own_id_is_the_local_tab(world, kind, name):
    eng, tabs = world.engine(kind, [world.b_st, world.local[name]])
    try:
        out = eng.outputs(tabs[world.local[name].id])
        assert not out.errors, out.errors
        assert [i.part for i in out.instances] == [world.b_hash]
    finally:
        eng.close()


def test_the_engines_match_never_returns_a_local_tab_for_another_document(world):
    eng, tabs = world.engine("process", [world.b_st])
    try:
        assert eng._match(world.cross()) is None
        assert eng._match(world.cross(version=None)) is None          # no Version: still not B's T1
        local = {k: v for k, v in world.cross().items() if k not in ("document", "version")}
        assert eng._match(local) is eng._tabs["T1"]
    finally:
        eng.close()


def test_the_in_process_provide_never_returns_a_local_tab_for_another_document(world):
    eng = InProcessEngine()
    eng.open_tab(world.path(world.b_st), tab="T1")
    from freeshape.engine.inputs import external_id
    assert eng.provide("studio", tab=external_id(world.cross())) is None
    assert eng.provide("studio", tab="T1") is not None
