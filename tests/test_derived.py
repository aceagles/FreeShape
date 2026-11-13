"""I1.7: the "derived" feature brings parts and sketches of another Part Studio into this one, with their
element names. It reads the source's StudioPackage (I1.5), so a reference picked on a derived edge or region
finds the same element after the source changes."""
import json

from conftest import add, line_along, rect_lines, region_refs, sketch_feature
from freeshape import doc_folder as DF
from freeshape import doc_registry as R
from freeshape import features as F
from freeshape import kernel as K
from freeshape import links as L
from freeshape import naming as N
from freeshape.document import Document
from freeshape.sketch import Sketch

DRV = ";DRV;:H"


def make_folder(tmp_path):
    return DF.FolderDocument.create(str(tmp_path / "Doc"), registry=R.default_registry())


def box_studio(fd, name="Src", w=20):
    tab = fd.add_tab("studio", name)
    sid = sketch_feature(tab.doc, lambda s: rect_lines(s, 0, 0, w, 10))
    add(tab.doc, "extrude", regions=region_refs(tab.doc, sid), depth=5, op="new")
    return tab, sid


def set_width(tab, w):
    s = Sketch()
    rect_lines(s, 0, 0, w, 10)
    s.solve()
    tab.doc.features[0]["params"]["sketch"] = s.to_dict()
    tab.doc.rebuild()


def derive(tab, src, parts=("F2/1",), sketches=("F1",), **kw):
    return add(tab.doc, "derived", source=L.studio_source(src, **kw), parts=list(parts), sketches=list(sketches),
               connectors=[])


def test_a_studio_derives_a_part_and_a_sketch_with_names(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src)
    fid = f["id"]
    st = use.doc.state
    assert not use.doc.errors and list(st.parts) == [f"{fid}/1"]
    part, origin = st.parts[f"{fid}/1"], src.doc.state.parts["F2/1"]
    assert abs(K.volume(part.shape) - K.volume(origin.shape)) < 1e-9
    named = st.named(part.id)
    assert named is not None and len(named.names) == len(N._Elements(part.shape).keys())
    assert all(DRV + fid + ":4," in n for n in named.names.values())
    # the source's own names are carried one to one in front of the fragment
    src_names = src.doc.state.named("F2/1").names
    assert {k: v + f"{DRV}{fid}:4,{k[0]}" for k, v in src_names.items()} == named.names
    sk = st.sketches[f"{fid}:{sid}"]
    assert len(sk.regions) == 1 and sk.region_names[0].endswith(f"{DRV}{fid}:4,F")
    # an extrude of the derived sketch region builds like a local sketch
    ex = add(use.doc, "extrude", regions=region_refs(use.doc, f"{fid}:{sid}"), depth=2, op="new")
    assert len(use.doc.state.parts) == 2 and not use.doc.errors, ex


def test_fillet_and_extrude_follow_the_source_width_by_name(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src)
    fid = f["id"]
    pid = f"{fid}/1"
    part = use.doc.state.parts[pid]
    vertical = [e for e in K.edges(part.shape) if line_along((0, 0, 1))(K.edge_info(e))]
    edges = [F.make_ref(pid, vertical[0], "edge", part.shape, use.doc.state.named(pid))]
    assert edges[0].get("name")
    fil = add(use.doc, "fillet", edges=edges, radius=1)
    ex = add(use.doc, "extrude", regions=region_refs(use.doc, f"{fid}:{sid}"), depth=2, op="new")
    vol = K.volume(use.doc.state.parts[pid].shape)
    set_width(src, 30)
    fd.refresh_links()
    assert not use.doc.errors, use.doc.errors
    assert fil["id"] not in use.doc.state.recovered and ex["id"] not in use.doc.state.recovered
    wide = use.doc.state.parts[pid]
    assert K.volume(wide.shape) > vol * 1.3
    lo, hi = K.bbox(use.doc.state.parts[f"{ex['id']}/1"].shape)
    assert abs((hi - lo)[0] - 30) < 1e-6


