"""I1.4: links.tab_sources is the one function that says what a tab consumes; SOURCE_FEATURES is its table of
studio feature types. Every place that asks (cross_sources, repair_links, document_warnings, _file_sources,
LiveTabs.sources, the worker's meta, ProcessEngine.submit) goes through it.

Entry points that start a ProcessEngine need a `__main__` guard (workers import the test module again); pytest
supplies it."""
import json

import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import kernel as K
from freeshape.document import Document
from freeshape import links as L
from freeshape.engine import ProcessEngine
from freeshape.engine import process as P
from freeshape.variable_studio import VariableStudioDoc

REF = "variable_studio_ref"
VS = {"kind": "variables", "tab": "T1", "name": "V"}


@pytest.fixture(scope="module", autouse=True)
def _no_spares_left():
    yield
    P.SPARES.clear()


def height_of(part):
    lo, hi = K.bbox(K.shape_from_brep(part.brep))
    return round(float(hi[2] - lo[2]), 6)


def make_ref_studio(name, source):
    """A box whose depth is the Variable Studio variable `w`."""
    a = Document()
    a.name = name
    a.var_inputs = lambda src: {"w": 20.0}              # only to build the file: the engine resolves it for real
    a.add_feature({"id": a.new_id(), "type": REF, "name": "Variables", "suppressed": False,
                   "params": {"source": source}})
    sid = sketch_feature(a, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(a, "extrude", regions=region_refs(a, sid), depth="#w", op="new")
    a.var_inputs = None
    return a


def test_tab_sources_of_each_kind():
    studio = {"features": [{"id": "F1", "type": REF, "params": {"source": VS}},
                           {"id": "F2", "type": "extrude", "params": {"source": {"kind": "studio"}}},
                           {"id": "F3", "type": REF, "params": {}}]}
    assert L.tab_sources("studio", studio) == [VS]
    a, b = {"kind": "studio", "tab": "T2", "part": "P1"}, {"kind": "studio", "tab": "T3", "part": "P2"}
    assert L.tab_sources("assembly", {"instances": [{"id": "I1", "source": a}, {"id": "I2", "source": b}]}) == [a, b]
    flat = {"kind": "studio", "tab": "T2", "part": "P1", "flat": True}
    assert L.tab_sources("drawing", {"source": flat}) == [flat]
    assert L.tab_sources("drawing", {"source": None}) == []
    assert L.tab_sources("variables", {"rows": []}) == []
    assert L.feature_source({"type": REF, "params": {"source": VS}}) is VS
    assert L.feature_source({"type": "extrude", "params": {"source": VS}}) is None


def test_a_fake_feature_type_is_reported_everywhere(tmp_path, monkeypatch):
    monkeypatch.setitem(L.SOURCE_FEATURES, "fake_ref", "src")
    fd = DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())
    st = fd.add_tab("studio", "S")
    cross = {"kind": "studio", "tab": "T9", "document": "other", "version": "v1", "part": "P"}
    gone = {"kind": "studio", "tab": "T77", "part": "P"}
    st.doc.features.append({"id": "F1", "type": "fake_ref", "name": "x", "suppressed": False, "params": {"src": cross}})
    st.doc.features.append({"id": "F2", "type": "fake_ref", "name": "y", "suppressed": False, "params": {"src": gone}})
    assert L.tab_sources("studio", st.doc) == [cross, gone]
    assert L.cross_sources(fd) == [(st, cross)]
    warns = [w for w in fd.document_warnings() if w["problem"] == "dangling-link"]
    assert [w["tab"] for w in warns] == [st.id, st.id]      # one per source that does not resolve
    ok = fd.add_tab("studio", "Target")
    local = {"kind": "studio", "tab": ok.id, "part": "P"}
    st.doc.features.append({"id": "F3", "type": "fake_ref", "name": "z", "suppressed": False, "params": {"src": local}})
    L.repair_links(fd.resolver, st.doc)
    assert local["studio_name"] == "Target"
    path = tmp_path / "S.fshape"
    st.doc.save(str(path))
    on_disk = json.loads(path.read_text())
    assert P._file_sources("studio", on_disk, str(path)) == [cross, gone, local]
    from freeshape.ui.partstudio_engine import LiveTabs
    assert LiveTabs.sources(st.doc) == [cross, gone, local]


def test_set_param_of_a_source_re_records_the_tab_inputs(tmp_path):
    v1, v2 = VariableStudioDoc(), VariableStudioDoc()
    v1.add_row("w", "20 mm")
    v2.add_row("w", "50 mm")
    p1, p2 = str(tmp_path / "V1.fvars"), str(tmp_path / "V2.fvars")
    v1.save(p1)
    v2.save(p2)
    a = make_ref_studio("A", {"kind": "variables", "path": p1, "name": "V1"})
    apath = str(tmp_path / "A.fshape")
    a.save(apath)
    eng = ProcessEngine()
    try:
        t1, t2, ta = eng.open_tab(p1, tab="V1"), eng.open_tab(p2, tab="V2"), eng.open_tab(apath, tab="A")
        assert height_of(eng.outputs(ta).parts[0]) == 20 and ("A", "V1") in eng.link_edges()
        fid = a.features[0]["id"]
        eng.submit_edit(ta, {"op": "set_param", "feature": fid, "key": "source",
                             "value": {"kind": "variables", "path": p2, "name": "V2"}})
        assert ("A", "V2") in eng.link_edges() and height_of(eng.outputs(ta).parts[0]) == 50
        count = eng.rebuild_count(ta)
        eng.submit_edit(t2, {"op": "set_row", "index": 0, "expression": "35 mm"})
        assert eng.rebuild_count(ta) == count + 1 and height_of(eng.outputs(ta).parts[0]) == 35
    finally:
        eng.close()
