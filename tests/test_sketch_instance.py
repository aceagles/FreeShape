"""A Sketch instance (I1.2): an Assembly instance whose source is {"kind": "sketch", "uid", "sketch"}. Its shape is
every entity of the sketch (construction lines and free points too) on the sketch plane; it is an ordinary
instance for insert, fixed, groups and the solver, and is left out of BOM, mass and STEP export."""
import copy

import numpy as np

from conftest import FRONT, add, rect_lines, region_refs, sketch_feature
from freeshape import assembly as A
from freeshape import kernel as K
from freeshape import links as L
from freeshape.document import Document


def layout_sketch(s):
    """Two construction lines and a free point."""
    a, b, c = s.add_point(0, 0), s.add_point(30, 0), s.add_point(30, 20)
    s.add_line(a, b, construction=True)
    s.add_line(b, c, construction=True)
    s.add_point_entity(s.add_point(10, 10))


def make_studio(with_part=False):
    doc = Document()
    sid = sketch_feature(doc, layout_sketch, FRONT)
    pid = None
    if with_part:
        rid = sketch_feature(doc, lambda s: rect_lines(s, 0, 0, 10, 10), FRONT)
        add(doc, "extrude", regions=region_refs(doc, rid), depth=5, op="new")
        pid = next(iter(doc.state.parts))
    return doc, sid, pid


def assembly_for(doc):
    r = L.LinkResolver(find_open_studio=lambda uid, path: doc)
    a = A.AssemblyDoc()
    a.resolver = r.resolve_source
    return a, r


def sk_src(doc, sid):
    return {"kind": "sketch", "uid": doc.uid, "sketch": sid}


def part_src(doc, pid):
    return {"kind": "studio", "uid": doc.uid, "part": pid}


def test_sketch_instance_shape_is_every_entity_on_the_plane():
    doc, sid, _ = make_studio()
    a, _ = assembly_for(doc)
    iid = a.insert(sk_src(doc, sid))
    shape = a.parts[iid].shape
    assert len(K.edges(shape)) == 2
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    exp, verts = TopExp_Explorer(shape, TopAbs_VERTEX), 0
    while exp.More():
        verts += 1
        exp.Next()
    assert verts >= 1                                  # the free point is a vertex of its own
    pl = doc.state.sketches[sid].plane
    lo, hi = K.bbox(shape)
    for p in (lo, hi):                                 # everything lies on the sketch plane
        assert abs(float((np.asarray(p) - pl.origin) @ pl.normal)) < 1e-6
    assert np.isclose(np.linalg.norm(hi - lo), np.linalg.norm([30, 20]), atol=1e-6)


def test_first_instance_is_fixed_second_has_six_dof():
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    first = a.insert(sk_src(doc, sid))
    assert a.instance(first)["fixed"] and a.instance_dof(first) == 0
    a2, _ = assembly_for(doc)
    a2.insert(part_src(doc, pid))
    second = a2.insert(sk_src(doc, sid))
    assert not a2.instance(second)["fixed"] and a2.instance_dof(second) == 6


def test_helpers_tell_sketch_from_part_instances():
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    ip, isk = a.insert(part_src(doc, pid)), a.insert(sk_src(doc, sid))
    assert a.is_sketch_instance(isk) and not a.is_sketch_instance(ip)
    assert A.is_sketch_source(a.instance(isk)["source"]) and not A.is_sketch_source(a.instance(ip)["source"])
    out = a.sketch_out(isk)
    assert out is not None and out.id == sid and out.hash
    assert a.sketch_out(ip) is None and a.sketch_out("nope") is None
    assert a._named(isk) is None


def test_editing_the_sketch_changes_the_instance_and_the_studio_is_consumed():
    doc, sid, _ = make_studio()
    a, r = assembly_for(doc)
    src = sk_src(doc, sid)
    iid = a.insert(src)
    assert L.tab_sources("assembly", a) == [src]
    before_hash, before_digest = a.sketch_out(iid).hash, r.resolve_studio(src).digest_for(None)
    f = copy.deepcopy(doc.feature(sid))
    f["params"]["sketch"]["points"]["p3"] = [50.0, 0.0]          # the end of the first line
    doc.replace_feature(f)
    assert not doc.errors.get(sid), doc.errors
    a.refresh()
    lo, hi = K.bbox(a.parts[iid].shape)
    assert hi[0] - lo[0] > 40 or hi[2] - lo[2] > 40 or hi[1] - lo[1] > 40
    assert a.sketch_out(iid).hash != before_hash
    assert r.resolve_studio(src).digest_for(None) != before_digest


def test_sketch_instance_is_not_a_part_for_bom_mass_and_step(tmp_path):
    doc, sid, pid = make_studio(with_part=True)
    a, _ = assembly_for(doc)
    ip = a.insert(part_src(doc, pid))
    b, _ = assembly_for(doc)
    b.insert(part_src(doc, pid))
    a.insert(sk_src(doc, sid))
    assert a.bom() == b.bom()
    assert a.interferences() == b.interferences()
    pa, pb = str(tmp_path / "a.step"), str(tmp_path / "b.step")
    a.export(pa)
    b.export(pb)
    from OCP.STEPControl import STEPControl_Reader

    def solids(path):
        rd = STEPControl_Reader()
        rd.ReadFile(path)
        rd.TransferRoots()
        return len(K.solids(rd.OneShape())), round(K.volume(rd.OneShape()), 6)
    assert solids(pa) == solids(pb) == (1, 500.0)
    assert K.volume(a.world_shape(ip)) == K.volume(b.world_shape(ip))


def test_save_load_and_a_deleted_sketch_is_a_named_error(tmp_path):
    doc, sid, pid = make_studio(with_part=True)
    a, r = assembly_for(doc)
    ip = a.insert(part_src(doc, pid))
    isk = a.insert(sk_src(doc, sid))
    T = A.translation([5, 6, 7])
    a.instance(isk)["T"] = T.tolist()
    a.path = str(tmp_path / "x.fasm")
    a.save()
    b = A.AssemblyDoc.load(a.path, resolver=r.resolve_source)
    inst = next(i for i in b.instances if i["id"] == isk)
    assert inst["source"] == sk_src(doc, sid)
    assert np.allclose(b.T(isk), T)
    assert isk in b.parts and not b.errors
    doc.delete_features([sid])
    b.refresh()
    assert "no longer exists" in b.errors[isk]
    assert ip in b.parts and ip not in b.errors            # the rest of the Assembly still solves
