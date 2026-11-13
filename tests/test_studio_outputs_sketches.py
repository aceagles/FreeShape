"""I1.3: a Part Studio publishes its sketches -- plane, solved sketch, regions, element names and a content
hash -- and its package digest changes when a sketch does, on both executors."""
import copy

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape.document import Document
from freeshape.engine import InProcessEngine, ProcessEngine
from freeshape.engine.encode import named_elements
from freeshape.engine.linkgraph import lean_package, live_package, package_digest

ENGINES = {"inprocess": InProcessEngine, "process": ProcessEngine}


@pytest.fixture(params=sorted(ENGINES))
def engine(request):
    e = ENGINES[request.param]()
    yield e
    e.close()


def _studio(tmp_path):
    doc = Document()
    s1 = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 40, 20))
    add(doc, "extrude", regions=region_refs(doc, s1), depth=10, op="new")
    s2 = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 5, 5))      # no part uses it
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    return doc, s1, s2, path


def _expected(doc):
    return {str(fid): (named_elements(r.named), list(r.region_names)) for fid, r in doc.state.sketches.items()}


def _package(engine, tab):
    if isinstance(engine, InProcessEngine):
        return live_package("studio", engine._get(tab).doc, engine.encoder)
    return lean_package(engine._lean(engine._tabs[tab], ())[1])


def _digest(engine, tab):
    return package_digest("studio", _package(engine, tab), ())


def test_sketches_carry_names_and_hash(engine, tmp_path):
    doc, s1, s2, path = _studio(tmp_path)
    tab = engine.open_tab(path)
    out = engine.outputs(tab)
    want = _expected(Document.load(path))
    assert {s.id for s in out.sketches} == {s1, s2}
    for sk in out.sketches:
        names, region_names = want[sk.id]
        assert sk.names == names and sk.region_names == region_names
        assert names["face"] and names["edge"] and names["vertex"]
        assert len(sk.region_names) == len(sk.regions) and all(sk.region_names)
        assert sk.plane and sk.sketch and len(sk.hash) == 64
    assert out.sketches[0].hash != out.sketches[1].hash
    assert len(out.parts) == 1


def test_sketch_only_change_changes_the_package_digest(engine, tmp_path):
    doc, s1, s2, path = _studio(tmp_path)
    tab = engine.open_tab(path)
    before, parts_before = _digest(engine, tab), [p.hash for p in engine.outputs(tab).parts]
    f = copy.deepcopy(doc.feature(s2))
    sk = f["params"]["sketch"]
    pt = next(p for p in sk["points"].values() if p[0] == 105)
    pt[0] = 110
    engine.submit_edit(tab, {"op": "replace_feature", "feature": f})
    out = engine.outputs(tab)
    assert [p.hash for p in out.parts] == parts_before          # no part changed
    assert _digest(engine, tab) != before


def test_edit_that_changes_nothing_published_keeps_the_digest(engine, tmp_path):
    doc, s1, s2, path = _studio(tmp_path)
    tab = engine.open_tab(path)
    before = _digest(engine, tab)
    f = copy.deepcopy(doc.feature(s2))
    f["name"] = "renamed"
    engine.submit_edit(tab, {"op": "replace_feature", "feature": f})
    engine.outputs(tab)
    assert _digest(engine, tab) == before