def test_the_derived_part_id_holds_when_the_source_gains_a_part(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src, sketches=())
    before = K.volume(use.doc.state.parts[f"{f['id']}/1"].shape)
    # a new part, built before the ticked one in the source's history
    other = Sketch()
    rect_lines(other, 50, 0, 5, 5)
    other.solve()
    sk = src.doc.make_feature("sketch")
    sk["params"].update(plane={"kind": "std", "name": "Top"}, sketch=other.to_dict())
    src.doc.add_feature(sk, index=0)
    ex = src.doc.make_feature("extrude")
    ex["params"].update(regions=region_refs(src.doc, sk["id"]), depth=3, op="new")
    src.doc.add_feature(ex, index=1)
    fd.refresh_links()
    assert len(src.doc.state.parts) == 2
    assert list(use.doc.state.parts) == [f"{f['id']}/1"] and not use.doc.errors
    assert abs(K.volume(use.doc.state.parts[f"{f['id']}/1"].shape) - before) < 1e-9    # still the ticked box


def test_part_ids_follow_the_position_in_the_ticked_list(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    other = sketch_feature(src.doc, lambda s: rect_lines(s, 50, 0, 5, 5))
    add(src.doc, "extrude", regions=region_refs(src.doc, other), depth=3, op="new")
    parts = list(src.doc.state.parts)
    assert len(parts) == 2
    use = fd.add_tab("studio", "Use")
    f = derive(use, src, parts=parts[::-1], sketches=())
    assert list(use.doc.state.parts) == [f"{f['id']}/1", f"{f['id']}/2"]
    assert use.doc.state.parts[f"{f['id']}/1"].name == src.doc.state.parts[parts[1]].name


def test_each_error_names_its_cause_and_the_rest_still_builds(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    use = fd.add_tab("studio", "Use")
    mine = sketch_feature(use.doc, lambda s: rect_lines(s, 0, 0, 4, 4))
    add(use.doc, "extrude", regions=region_refs(use.doc, mine), depth=1, op="new")
    f = derive(use, src)
    fid = f["id"]
    after = add(use.doc, "extrude", regions=region_refs(use.doc, mine), depth=2, op="new")
    assert not use.doc.errors and len(use.doc.state.parts) == 3

    # a ticked sketch the source no longer publishes
    use.doc.features[2]["params"]["sketches"] = ["F99"]
    use.doc.rebuild()
    assert "F99" in use.doc.errors[fid] and "no longer published" in use.doc.errors[fid]
    use.doc.features[2]["params"]["sketches"] = ["F1"]

    # a ticked part deleted in the source
    src.doc.delete_features(["F2"])
    fd.refresh_links()
    assert "F2/1" in use.doc.errors[fid] and "no longer published" in use.doc.errors[fid]
    assert len(use.doc.state.parts) >= 2          # the studio's own parts still build

    # the source in error
    src.doc.features[0]["params"]["plane"] = {"kind": "feature", "id": "NOPE"}
    src.doc.rebuild()
    assert src.doc.errors
    fd.refresh_links()
    assert "in error" in use.doc.errors[fid] and "Src" in use.doc.errors[fid]
    assert len(use.doc.state.parts) >= 2

    # the source tab removed
    fd.remove_tab(src.id)
    use.doc.rebuild()
    assert "not found" in use.doc.errors[fid] and "Src" in use.doc.errors[fid]
    assert len(use.doc.state.parts) >= 2


def test_a_studio_outside_a_document_names_the_cause():
    doc = Document()
    f = doc.make_feature("derived")
    f["params"].update(source={"kind": "studio", "tab": "T1", "part": None, "studio_name": "Src"}, parts=["F2/1"])
    doc.add_feature(f)
    assert "add this Part Studio to a Document to use Derived" in doc.errors[f["id"]]


def test_a_studio_without_derived_builds_the_same_hashes():
    from freeshape.engine.encode import brep_text
    import hashlib
    doc = Document()
    sid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 20, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=5, op="new")
    sid2 = sketch_feature(doc, lambda s: rect_lines(s, 3, 3, 4, 4))
    add(doc, "extrude", regions=region_refs(doc, sid2), depth=9, op="add")
    got = {pid: hashlib.sha256(brep_text(p.shape)).hexdigest() for pid, p in doc.state.parts.items()}
    assert got == BASELINE, json.dumps(got)


BASELINE = {"F2/1": "e316e0027921118426ca125327a8b985487e931d98134ca6ed91d45ed7c988c3"}


# ---------------------------------------------------------------------------- mate connectors (I3.6)
def connector_in(tab, pid="F2/1", name="Top"):
    from test_derived_placement import top_face_connector
    return top_face_connector(tab.doc, pid, name)["id"]


def test_a_derived_connector_has_the_source_frame_and_is_published(tmp_path):
    from freeshape.engine.encode import connector_out
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    cid = connector_in(src)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src)
    f["params"]["connectors"] = [cid]
    use.doc.rebuild()
    fid = f["id"]
    assert not use.doc.errors, use.doc.errors
    got = use.doc.state.connectors[f"{fid}:{cid}"]
    want = src.doc.state.connectors[cid]
    assert got["part"] == f"{fid}/1" and got["name"] == "Top" and got["frame"] == want["frame"]
    assert f"{fid}:{cid}" in {c.id for c in [connector_out(k, v) for k, v in use.doc.state.connectors.items()]}
    # a studio's published connectors: what the resolver (and so an Assembly) reads
    from freeshape.links import LinkResolver
    pkg = fd.resolver.resolve_studio(L.studio_source(use))
    assert pkg.connectors[f"{fid}:{cid}"]["frame"] == want["frame"]
    assert [c["id"] for c in fd.resolver.resolve_connectors(L.studio_source(use, part=f"{fid}/1"))] == [f"{fid}:{cid}"]


