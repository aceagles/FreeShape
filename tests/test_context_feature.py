"""The "context" feature: a stored capture (assembly.capture_context, ADR 0004) restored as reference bodies."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import (add, block_doc, state_fingerprint, shape_fingerprint as state_fingerprint_shape, try_add,
                      sketch_feature, rect_lines, region_refs, pick_faces, pick_edges)
from freeshape import features as F
from freeshape import assembly as A
from freeshape import blobstore
from freeshape import kernel as K
from freeshape import naming as N
from freeshape.document import Document
from tests.test_assembly import box, cyl, face, make, mate, src


def capture(named=True):
    """The capture of the plate in a plate-and-pin Assembly, and the pin's world shape and the plate's pose."""
    lib = {"plate": box(60, 40, 10), "pin": cyl(4, 30)}
    a = make(lib)
    ip, ipin = a.insert(src("plate")), a.insert(src("pin"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "fastened", top, pb)
    if named:
        a.named_resolver = lambda s: N.seed(a.parts[ipin].shape, "F9") if s["part"] == "pin" else None
    return A.capture_context(a, ip), a.world_shape(ipin), a.T(ip)


def studio_with_context(cap):
    doc = block_doc()
    key = doc.store_context(cap)
    add(doc, "context", assembly=cap["assembly"], instance=cap["instance"], capture=key)
    return doc, key


def bb(shape):
    lo, hi = K.bbox(shape)
    return np.array([lo, hi])


def test_builds_neighbour_at_captured_position_without_assembly():
    cap, pin_world, plate_T = capture()
    doc, _ = studio_with_context(cap)         # the capture is plain data: nothing here can reach the Assembly
    fid = doc.features[-1]["id"]
    part = doc.state.parts[f"{fid}/1"]
    assert part.reference is True and part.name == cap["neighbours"][0]["name"]
    want = bb(K.located(pin_world, np.linalg.inv(plate_T)))
    assert np.allclose(bb(part.shape), want, atol=1e-6)
    assert not doc.state.parts[f"{doc.features[1]['id']}/1"].reference


def test_save_reopen_same_hash_and_source_not_rebuilt(tmp_path, monkeypatch):
    cap, _, _ = capture()
    source = block_doc(10, 10, 10)                 # the neighbour's source studio
    doc, _ = studio_with_context(cap)
    fid = doc.features[-1]["id"]
    before = state_fingerprint(doc)
    path = str(tmp_path / "ctx.json")
    doc.save(path)
    builds = []
    orig = Document.compute
    monkeypatch.setattr(Document, "compute", lambda self, *a, **k: (builds.append(self), orig(self, *a, **k))[1])
    del doc
    again = Document.load(path)
    assert not again.errors, again.errors
    assert state_fingerprint(again)[f"{fid}/1"] == before[f"{fid}/1"]
    assert source not in builds and all(b is again for b in builds)


def test_every_element_has_a_ctx_name():
    for named in (True, False):
        cap, _, _ = capture(named)
        doc, _ = studio_with_context(cap)
        fid = doc.features[-1]["id"]
        nm = doc.state.named(f"{fid}/1")
        assert nm is not None and nm.unnamed() == []
        for key in nm.keys():
            name = nm.names[key]
            assert f";{N.OP_CONTEXT}" in name and f"{N.TAG}{fid}" in name, name
            assert N.split_last(name)[1] == f";{N.OP_CONTEXT}" and N.split_last(name)[2] == fid
        assert len(set(nm.names.values())) == len(nm.names)


def test_missing_capture_blob_is_red_and_rest_builds(tmp_path):
    cap, _, _ = capture()
    doc, key = studio_with_context(cap)
    add(doc, "variable", name="w", expr="3")
    path = str(tmp_path / "ctx.json")
    doc.save(path)
    os.remove(tmp_path / "blobs" / key)
    again = Document.load(path)
    cid = next(f["id"] for f in again.features if f["type"] == "context")
    assert cid in again.errors and "capture" in again.errors[cid]
    assert len(again.errors) == 1
    assert again.state.vars["w"] == 3 and f"{again.features[1]['id']}/1" in again.state.parts


def test_capture_is_small():
    cap, _, _ = capture()
    doc, key = studio_with_context(cap)
    total = sum(len(b) for b in doc._blob_bytes.values())
    assert 0 < total < 5 * 2 ** 20


def test_not_a_link_and_no_part_studio_change():
    from freeshape import links          # a Context is listed by the dependency view but is not a Link source
    assert "context" not in links.SOURCE_FEATURES
    plain = block_doc()
    withctx, _ = studio_with_context(capture()[0])
    first = f"{plain.features[1]['id']}/1"
    assert state_fingerprint(plain)[first] == state_fingerprint(withctx)[first]


# --------------------------------------------------------------------------- I5.3: reference only

def _ref_id(doc):
    return f"{doc.features[-1]['id']}/1"


def _named_ref(doc, pid, kind, pred):
    part = doc.state.parts[pid]
    pick = pick_faces if kind == "face" else pick_edges
    subs = pick(part, pred)
    assert subs
    return F.make_ref(pid, subs[0], kind, part.shape, doc.state.named(pid))


def _top_face_pred(i):
    return i.get("n") is not None and i["n"][2] > 0.9


def test_extrude_up_to_context_face_stops_there():
    cap, _, _ = capture()
    doc, _ = studio_with_context(cap)
    ref = _named_ref(doc, _ref_id(doc), "face", _top_face_pred)
    top = K.face_info(pick_faces(doc.state.parts[_ref_id(doc)], _top_face_pred)[0])["c"][2]
    sid = sketch_feature(doc, lambda s: rect_lines(s, 100, 0, 10, 10))
    add(doc, "extrude", regions=region_refs(doc, sid), end="to_face", to=ref, op="new")
    new = doc.state.parts[next(i for i in doc.state.parts if i.startswith(doc.features[-1]["id"]))]
    assert abs(K.volume(new.shape) - 100 * top) <= 1e-6 * 100 * top


def test_sketch_on_context_face_builds():
    cap, _, _ = capture()
    doc, _ = studio_with_context(cap)
    ref = _named_ref(doc, _ref_id(doc), "face", _top_face_pred)
    sid = sketch_feature(doc, lambda s: rect_lines(s, -1, -1, 2, 2), plane={"kind": "face", "face": ref})
    assert not doc.errors and doc.state.sketches[sid].regions


def test_fillet_on_context_edge_is_red():
    cap, _, _ = capture()
    doc, _ = studio_with_context(cap)
    pid = _ref_id(doc)
    ref = _named_ref(doc, pid, "edge", lambda i: i["type"] == int(K.GeomAbs_Circle))
    before = state_fingerprint(doc)
    f, err = try_add(doc, "fillet", edges=[ref], radius=0.5)
    assert err == "Context bodies are reference only"
    after = state_fingerprint(doc)
    assert after[pid] == before[pid] and doc.state.parts[pid].reference


def test_unscoped_cut_leaves_context_alone_and_move_of_it_is_red():
    cap, _, _ = capture()
    doc, _ = studio_with_context(cap)
    pid = _ref_id(doc)
    before = state_fingerprint(doc)[pid]
    sid = sketch_feature(doc, lambda s: rect_lines(s, -50, -50, 200, 200))
    add(doc, "extrude", regions=region_refs(doc, sid), depth=100, op="remove")
    assert state_fingerprint(doc)[pid] == before
    f, err = try_add(doc, "move", parts=[pid], dx=5)
    assert err == "Context bodies are reference only"


@pytest.fixture(params=["inprocess", "process"])
def engine(request):
    from freeshape.engine import InProcessEngine, ProcessEngine
    e = (InProcessEngine if request.param == "inprocess" else ProcessEngine)()
    yield e
    e.close()


def test_published_outputs_exclude_context_bodies(engine, tmp_path):
    from freeshape.engine import InProcessEngine
    from freeshape.engine.linkgraph import lean_package, live_package
    doc, _ = studio_with_context(capture()[0])
    own = f"{doc.features[1]['id']}/1"
    path = str(tmp_path / "ctx.fshape")
    doc.save(path)
    tab = engine.open_tab(path)
    out = engine.outputs(tab)
    assert [p.id for p in out.parts] == [own]
    if isinstance(engine, InProcessEngine):
        pkg = live_package("studio", engine._get(tab).doc, engine.encoder)
    else:
        pkg = lean_package(engine._lean(engine._tabs[tab], ())[1])
    assert list(pkg["parts"]) == [own]
    assert _ref_id(doc) in doc.state.parts          # still built, shown and pickable inside the studio


def test_step_export_and_links_hold_only_the_studios_part(tmp_path):
    from freeshape import links as L
    doc, _ = studio_with_context(capture()[0])
    own, ref = f"{doc.features[1]['id']}/1", _ref_id(doc)
    out = str(tmp_path / "out.step")
    doc.export(out)
    back = K.import_step(out)
    assert len(back) == 1 and abs(K.volume(back[0]) - 40 * 20 * 10) < 1e-6 * 40 * 20 * 10
    assert [p.id for p in doc.visible_parts()] == [own]
    path = str(tmp_path / "studio.fshape")
    doc.save(path)
    r = L.LinkResolver()
    studio = r.studio_by_uid(None, path, load=True)
    base = {"kind": "studio", "uid": studio.uid, "path": path}
    assert r.resolve_drawing_source({**base, "part": "*"}) is studio.state.parts[own].shape
    with pytest.raises(K.KernelError):
        r.resolve_source({**base, "part": ref})
    with pytest.raises(K.KernelError):
        r.resolve_drawing_source({**base, "part": ref})


def test_assembly_instance_unchanged_by_adding_a_context(tmp_path):
    from freeshape import links as L
    from freeshape.assembly import AssemblyDoc
    plain = block_doc()
    withctx, _ = studio_with_context(capture()[0])
    own = f"{plain.features[1]['id']}/1"
    assert own == f"{withctx.features[1]['id']}/1"
    res = []
    for name, d in (("a", plain), ("b", withctx)):
        path = str(tmp_path / f"{name}.fshape")
        d.save(path)
        r = L.LinkResolver()
        studio = r.studio_by_uid(None, path, load=True)
        a = AssemblyDoc()
        a.resolver = r.resolve_source
        iid = a.insert({"kind": "studio", "uid": studio.uid, "path": path, "part": own})
        assert not a.errors, a.errors
        res.append((len(a.instances), state_fingerprint_shape(a.parts[iid].shape), np.round(a.T(iid), 9).tolist()))
    assert res[0] == res[1]
