"""I1.5: a Part Studio reads another Part Studio's published output. Document.link_inputs gives a feature of
links.SOURCE_FEATURES the StudioPackage of its source as params["resolved"], and its digest goes into the
rebuild key. LinkResolver.resolve_studio is the headless provider; FolderDocument wires it, and var_inputs,
into every studio tab."""
import pytest

from conftest import add, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import links as L

REF = "variable_studio_ref"


def make_folder(tmp_path):
    return DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())


def box_studio(fd, name="Src", w=10):
    tab = fd.add_tab("studio", name)
    sid = sketch_feature(tab.doc, lambda s: rect_lines(s, 0, 0, w, 10))
    add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=5, op="new")
    return tab


def test_folder_studio_resolves_a_variable_studio_reference(tmp_path):
    fd = make_folder(tmp_path)
    v = fd.add_tab("variables", "Vars")
    v.doc.add_row("w", "20 mm")
    st = fd.add_tab("studio", "S")
    assert st.doc.var_inputs is not None and st.doc.link_inputs is not None
    add(st.doc, REF, source={"kind": "variables", "tab": v.id, "name": "Vars"})
    sid = sketch_feature(st.doc, lambda s: rect_lines(s, 0, 0, 10, 10))
    add(st.doc, "extrude", regions=region_refs(st.doc, sid), depth="#w", op="new")
    assert st.doc.state.vars["w"] == 20
    fd.save()
    again = DF.FolderDocument.open(fd.path, registry=R.default_registry())
    s2 = again.tab(st.id).doc
    assert s2.state.vars["w"] == 20 and len(s2.state.parts) == 1 and not s2.errors
    v2 = again.tab(v.id).doc
    v2.set_row(0, expression="50 mm")
    again.refresh_links()
    assert s2.state.vars["w"] == 50


def test_resolve_studio_gives_parts_and_sketches_with_names(tmp_path):
    fd = make_folder(tmp_path)
    src = box_studio(fd)
    pkg = fd.resolver.resolve_studio(L.studio_source(src))
    st = src.doc.state
    assert list(pkg.parts) == list(st.parts) and pkg.parts
    for pid, part in pkg.parts.items():
        named = st.named(pid)
        assert named is not None and part.names["face"]
        assert part.names == {k: {} for k in ("face", "edge", "vertex")} | {
            kind: {i: n for (letter, i), n in named.names.items() if letter == L_KIND[kind]} for kind in L_KIND}
        assert part.name == st.parts[pid].name and part.shape is st.parts[pid].shape and part.hash
    assert list(pkg.sketches) == list(st.sketches)
    sk = next(iter(pkg.sketches.values()))
    assert sk.names["face"] and sk.hash and sk.region_names


L_KIND = {"face": "F", "edge": "E", "vertex": "V"}


def test_digest_changes_with_the_source_and_is_stable_otherwise(tmp_path):
    fd = make_folder(tmp_path)
    src = box_studio(fd)
    a = fd.resolver.resolve_studio(L.studio_source(src))
    b = fd.resolver.resolve_studio(L.studio_source(src))
    assert a.digest == b.digest and a.digest_for(None) == a.digest
    twin_fd = make_folder(tmp_path / "x")
    twin = box_studio(twin_fd, "Src")         # the same model built again: the same digest
    assert twin_fd.resolver.resolve_studio(L.studio_source(twin)).digest == a.digest
    # a part change
    src.doc.features[1]["params"]["depth"] = 6
    src.doc.rebuild()
    c = fd.resolver.resolve_studio(L.studio_source(src))
    assert c.digest != a.digest
    # a sketch-only change: a second sketch adds no part
    before = c.digest
    sketch_feature(src.doc, lambda s: rect_lines(s, 0, 0, 3, 3))
    d = fd.resolver.resolve_studio(L.studio_source(src))
    assert d.digest != before and list(d.parts) == list(c.parts)
    # a rebuilt, unchanged source keeps its digest
    src.doc.rebuild()
    assert fd.resolver.resolve_studio(L.studio_source(src)).digest == d.digest


PROBE = "probe_link"


@pytest.fixture
def probe(monkeypatch):
    seen = []
    monkeypatch.setitem(L.SOURCE_FEATURES, PROBE, "source")
    monkeypatch.setitem(F.RUNNERS, PROBE, lambda state, f: seen.append(f["params"].get("resolved")))
    return seen


def test_a_source_feature_receives_the_package_and_keys_the_rebuild(tmp_path, probe):
    fd = make_folder(tmp_path)
    src = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    use.doc.add_feature({"id": use.doc.new_id(), "type": PROBE, "name": "Probe", "suppressed": False,
                         "params": {"source": L.studio_source(src, part="*")}})
    pkg = probe[-1]
    assert isinstance(pkg, L.StudioPackage) and list(pkg.parts) == list(src.doc.state.parts)
    n = len(probe)
    use.doc.rebuild()
    assert len(probe) == n                      # unchanged source: the cache key holds, the runner is not run
    src.doc.features[1]["params"]["depth"] = 7
    src.doc.rebuild()
    use.doc.rebuild()
    assert len(probe) == n + 1 and probe[-1].digest != pkg.digest
    # a link that finds nothing runs the feature with None
    use.doc.features[0]["params"]["source"] = {"kind": "studio", "tab": "T99", "part": "*"}
    use.doc.rebuild()
    assert probe[-1] is None