def test_the_derived_connector_follows_a_source_edit_that_moves_it(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    cid = connector_in(src)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src)
    f["params"]["connectors"] = [cid]
    use.doc.rebuild()
    key = f"{f['id']}:{cid}"
    before = list(use.doc.state.connectors[key]["frame"]["o"])
    set_width(src, 30)
    fd.refresh_links()
    after = use.doc.state.connectors[key]["frame"]["o"]
    assert not use.doc.errors and abs(after[0] - before[0] - 5) < 1e-6
    assert after == src.doc.state.connectors[cid]["frame"]["o"]


def test_a_connector_without_its_part_or_no_longer_published_is_red(tmp_path):
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    cid = connector_in(src)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src, parts=())
    f["params"]["connectors"] = [cid]
    use.doc.rebuild()
    assert cid in use.doc.errors[f["id"]] and "tick that part" in use.doc.errors[f["id"]]
    f["params"]["parts"] = ["F2/1"]
    f["params"]["connectors"] = ["F99"]
    use.doc.rebuild()
    assert "F99" in use.doc.errors[f["id"]] and "no longer published" in use.doc.errors[f["id"]]


def test_an_assembly_mate_on_a_derived_connector_solves(tmp_path):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from freeshape import assembly as A
    from test_connector_frames import assembly, bottom_face, src as src_of
    fd = make_folder(tmp_path)
    src, sid = box_studio(fd)
    cid = connector_in(src)
    use = fd.add_tab("studio", "Use")
    f = derive(use, src)
    f["params"]["connectors"] = [cid]
    use.doc.rebuild()
    key = f"{f['id']}:{cid}"
    block = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20, 10, 8).Shape()
    a = assembly(use.doc, {"block": block})
    a.connector_resolver = lambda s: [{"id": k, "name": c["name"], "frame": c["frame"]}
                                      for k, c in use.doc.state.connectors.items()]
    ip = a.insert(src_of("plate"))
    a.set_fixed(ip, True)
    ib = a.insert(src_of("block"))
    m = a.make_mate("fastened")
    m["a"] = a.named_connector(ip, key)
    m["b"] = a.connector(ib, "face", bottom_face(block))
    a.add_mate(m)
    a.solve()
    assert not a.errors, a.errors
    assert abs(a.T(ib)[2, 3] - use.doc.state.connectors[key]["frame"]["o"][2]) < 1e-6
